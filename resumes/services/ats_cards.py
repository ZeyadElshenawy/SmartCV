"""Category-1 ATS finding cards (Slice 2) — read-only, real deltas.

A deterministic producer over the breakdown + the gap matched set. Two card
types ship:

  (a) add_skill  — ACTIONABLE: a skill the candidate demonstrably HAS (gap
      matched, real evidence) that the scorer counts as missing from the résumé
      text. Adding it to the skills line raises the ATS score by keyword
      *coverage only* (no in-context bonus — the scorer's real recompute reflects
      that). Carries the exact `edit` Slice 3 will apply.
  (c) stuffing   — ADVISORY only: a skill repeated >4× in prose, costing −5.0.
      No deterministic one-click fix (which occurrences to cut is a judgment),
      so no edit / no delta / no button.

Card (b) (spelling/variant normalize) is intentionally NOT built — a
provenance-gated (b) collapses into (a), and its only unique cases are skills the
gap analyzer declined to mark matched (overclaim risk). Deferred.

The only writes are hypothetical (scored, never persisted). Slice 3 re-runs
build_ats_cards to resolve a card by id and apply its `edit`.
"""
from __future__ import annotations

import hashlib

from jobs.services.skill_extractor import skills_match

from .ats_breakdown import breakdown_for_resume, score_with_edit
from .scoring import STUFFING_PENALTY_PER_SKILL

# Human label for a matched entry's evidence_source (the gap analyzer's values).
_EVIDENCE_SOURCE_LABEL = {
    "skills": "in your skills",
    "experience": "in your work experience",
    "projects": "in your projects",
    "certifications": "in a certification",
    "github": "on your GitHub",
    "scholar": "in your publications",
    "kaggle": "on Kaggle",
    "education": "in your education",
    "multiple": "across your profile",
    "": "in your profile",
}


def _ats_card_id(card_type: str, skill: str) -> str:
    """Deterministic card id — mirrors the findings_ux.py:299 stable-id pattern
    (sha1 of a stable identity tuple, 16 hex chars) so Slice 3's apply can
    address one card across reloads. Case-insensitive on the skill."""
    raw = f"ats|{card_type}|{(skill or '').lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _evidence_backed_matched(gap):
    """The §2 provenance gate — yields ``(name, evidence_source, evidence_quote,
    tier)`` for gap matched entries with REAL evidence only:
      - not user_asserted (drag-drop self-reports; the flag is ABSENT on
        LLM-pipeline rows, so read it with ``.get(..., False)``),
      - a concrete ``evidence_source`` that isn't ``'user'``, and
      - a non-empty ``evidence_quote``.
    Drop-when-unsure: anything weaker is never surfaced as "you have this".

    KNOWN EDGE (accepted, do not "fix"): the no-LLM fallback path
    (analysis.services.gap_analyzer ~:1133) emits ``evidence_source='skills'``
    with a difflib-derived quote and passes this gate. ``GapAnalysis`` does not
    persist ``analysis_method``, so a fallback row can't be detected from the DB.
    Rare and self-healing (a difflib@0.85 match is real signal); intentionally
    not gated rather than adding a schema field for it.
    """
    if gap is None:
        return
    for tier, entries in (("must", gap.matched_must_have or []),
                          ("nice", gap.matched_nice_to_have or [])):
        for e in entries:
            if not isinstance(e, dict) or e.get("user_asserted", False):
                continue
            name = (e.get("name") or "").strip()
            src = (e.get("evidence_source") or "").strip()
            quote = (e.get("evidence_quote") or "").strip()
            if not name or not src or src == "user" or not quote:
                continue
            yield name, src, quote, tier


def build_ats_cards(resume, *, current=None) -> list[dict]:
    """The single deterministic card producer. Reads the breakdown + gap matched
    set; writes nothing. Pass *current* (an already-computed breakdown) to skip a
    redundant recompute."""
    current = current if current is not None else breakdown_for_resume(resume)
    cards: list[dict] = []

    # --- (a) Keyword-you-have → add to skills (actionable, coverage-only) ---
    missed = list(current["must_have"]["missed"]) + list(current["nice_to_have"]["missed"])
    seen: set[str] = set()
    for name, src, quote, tier in _evidence_backed_matched(resume.gap_analysis):
        key = name.lower()
        if key in seen:
            continue
        # Must be MISSING from the résumé text (the scorer's own notion),
        # decided by the variant-aware matcher — never a raw string compare.
        if not any(skills_match(name, m) for m in missed):
            continue
        edit = {"op": "add_skill", "skill": name}
        new_bd, delta = score_with_edit(resume, edit, current=current)
        if delta <= 0.0:                       # zero-delta guard — no "+0.0" noise
            continue
        seen.add(key)
        where = _EVIDENCE_SOURCE_LABEL.get(src, "in your profile")
        cards.append({
            "id": _ats_card_id("add_skill", name),
            "type": "add_skill",
            "kind": "actionable",
            "skill": name,
            "tier": tier,
            "current_score": current["score"],
            "projected_score": new_bd["score"],
            "delta": delta,
            "evidence_source": src,
            "evidence_quote": quote,
            # Says "ATS score" (not "match"); grounds the claim in the real
            # evidence quote; keeps the coverage-only nature explicit. The
            # numbers are rendered by the template from the fields above.
            "message": (
                f'You have {name} — {where}: "{quote}". It isn\'t in your '
                f"résumé text. Adding it to your skills list raises your ATS "
                f"score (keyword coverage only)."
            ),
            "edit": edit,
        })

    # --- (c) Stuffing (advisory only — no edit, no delta, no button) ---
    for skill in current["stuffing"]["skills"]:
        # keyword_counts is the résumé-wide count (matches the Slice-1 stuffing
        # detail line, so the panel shows one count per skill). The penalty
        # triggers on the prose subset; the recoverable points are exact.
        count = current["keyword_counts"].get(skill, 0)
        cards.append({
            "id": _ats_card_id("stuffing", skill),
            "type": "stuffing",
            "kind": "advisory",
            "skill": skill,
            "count": count,
            "recoverable": STUFFING_PENALTY_PER_SKILL,
            "message": (
                f'"{skill}" appears {count}× in your prose. Keyword stuffing '
                f"costs −{STUFFING_PENALTY_PER_SKILL:.1f}. Reducing it toward ≤4 "
                f"occurrences would recover +{STUFFING_PENALTY_PER_SKILL:.1f}."
            ),
        })

    return cards
