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

import copy

from jobs.services.skill_extractor import skills_match

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


# ---------------------------------------------------------------------------
# Candidate-builder (Slice 2) — score a HYPOTHETICAL mechanical edit.
# `apply_edit_to_content` is the single content constructor: Slice 2 scores its
# output for a preview delta; Slice 3's apply will call the SAME function then
# persist the result, so the previewed hypothetical is byte-identical to what
# gets saved (previewed == realized). Deltas come only from breakdown_for_resume
# — one rescore authority, no second scoring path, no client-side math.
# ---------------------------------------------------------------------------
def apply_edit_to_content(content: dict, edit: dict) -> dict:
    """PURE. Return a NEW content dict with *edit* applied; never mutate *content*.

    Supported ops:
      - ``add_skill``: append ``edit['skill']`` to ``content['skills']`` (a
        ``List[str]``) ONLY when no existing skill already ``skills_match``-es it
        (idempotent, variant-safe — won't double-add "python" next to "Python").
    Unknown ops return a faithful deep copy unchanged.
    """
    new = copy.deepcopy(content or {})
    op = edit.get("op")
    if op == "add_skill":
        skill = edit["skill"]
        skills = list(new.get("skills") or [])
        if not any(isinstance(s, str) and skills_match(skill, s) for s in skills):
            skills.append(skill)
        new["skills"] = skills
    return new


def score_with_edit(resume, edit, *, current=None):
    """Score a hypothetical mechanical *edit*. Returns ``(new_breakdown, delta)``.

    *delta* is ``round(new_score − current_score, 1)``. Pass *current* (the
    already-computed breakdown) to avoid a redundant recompute; both the
    baseline and the hypothetical are scored through ``breakdown_for_resume`` —
    the one rescore authority. Writes nothing.
    """
    base = current if current is not None else breakdown_for_resume(resume)
    hypo = apply_edit_to_content(resume.content or {}, edit)
    new_bd = breakdown_for_resume(resume, hypo)
    return new_bd, round(new_bd["score"] - base["score"], 1)
