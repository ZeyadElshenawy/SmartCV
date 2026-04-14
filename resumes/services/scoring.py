"""ATS scoring + evidence confidence.

Two scoring functions used across the app:

- compute_ats_breakdown(content, job_skills) → rich dict with score,
  matched_count, total_count, stuffed_skills, and in_context bonus.
  Penalizes keyword stuffing (same word repeated >4 times across the
  resume) and rewards keywords that appear in experience descriptions
  rather than only in the skills list.

- compute_evidence_confidence(profile) → 0–3 confidence rating based on
  how many external signal sources (GitHub / Scholar / Kaggle) are
  connected and non-trivial. Used to decorate the gap-analysis verdict
  with a "this score is well-evidenced" indicator.

The legacy resumes.services.resume_generator.calculate_ats_score wrapper
delegates here so existing callers (tasks.py, views.py) keep working.
"""
from __future__ import annotations

import json
import logging
from typing import TypedDict

logger = logging.getLogger(__name__)

# A keyword appearing more than this many times across the whole resume is
# treated as stuffing and penalized rather than rewarded.
STUFFING_THRESHOLD = 4
STUFFING_PENALTY_PER_SKILL = 5.0  # points knocked off the raw score per stuffed keyword
IN_CONTEXT_BONUS_PER_SKILL = 2.0  # bonus when a keyword shows up in experience text, capped


class AtsBreakdown(TypedDict):
    score: float                # final 0–100 score after bonuses & penalties
    raw_score: float            # matched_count / total_count * 100, before adjustments
    matched_count: int
    total_count: int
    in_context_count: int       # how many keywords appeared in experience descriptions
    in_context_bonus: float     # actual bonus applied (capped)
    stuffed_skills: list[str]   # keywords that appeared > STUFFING_THRESHOLD times
    stuffing_penalty: float     # actual penalty applied
    keyword_counts: dict[str, int]  # per-skill count, useful for debug/UI


class EvidenceConfidence(TypedDict):
    score: int                  # 0–3 stars
    label: str                  # "Strong" / "Moderate" / "Limited" / "Untested"
    sources: list[str]          # ["github", "scholar", "kaggle"] — only those that contributed
    detail: str                 # one-sentence explanation for the UI


def compute_ats_breakdown(resume_content: dict, job_skills: list[str]) -> AtsBreakdown:
    """Score a tailored resume against the job's required skills.

    Algorithm:
    1. Count occurrences of each job_skill across the full resume JSON.
    2. raw_score = matched_count / total_count × 100.
    3. Apply stuffing penalty: for each skill that appears > 4 times,
       knock 5 points off (so a resume jamming 5 of the same keyword 6×
       each loses 25 points).
    4. Apply in-context bonus: for each skill that appears in any
       experience description (not just the skills list), award 2 points,
       capped at +10 total. Encourages using keywords *in evidence*
       rather than as stuffed bullet-list filler.
    5. Clamp to [0, 100], round to one decimal.
    """
    if not job_skills:
        return AtsBreakdown(
            score=0.0, raw_score=0.0, matched_count=0, total_count=0,
            in_context_count=0, in_context_bonus=0.0,
            stuffed_skills=[], stuffing_penalty=0.0, keyword_counts={},
        )

    full_text = json.dumps(resume_content).lower()
    # In-context = appearing in any experience.description bullet (not just the skills list)
    experience_text = ""
    for exp in (resume_content.get('experience') or []):
        if not isinstance(exp, dict):
            continue
        desc = exp.get('description')
        if isinstance(desc, list):
            experience_text += " " + " ".join(str(b) for b in desc)
        elif isinstance(desc, str):
            experience_text += " " + desc
    experience_text = experience_text.lower()

    keyword_counts: dict[str, int] = {}
    matched_count = 0
    in_context_count = 0
    stuffed_skills: list[str] = []

    for skill in job_skills:
        skill_lower = skill.lower().strip()
        if not skill_lower:
            continue
        count = full_text.count(skill_lower)
        keyword_counts[skill] = count
        if count > 0:
            matched_count += 1
            if skill_lower in experience_text:
                in_context_count += 1
        if count > STUFFING_THRESHOLD:
            stuffed_skills.append(skill)
            logger.warning("Keyword stuffing detected: '%s' appears %d times", skill, count)

    raw_score = (matched_count / len(job_skills)) * 100.0

    in_context_bonus = min(in_context_count * IN_CONTEXT_BONUS_PER_SKILL, 10.0)
    stuffing_penalty = len(stuffed_skills) * STUFFING_PENALTY_PER_SKILL

    final = raw_score + in_context_bonus - stuffing_penalty
    final = max(0.0, min(100.0, final))

    return AtsBreakdown(
        score=round(final, 1),
        raw_score=round(raw_score, 1),
        matched_count=matched_count,
        total_count=len(job_skills),
        in_context_count=in_context_count,
        in_context_bonus=round(in_context_bonus, 1),
        stuffed_skills=stuffed_skills,
        stuffing_penalty=round(stuffing_penalty, 1),
        keyword_counts=keyword_counts,
    )


def compute_evidence_confidence(profile) -> EvidenceConfidence:
    """Rate how well the candidate's profile is corroborated by external signals.

    Counts signals that meaningfully exist (non-error, non-trivial):
    - GitHub: at least 1 public repo
    - Scholar: at least 1 publication OR any citations
    - Kaggle:  at least 1 entry in any category

    Returns 0–3 confidence with a label + one-sentence detail.
    """
    data = getattr(profile, 'data_content', None) or {}
    if not isinstance(data, dict):
        data = {}

    sources: list[str] = []

    # GitHub
    gh = data.get('github_signals') or {}
    if isinstance(gh, dict) and not gh.get('error') and (gh.get('public_repos') or 0) > 0:
        sources.append('github')

    # Scholar — needs either a publication or any citations to count as real evidence
    sc = data.get('scholar_signals') or {}
    if isinstance(sc, dict) and not sc.get('error'):
        has_pubs = bool(sc.get('top_publications'))
        has_cites = (sc.get('total_citations') or 0) > 0
        if has_pubs or has_cites:
            sources.append('scholar')

    # Kaggle — any non-zero category counts as engagement
    kg = data.get('kaggle_signals') or {}
    if isinstance(kg, dict) and not kg.get('error'):
        any_activity = any(
            isinstance(kg.get(k), dict) and (kg[k].get('count') or 0) > 0
            for k in ('competitions', 'datasets', 'notebooks', 'discussion')
        )
        if any_activity:
            sources.append('kaggle')

    score = len(sources)
    label = {0: 'Untested', 1: 'Limited', 2: 'Moderate', 3: 'Strong'}.get(score, 'Untested')

    if score == 0:
        detail = "No external signals connected — connect GitHub, Scholar, or Kaggle to corroborate skills."
    else:
        names = ", ".join(s.capitalize() for s in sources)
        detail = f"Backed by {names}."

    return EvidenceConfidence(
        score=score, label=label, sources=sources, detail=detail,
    )


def calculate_ats_score(resume_content: dict, job_skills: list[str]) -> float:
    """Backwards-compat wrapper: returns just the final score float.

    Kept as the canonical API for resumes.tasks and resumes.views (which
    store it on resume.ats_score). New code should prefer
    compute_ats_breakdown() for transparency.
    """
    return compute_ats_breakdown(resume_content, job_skills)['score']
