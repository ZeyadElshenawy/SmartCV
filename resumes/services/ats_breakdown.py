"""Resume-aware ATS breakdown — the single rescore authority.

`scoring.compute_ats_breakdown` is the pure, deterministic (σ=0), Django-free
scorer: it takes (content, job_skills, tiers) and returns an ``AtsBreakdown``.
This module is the thin layer that knows how to resolve that scoring triple
*from a GeneratedResume* — so the editor panel, the read endpoint, and Slice 2's
candidate-builder all score through one place and can never disagree with the
number on screen.

Nothing here re-implements scoring; it only resolves inputs and delegates.
"""
from __future__ import annotations

from .scoring import compute_ats_breakdown, AtsBreakdown


def breakdown_for_resume(resume, content=None) -> AtsBreakdown:
    """Recompute the ATS breakdown for *resume*.

    Scores *content* when supplied, else ``resume.content``. Resolves the
    scoring triple from ``resume.gap_analysis.job`` (``job`` is a non-null FK,
    so the chain is always present) and delegates to the pure scorer.

    Pass an explicit *content* to score a *hypothetical* résumé with the same
    authority — this is the seam Slice 2's candidate-builder reuses to compute
    a real "+X if fixed" delta. ``content is None`` (not falsiness) selects the
    default, so an explicit ``{}`` is honoured and scores as empty.
    """
    job = resume.gap_analysis.job
    job_tiers = getattr(job, "extracted_skills_tiers", None) or None
    skills = job.extracted_skills or []
    payload = content if content is not None else (resume.content or {})
    return compute_ats_breakdown(payload, skills, job_tiers)


def refresh_ats_score(resume) -> float:
    """Idempotently sync the stored ``resume.ats_score`` to the live content.

    Recomputes from ``resume.content`` and persists **only the ``ats_score``
    column, and only when it actually changed** (compared at 1dp). Never
    touches ``content`` / ``validation_report``. Returns the current score.

    Call this from paths that have just mutated ``resume.content`` (inline
    save, section regen, accept-fix) so the stored float — which other surfaces
    (résumé list, export snapshot) read — stops going stale after edits. The
    read surfaces (panel, GET endpoint) never call this; reads stay pure.
    """
    new_score = breakdown_for_resume(resume)["score"]
    if round(new_score, 1) != round(resume.ats_score or 0.0, 1):
        resume.ats_score = new_score
        resume.save(update_fields=["ats_score"])
    return new_score


def score_reconciles(breakdown) -> bool:
    """True when ``base + in_context_bonus − stuffing_penalty`` equals ``score``
    (within rounding) — i.e. the [0,100] clamp did NOT bind, so the panel may
    show the explicit reconciliation equation honestly.

    False when the clamp bound (the unclamped sum fell below 0 or above 100):
    the equation would not literally reach the displayed ``score``, so the panel
    states the cap/floor instead of asserting a false sum.
    """
    unclamped = (
        breakdown["base"]
        + breakdown["in_context_bonus"]
        - breakdown["stuffing_penalty"]
    )
    return abs(round(unclamped, 1) - breakdown["score"]) <= 0.1
