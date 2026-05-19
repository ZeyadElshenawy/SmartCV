"""Build a per-candidate evidence index for resume tailoring.

For each user we maintain a `CandidateEvidence` table populated from the
parts of their profile that constitute *real* evidence — CV bullets,
project descriptions, README excerpts, certifications, volunteer entries,
publications, and any free-form summary text. Each row is a single chunk
with a 384-dim embedding, queryable by JD-required skill at resume time.

The pipeline:

1. `compute_evidence_hash(profile)` — sha256 of the serialized subset of
   `data_content` we index. Stable + cheap.
2. `build_chunks(profile)` — pure function: walks every section, emits
   chunk dicts. No LLM, no embedding (the indexer batches embedding
   separately so we don't pay for embeddings when nothing changed).
3. `refresh_if_stale(profile)` — entry point. Compares the freshly
   computed hash against the hash on the user's existing rows. On
   mismatch, deletes the user's rows and re-embeds in one batch call.
   Returns the number of chunks indexed.

Idempotent: re-running with an unchanged profile is a no-op.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Iterable

from django.db import transaction

from profiles.models import CandidateEvidence
from profiles.services.embeddings import embed_texts

logger = logging.getLogger(__name__)

# Sections of `data_content` whose contents we hash + index. Keeping this
# list tight is what makes `compute_evidence_hash` cheap and meaningful —
# changes to onboarding flags or signal-cache timestamps must NOT bust
# the index.
_INDEXED_KEYS = (
    'skills', 'experiences', 'education', 'projects', 'certifications',
    'volunteer_experience', 'publications', 'awards',
    'normalized_summary', 'objective',
)
# GitHub fields that matter for evidence (per-repo READMEs + the profile
# README). Linkedin/Scholar/Kaggle fields are NOT chunk sources today —
# they were already merged into the master-profile sections by
# signal_merger; indexing them again would double-count. Revisit if we
# stop merging.
_GITHUB_KEYS = ('profile_readme', 'top_repos')

# Core technology vocabulary used as a cheap mention scan for `skill_tags`.
# Not authoritative — the embedding does the real semantic match — but
# gives the retriever a hard-keyword fallback for tie-breaking.
_CORE_VOCAB = {
    'python', 'java', 'javascript', 'typescript', 'go', 'rust', 'c++', 'c#',
    'sql', 'postgresql', 'mysql', 'mongodb', 'redis', 'cassandra', 'nosql',
    'react', 'vue', 'angular', 'next.js', 'django', 'flask', 'fastapi',
    'spring', 'rails', 'node.js', 'express',
    'aws', 'azure', 'gcp', 'kubernetes', 'k8s', 'docker', 'terraform',
    'ansible', 'jenkins', 'github actions', 'ci/cd',
    'tensorflow', 'pytorch', 'keras', 'scikit-learn', 'sklearn', 'pandas',
    'numpy', 'scipy', 'spark', 'pyspark', 'hadoop', 'kafka', 'airflow',
    'machine learning', 'deep learning', 'nlp', 'computer vision', 'cnn',
    'rnn', 'lstm', 'transformer', 'llm', 'rag', 'embedding', 'vector',
    'data analysis', 'data visualization', 'power bi', 'tableau',
    'sap', 'erp', 'salesforce', 'jira', 'figma',
}


def compute_evidence_hash(profile) -> str:
    """Stable hash over the indexed slice of `data_content`."""
    data = profile.data_content or {}
    sliced = {key: data.get(key) for key in _INDEXED_KEYS}
    gh = (data.get('github_signals') or {})
    if isinstance(gh, dict):
        # Per-repo READMEs live inside top_repos[*].readme_excerpt; the
        # whole top_repos list is included so adding/removing a repo
        # busts the hash, but the volatile bits we don't care about
        # (stargazer_count etc.) get hashed too — acceptable price for
        # simplicity. fetched_at is excluded to avoid spurious busts.
        sliced['github'] = {key: gh.get(key) for key in _GITHUB_KEYS}
    blob = json.dumps(sliced, sort_keys=True, default=str).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


def _scan_skill_tags(text: str, profile_skill_names: set[str]) -> list[str]:
    """Cheap keyword scan for skill mentions inside `text`."""
    if not text:
        return []
    low = text.lower()
    hits: list[str] = []
    seen: set[str] = set()
    # Profile-specific skills first (more discriminating).
    for skill in profile_skill_names:
        s = skill.lower().strip()
        if not s or s in seen:
            continue
        if re.search(rf"\b{re.escape(s)}\b", low):
            hits.append(s)
            seen.add(s)
    # Then the core vocab.
    for term in _CORE_VOCAB:
        if term in seen:
            continue
        if re.search(rf"\b{re.escape(term)}\b", low):
            hits.append(term)
            seen.add(term)
    return hits


def _profile_skill_names(data: dict) -> set[str]:
    out: set[str] = set()
    for entry in (data.get('skills') or []):
        if isinstance(entry, dict):
            name = (entry.get('name') or '').strip()
        else:
            name = str(entry or '').strip()
        if name:
            out.add(name)
    return out


def _flatten(value: Any) -> str:
    """Bullet-list-or-string → single string. Used because the CV parser
    sometimes lands `description` as a list and sometimes as a paragraph."""
    if value is None:
        return ''
    if isinstance(value, list):
        return '\n'.join(_flatten(v) for v in value if v)
    if isinstance(value, dict):
        # ItemDetailed-shaped (volunteer/publication/award) entries —
        # synthesize a sentence-ish form.
        title = (value.get('title') or value.get('name') or value.get('role') or '').strip()
        org = (value.get('organization') or value.get('institution') or value.get('issuer') or '').strip()
        date = (value.get('date') or value.get('duration') or '').strip()
        desc = _flatten(value.get('description'))
        parts = [p for p in (title, org, date, desc) if p]
        return ' — '.join(parts)
    return str(value).strip()


def build_chunks(profile) -> list[dict]:
    """Walk the profile and emit chunk dicts.

    Returns dicts with keys: chunk_id, source_type, source_id, text,
    skill_tags. Caller embeds + persists. No LLM, no DB writes here.
    """
    data = profile.data_content or {}
    skill_names = _profile_skill_names(data)
    chunks: list[dict] = []

    def emit(chunk_id: str, source_type: str, source_id: str, text: str):
        text = (text or '').strip()
        # Skip tiny fragments — the embedding signal is too noisy.
        if len(text) < 12:
            return
        chunks.append({
            'chunk_id': chunk_id,
            'source_type': source_type,
            'source_id': source_id,
            'text': text,
            'skill_tags': _scan_skill_tags(text, skill_names),
        })

    # --- Experience: one chunk per bullet, plus a "role context" chunk
    # carrying title + company so a query like "senior data scientist"
    # has something to bind to even if no bullet mentions the title. ---
    for i, exp in enumerate(data.get('experiences') or []):
        if not isinstance(exp, dict):
            continue
        title = (exp.get('title') or exp.get('role') or '').strip()
        company = (exp.get('company') or '').strip()
        duration = (exp.get('duration') or '').strip()
        # Role-context chunk.
        bits = [b for b in (title, 'at ' + company if company else '', duration) if b]
        if bits:
            emit(f'experience:{i}:context', 'experience', f'experience:{i}', ' '.join(bits))
        # PR 3b: description is the single canonical bullets field on
        # the profile-side Experience schema. Pre-3b this loop iterated
        # `highlights` separately (with a context-string `desc` from
        # `description`). Now both flow through one list.
        for j, bullet in enumerate(exp.get('description') or []):
            text = _flatten(bullet)
            if not text:
                continue
            emit(f'experience:{i}:bullet:{j}', 'experience', f'experience:{i}', text)

    # --- Projects: one chunk per bullet + a context chunk per project ---
    for i, proj in enumerate(data.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        name = (proj.get('name') or proj.get('title') or '').strip()
        url = (proj.get('url') or '').strip()
        tech = proj.get('technologies') or []
        tech_str = ', '.join(t for t in tech if t)
        context_bits = [b for b in (name, f'tech: {tech_str}' if tech_str else '', url) if b]
        if context_bits:
            emit(
                f'project:{i}:context', 'project', f'project:{i}',
                ' — '.join(context_bits),
            )
        # PR 3b closed the loop: profile-side and resume-side both use
        # `description` as canonical now (pre-3b this iterated both
        # `description` and `highlights`; the dual-shape comment lived
        # right here).
        for j, bullet in enumerate(proj.get('description') or []):
            text = _flatten(bullet)
            if not text:
                continue
            emit(f'project:{i}:bullet:{j}', 'project', f'project:{i}', text)

    # --- GitHub READMEs (profile + per-repo) ---
    gh = data.get('github_signals') or {}
    if isinstance(gh, dict):
        prof_rm = gh.get('profile_readme') or {}
        prof_content = prof_rm.get('content') if isinstance(prof_rm, dict) else ''
        if prof_content:
            emit(
                'readme:profile', 'readme', gh.get('username') or 'profile',
                _flatten(prof_content),
            )
        for repo in (gh.get('top_repos') or []):
            if not isinstance(repo, dict):
                continue
            full = repo.get('full_name') or repo.get('name') or ''
            excerpt = repo.get('readme_excerpt') or ''
            if not (full and excerpt):
                continue
            # Add the repo description as a header so the chunk has a
            # title even when the README opens with a code block.
            desc = (repo.get('description') or '').strip()
            blob = f"{full} — {desc}\n\n{excerpt}" if desc else f"{full}\n\n{excerpt}"
            emit(f'readme:{full}', 'readme', full, blob)

    # --- Certifications ---
    for i, cert in enumerate(data.get('certifications') or []):
        if not isinstance(cert, dict):
            text = _flatten(cert)
            if text:
                emit(f'cert:{i}', 'cert', text[:80], text)
            continue
        name = (cert.get('name') or '').strip()
        issuer = (cert.get('issuer') or '').strip()
        date = (cert.get('date') or '').strip()
        bits = [b for b in (name, f'by {issuer}' if issuer else '', date) if b]
        if bits:
            emit(f'cert:{i}', 'cert', name, ' — '.join(bits))

    # --- Volunteer / publications / awards (ItemDetailed-shaped lists) ---
    for source_type, key in (
        ('volunteer', 'volunteer_experience'),
        ('publication', 'publications'),
        ('award', 'awards'),
    ):
        for i, item in enumerate(data.get(key) or []):
            text = _flatten(item)
            if not text:
                continue
            sid = ''
            if isinstance(item, dict):
                sid = (
                    item.get('organization') or item.get('title')
                    or item.get('name') or ''
                )
            emit(f'{source_type}:{i}', source_type, sid, text)

    # --- Free-form summary / objective ---
    summary = (data.get('normalized_summary') or '').strip()
    if summary:
        emit('summary', 'summary', '', summary)
    objective = (data.get('objective') or '').strip()
    if objective:
        emit('objective', 'objective', '', objective)

    return chunks


def refresh_if_stale(profile) -> int:
    """Rebuild the user's CandidateEvidence rows iff the content hash
    differs from what's stored. Returns the row count after the call.

    Cheap when nothing changed: one COUNT + one hash compare, no embed
    calls, no writes. Expensive only on the first call for a profile and
    after any real change to indexed sections.
    """
    user = profile.user
    new_hash = compute_evidence_hash(profile)
    # Read one row to learn the current hash. unique_together on
    # (user, chunk_id) means rows-for-a-user share the same hash by
    # construction (we wipe on rebuild), so any row is representative.
    existing_hash = (
        CandidateEvidence.objects
        .filter(user=user)
        .values_list('content_hash', flat=True)
        .first()
    )
    if existing_hash == new_hash:
        return CandidateEvidence.objects.filter(user=user).count()

    chunks = build_chunks(profile)
    if not chunks:
        # Profile is empty — delete any stale rows and exit. Avoid an
        # empty embed_texts() call.
        CandidateEvidence.objects.filter(user=user).delete()
        return 0

    embeddings = embed_texts([c['text'] for c in chunks])

    with transaction.atomic():
        CandidateEvidence.objects.filter(user=user).delete()
        CandidateEvidence.objects.bulk_create([
            CandidateEvidence(
                user=user,
                chunk_id=c['chunk_id'],
                source_type=c['source_type'],
                source_id=c['source_id'],
                text=c['text'],
                skill_tags=c['skill_tags'],
                embedding=emb,
                content_hash=new_hash,
            )
            for c, emb in zip(chunks, embeddings)
        ])

    logger.info(
        "candidate_evidence_indexer: rebuilt %d chunks for user=%s hash=%s",
        len(chunks), user.pk, new_hash[:8],
    )
    return len(chunks)
