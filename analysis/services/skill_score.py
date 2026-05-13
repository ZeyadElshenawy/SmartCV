"""Tier-aware, proximity-weighted similarity score for gap analysis.

The frontend Alpine component (templates/analysis/gap_analysis.html) mirrors
this formula in JS — if you change the math here, keep the JS in sync or
the displayed % will drift from what the server persists.

Formula (must_weight=0.75, nice_weight=0.20, base=0.05):

    must_credit = len(matched_must) + sum(0.5 * m.proximity for m in missing_must)
    nice_credit = len(matched_nice) + sum(0.5 * m.proximity for m in missing_nice)

    must_ratio  = must_credit / (len(matched_must) + len(missing_must))
    nice_ratio  = nice_credit / (len(matched_nice) + len(missing_nice))

    score = base + must_weight * must_ratio + nice_weight * nice_ratio

If a tier has zero JD-required skills, its ratio is 1.0 (treated as "fully
satisfied for an empty contract"). This avoids penalizing a candidate when
the JD has no nice-to-have section.

The 0.5 multiplier caps partial credit: even a 0.8 proximity missing skill
only contributes 0.4 of a match. Proximity tells you how *close* the
candidate is, but they don't actually have the skill yet — the score
should reflect that.

Band thresholds (returned by match_band):
    >= 0.85  strong
    >= 0.70  solid
    >= 0.55  partial
    <  0.55  weak
"""
from __future__ import annotations

from typing import Any, Iterable

BASE = 0.05
MUST_WEIGHT = 0.75
NICE_WEIGHT = 0.20
PROXIMITY_CREDIT_CAP = 0.5


def _proximity_of(item: Any) -> float:
    """Pull a proximity value out of a missing-skill object/dict.

    Accepts Pydantic MissingSkill instances, plain dicts, or string-only
    legacy entries (treated as proximity 0.0). Clamps to [0, 1)."""
    if item is None:
        return 0.0
    if hasattr(item, "proximity"):
        p = item.proximity
    elif isinstance(item, dict):
        p = item.get("proximity", 0.0)
    else:
        p = 0.0
    try:
        p = float(p)
    except (TypeError, ValueError):
        p = 0.0
    # Strict upper bound: proximity 1.0 belongs in matched_, not missing_.
    # Clamp defensively so a bad LLM emission doesn't poison the score.
    if p < 0.0:
        return 0.0
    if p >= 1.0:
        return 0.99
    return p


def _missing_credit(missing: Iterable[Any]) -> float:
    """Sum of (proximity * 0.5) across a missing list. Empty list → 0."""
    return sum(PROXIMITY_CREDIT_CAP * _proximity_of(m) for m in (missing or []))


def compute_match_score(
    matched_must: list,
    missing_must: list,
    matched_nice: list,
    missing_nice: list,
) -> float:
    """Tier-aware similarity score on [0, 1].

    See module docstring for the full formula. Inputs are lists; only their
    lengths matter for matched_*, and missing_* items must expose a
    `proximity` attribute or `'proximity'` dict key (strings count as 0.0).
    """
    n_matched_must = len(matched_must or [])
    n_missing_must = len(missing_must or [])
    n_matched_nice = len(matched_nice or [])
    n_missing_nice = len(missing_nice or [])

    total_must = n_matched_must + n_missing_must
    total_nice = n_matched_nice + n_missing_nice

    if total_must:
        must_credit = n_matched_must + _missing_credit(missing_must)
        must_ratio = must_credit / total_must
    else:
        # No JD must-haves declared — treat as fully satisfied for that tier
        # rather than 0/0 division. Edge case for tiny JDs.
        must_ratio = 1.0

    if total_nice:
        nice_credit = n_matched_nice + _missing_credit(missing_nice)
        nice_ratio = nice_credit / total_nice
    else:
        nice_ratio = 1.0

    score = BASE + MUST_WEIGHT * must_ratio + NICE_WEIGHT * nice_ratio
    # Clamp [0, 1] just in case (base + sum of weights = 1.0 exactly when both
    # ratios = 1.0 → score = 1.0, so no rounding overflow in practice).
    return round(max(0.0, min(1.0, score)), 4)


def match_band(score: float) -> str:
    """Bucket the score into a human label.

    Thresholds match the JS scoreTier helper in gap_analysis.html.
    """
    if score >= 0.85:
        return "strong"
    if score >= 0.70:
        return "solid"
    if score >= 0.55:
        return "partial"
    return "weak"


def avg_proximity(missing_must: Iterable[Any], missing_nice: Iterable[Any]) -> float | None:
    """Mean proximity across both missing tiers.

    Returns None when there are no missing skills (no signal to report).
    """
    proximities = [
        _proximity_of(m) for m in (missing_must or [])
    ] + [
        _proximity_of(m) for m in (missing_nice or [])
    ]
    if not proximities:
        return None
    return round(sum(proximities) / len(proximities), 4)
