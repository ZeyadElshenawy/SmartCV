"""Per-skill retrieval over a user's `CandidateEvidence` rows.

Two query modes:

- `retrieve_for_skills(profile, skills, k_per_skill=3)` — for each skill,
  embed the skill name and pull the top-K candidate chunks by cosine.
  Used by the resume orchestrator to build the per-skill evidence map
  the inclusion planner and LLM both consume.
- `retrieve_for_jd(profile, jd_text, k=12)` — single broad sweep over
  the user's chunks for the whole JD. Used to draft the summary
  section (which speaks to the role as a whole rather than per-skill).

Both modes call `refresh_if_stale(profile)` first so the index is always
current. Refresh is a no-op when the profile hash matches the stored
row hash, so the overhead is one DB read on the happy path.
"""
from __future__ import annotations

import logging
from typing import Sequence

from pgvector.django import CosineDistance

from profiles.models import CandidateEvidence
from profiles.services.embeddings import embed_text, embed_texts
from profiles.services.candidate_evidence_indexer import refresh_if_stale

logger = logging.getLogger(__name__)


def retrieve_for_skills(
    profile,
    skills: Sequence[str],
    k_per_skill: int = 3,
) -> dict[str, list[CandidateEvidence]]:
    """Per-skill top-K candidate-evidence retrieval.

    Returns `{skill_name: [CandidateEvidence, ...]}` ordered by cosine
    similarity (closest first). A skill with no rows for the user
    yields an empty list — the inclusion planner uses that as the
    signal "no evidence on file for this skill."
    """
    if not skills:
        return {}
    refresh_if_stale(profile)
    user = profile.user

    # Batch-embed all skill queries in one pass. all-MiniLM-L6-v2 is
    # fast but the per-call overhead is non-trivial; batching here keeps
    # resume generation under ~1s on this stage even for a JD with 30+
    # required skills.
    cleaned = [s.strip() for s in skills if s and isinstance(s, str) and s.strip()]
    cleaned = list(dict.fromkeys(cleaned))  # dedupe, preserve order
    if not cleaned:
        return {}
    embeddings = embed_texts(cleaned)

    out: dict[str, list[CandidateEvidence]] = {}
    for skill, vec in zip(cleaned, embeddings):
        qs = (
            CandidateEvidence.objects
            .filter(user=user)
            .exclude(embedding__isnull=True)
            .order_by(CosineDistance('embedding', vec))[:k_per_skill]
        )
        out[skill] = list(qs)
    return out


def retrieve_for_jd(
    profile,
    jd_text: str,
    k: int = 12,
) -> list[CandidateEvidence]:
    """Single-shot top-K retrieval over the whole JD. Useful for the
    summary draft and any caller that wants the user's most JD-aligned
    chunks regardless of skill bucket."""
    if not jd_text:
        return []
    refresh_if_stale(profile)
    vec = embed_text(jd_text)
    qs = (
        CandidateEvidence.objects
        .filter(user=profile.user)
        .exclude(embedding__isnull=True)
        .order_by(CosineDistance('embedding', vec))[:k]
    )
    return list(qs)


def evidence_for_chunk_ids(
    profile, chunk_ids: Sequence[str],
) -> dict[str, CandidateEvidence]:
    """Bulk-fetch specific chunks by `chunk_id`. Used by the validator
    when it needs to verify that a citation in an LLM-generated bullet
    actually resolves to a real piece of evidence."""
    if not chunk_ids:
        return {}
    rows = CandidateEvidence.objects.filter(
        user=profile.user, chunk_id__in=list(chunk_ids),
    )
    return {row.chunk_id: row for row in rows}
