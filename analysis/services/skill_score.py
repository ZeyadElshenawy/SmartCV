"""Recompute a gap-analysis similarity score from the three skill bucket sizes.

Called by the `update_gap_skills` endpoint after the user drags skills between
columns, so `GapAnalysis.similarity_score` stays consistent with what the
Alpine frontend just displayed. Also called directly by the gap_analysis
template via Alpine — the JS formula in templates/analysis/gap_analysis.html
must stay a mirror of this one.

Formula:
    score = (matched + 0.5 * soft) / (matched + missing + soft)

- matched  = 1.0 weight (skill is in CV and in job)
- soft     = 0.5 weight (negotiable gap — recruiter-facing skills the user
             could pick up easily or reframe from adjacent experience)
- missing  = 0.0 weight (critical gap)

Edge case: empty buckets return 0.0. Callers that want to preserve an existing
LLM-computed score in that case should check for zero counts before calling.
"""
from __future__ import annotations


def compute_match_score(
    matched_count: int,
    missing_count: int,
    soft_count: int,
) -> float:
    """Return a 0..1 similarity score from bucket sizes.

    See module docstring for the formula.
    """
    total = matched_count + missing_count + soft_count
    if total == 0:
        return 0.0
    return round(
        (matched_count + 0.5 * soft_count) / total,
        4,
    )
