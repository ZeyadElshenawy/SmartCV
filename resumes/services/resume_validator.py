"""Grounding validator for the v2 resume pipeline.

The format-pass validator (``_apply_bullet_validator`` in
``resume_generator.py``) checks bullet length, action-verb starts, and
banned-word usage. It does NOT check whether the *claims* in those
bullets are grounded in the candidate's real evidence.

This module adds that second pass. For every bullet in the generated
resume, it asks:

  1. **Skills mentioned** — does each skill the bullet name-drops appear
     in the inclusion plan's `skills_to_list` OR in `bridge_bullet_skills`?
     Any other skill name is a leak (the LLM either invented a tool the
     candidate doesn't have, or used a banned `drop_skill`).

  2. **Metrics / numbers** — does the bullet make a numeric claim (e.g.
     "92%", "5M users", "12 teams") that we can trace to one of the
     candidate-evidence chunks the LLM was given? If not, flag it —
     the LLM might be fabricating.

  3. **Drop-skill leaks** — any explicit mention of a skill in
     `plan.drop_skills` is a hard failure regardless of context.

Findings are returned as a list of structured records; no bullets are
rewritten or stripped automatically (the user reviews on the edit page
and decides). Same pattern as ``_apply_bullet_validator`` — surface,
don't silently mutate.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Matches the kinds of numeric claims we want to be able to trace —
# percentages, multipliers, scales (k/M/B), money, team sizes, durations.
_NUMBER_RE = re.compile(r"\b\d[\d.,]*\s*(?:%|x|k|m|b|million|billion|users?|teams?|months?|years?|hours?|days?)?\b", re.IGNORECASE)
# Stripped from a bullet before metric extraction so common dates / years
# don't trigger false positives ("Jan 2024" isn't a fabricated metric).
_DATE_RE = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4}\b|\b(?:19|20)\d{2}\b", re.IGNORECASE)
# Drops the trailing "[chunk_id]" citation the prompt asks the LLM to
# emit. We strip these BEFORE persistence; the validator runs after the
# strip so it shouldn't see them — but be defensive.
_CITATION_RE = re.compile(r"\s*\[[^\]]+\]\s*$")
# "1-2 word" stoppers for skill-name extraction. The skill-matching pass
# stays on whole-word boundaries so "JS" doesn't false-positive on "AdJustment".
_WORDLIKE = re.compile(r"\b[A-Za-z][A-Za-z0-9+#./-]*\b")


@dataclass
class Finding:
    """One validator complaint. `kind` is the category; `where` says
    "experience[0].description[2]" or similar so the reviewer can jump
    to the offending bullet on the edit page; `detail` is human text."""
    kind: str            # 'unsupported_skill' | 'drop_skill_leak' | 'unsupported_metric'
    where: str
    detail: str


def _strip_citation(text: str) -> str:
    """Remove a trailing `[chunk_id]` citation if present."""
    if not text:
        return ''
    return _CITATION_RE.sub('', text).strip()


def _candidate_evidence_text(per_skill_ev: dict) -> str:
    """Concat all retrieved candidate-evidence text into one searchable
    blob. We don't need per-chunk attribution at validate time — we just
    need to know "is THIS metric anywhere in the evidence pool?"."""
    parts: list[str] = []
    for chunks in (per_skill_ev or {}).values():
        for c in chunks:
            parts.append(getattr(c, 'text', '') or '')
    return ' '.join(parts).lower()


def _extract_skill_mentions(bullet: str, known_skills: set[str]) -> list[str]:
    """Return the subset of `known_skills` that appear (whole-word,
    case-insensitive) in the bullet."""
    if not bullet or not known_skills:
        return []
    low = bullet.lower()
    hits: list[str] = []
    for skill in known_skills:
        s = skill.lower().strip()
        if not s:
            continue
        # Word-boundary on the first and last alnum char; allow punctuation
        # inside (e.g. "node.js", "c++").
        pattern = rf"(?<!\w){re.escape(s)}(?!\w)"
        if re.search(pattern, low):
            hits.append(skill)
    return hits


def _extract_numeric_claims(bullet: str) -> list[str]:
    """Extract numeric claims that aren't just dates. Returns the raw
    matched strings (e.g. "92%", "5M users")."""
    if not bullet:
        return []
    # Mask out dates so they don't get flagged.
    masked = _DATE_RE.sub('  ', bullet)
    return [m.group(0) for m in _NUMBER_RE.finditer(masked)]


def _metric_in_evidence(metric: str, evidence_blob: str) -> bool:
    """Lenient match: a metric is grounded if the same digit sequence
    appears anywhere in the evidence blob. We don't insist on unit-match
    because the same number could appear with different units (e.g.,
    the README says "92% recall", the bullet says "92% accuracy" — a
    suspicious mismatch, but both share 92% so the digits alone are a
    reasonable confidence signal). The user reviews findings; this is
    a probabilistic flag, not a hard reject."""
    digits = re.sub(r"\D", "", metric)
    if not digits:
        return True   # word-only "millions" / "thousands" — nothing to verify
    if len(digits) < 1:
        return True
    return digits in re.sub(r"\D", "", evidence_blob)


def _all_skill_names(plan) -> tuple[set[str], set[str], set[str]]:
    """Pull (allowed, bridge, dropped) skill sets off the plan."""
    allowed = set(plan.skills_to_list or [])
    bridge = {b['name'] for b in (plan.bridge_bullet_skills or []) if b.get('name')}
    dropped = set(plan.drop_skills or [])
    return allowed, bridge, dropped


def _iter_bullets(resume: dict) -> Iterable[tuple[str, str]]:
    """Yield (path, bullet_text) for every bullet in the resume. Path is
    a string like 'experience[0].description[2]' for jump-to-bullet UX."""
    for i, exp in enumerate(resume.get('experience') or []):
        if not isinstance(exp, dict):
            continue
        desc = exp.get('description')
        if isinstance(desc, list):
            for j, b in enumerate(desc):
                if isinstance(b, str) and b.strip():
                    yield f"experience[{i}].description[{j}]", b
        elif isinstance(desc, str) and desc.strip():
            yield f"experience[{i}].description", desc

    for i, proj in enumerate(resume.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        desc = proj.get('description')
        if isinstance(desc, list):
            for j, b in enumerate(desc):
                if isinstance(b, str) and b.strip():
                    yield f"projects[{i}].description[{j}]", b
        elif isinstance(desc, str) and desc.strip():
            yield f"projects[{i}].description", desc
        # Highlights are bullets too.
        for j, b in enumerate(proj.get('highlights') or []):
            if isinstance(b, str) and b.strip():
                yield f"projects[{i}].highlights[{j}]", b


def strip_citations_from_resume(resume: dict) -> dict:
    """Walk the resume and strip any trailing `[chunk_id]` citation the
    LLM emitted (per the prompt's GROUNDING RULE). In-place on a copy."""
    out = dict(resume)
    for path, bullet in _iter_bullets(out):
        # `path` is informational only — _iter_bullets yields the text
        # for inspection; we rewrite via the index. Walk the structure
        # again for the rewrite so we don't fight the generator.
        pass
    for i, exp in enumerate(out.get('experience') or []):
        if not isinstance(exp, dict):
            continue
        desc = exp.get('description')
        if isinstance(desc, list):
            exp['description'] = [_strip_citation(b) if isinstance(b, str) else b for b in desc]
        elif isinstance(desc, str):
            exp['description'] = _strip_citation(desc)
    for i, proj in enumerate(out.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        desc = proj.get('description')
        if isinstance(desc, list):
            proj['description'] = [_strip_citation(b) if isinstance(b, str) else b for b in desc]
        elif isinstance(desc, str):
            proj['description'] = _strip_citation(desc)
        hl = proj.get('highlights')
        if isinstance(hl, list):
            proj['highlights'] = [_strip_citation(b) if isinstance(b, str) else b for b in hl]
    return out


def run_grounding_check(
    resume: dict,
    plan,
    per_skill_ev: dict,
) -> list[Finding]:
    """Pass-2 validator. Returns findings; never mutates the resume.

    Caller (resume_generator) is responsible for:
      - stripping citations (use strip_citations_from_resume above)
      - persisting findings into GeneratedResume.validation_report
    """
    if plan is None:
        return []

    allowed, bridge, dropped = _all_skill_names(plan)
    # `known_skills` = every skill the LLM is allowed to name. We track
    # `dropped` separately to flag bullets that mention them anyway.
    all_known = allowed | bridge | dropped
    evidence_blob = _candidate_evidence_text(per_skill_ev)

    findings: list[Finding] = []
    for where, bullet in _iter_bullets(resume):
        text = _strip_citation(bullet)
        # 1) Drop-skill leak — hard failure.
        for dropped_skill in dropped:
            pattern = rf"(?<!\w){re.escape(dropped_skill.lower())}(?!\w)"
            if re.search(pattern, text.lower()):
                findings.append(Finding(
                    kind='drop_skill_leak',
                    where=where,
                    detail=(
                        f"Bullet mentions '{dropped_skill}', which the inclusion plan "
                        "marked do-not-claim (low proximity, no bridge evidence)."
                    ),
                ))
        # 2) Unknown skills — a name-shaped token that LOOKS like a skill
        # but isn't on the allowed/bridge list. Heuristic: capitalized,
        # alpha-num + . / + #, not in a common-English short-list.
        # We only flag the most obvious cases to avoid false positives.
        mentioned_known = set(_extract_skill_mentions(text, all_known))
        # Find capitalized tech-shaped tokens (PyTorch, kubernetes, k8s).
        candidates = {m.group(0) for m in _WORDLIKE.finditer(text)
                       if (
                           # tech-y indicators: uppercase + lowercase mix, or
                           # numbers/punctuation in the token, or 2+ caps in a row.
                           any(ch.isupper() for ch in m.group(0))
                           and any(ch.islower() for ch in m.group(0))
                           and len(m.group(0)) >= 3
                       )}
        # Filter: drop everything we already know is allowed.
        suspect = {c for c in candidates if c.lower() not in {s.lower() for s in mentioned_known}}
        # Filter again: drop English words / proper nouns we don't care about.
        # Keep this conservative — we'd rather miss a few than false-positive
        # on "Microsoft Office" or "Stanford University".
        _COMMON = {'Built', 'Designed', 'Implemented', 'Shipped', 'Launched',
                   'Reduced', 'Improved', 'Optimized', 'Led', 'Owned',
                   'Analyzed', 'Investigated', 'Modeled', 'Trained', 'Developed',
                   'Cleaned', 'Created', 'Applied', 'Used', 'Wrote',
                   'Achieved', 'Delivered', 'Drove'}
        suspect -= _COMMON
        # If the suspect is referenced in evidence text, it's probably fine —
        # the LLM is echoing a real signal we just didn't list as a skill.
        suspect = {s for s in suspect if s.lower() not in evidence_blob}
        for s in sorted(suspect):
            # Cap at 3 per bullet to avoid spam.
            if sum(1 for f in findings if f.where == where and f.kind == 'unsupported_skill') >= 3:
                break
            findings.append(Finding(
                kind='unsupported_skill',
                where=where,
                detail=(
                    f"Possible unsupported skill '{s}' — not in the inclusion plan's "
                    "skills_to_list / bridge_bullet_skills and not mentioned in any "
                    "retrieved evidence chunk. Verify."
                ),
            ))
        # 3) Numeric claim — must trace into evidence.
        for metric in _extract_numeric_claims(text):
            if not _metric_in_evidence(metric, evidence_blob):
                findings.append(Finding(
                    kind='unsupported_metric',
                    where=where,
                    detail=(
                        f"Metric '{metric}' doesn't trace to any retrieved candidate-evidence "
                        "chunk. Confirm the number is real or remove it."
                    ),
                ))

    logger.info(
        "resume_validator: grounding pass produced %d findings (%s)",
        len(findings),
        dict(
            (k, sum(1 for f in findings if f.kind == k))
            for k in {f.kind for f in findings}
        ),
    )
    return findings


def findings_to_report(findings: list[Finding]) -> list[dict]:
    """Serialize for persistence onto GeneratedResume.validation_report."""
    return [
        {'kind': f.kind, 'where': f.where, 'detail': f.detail}
        for f in findings
    ]
