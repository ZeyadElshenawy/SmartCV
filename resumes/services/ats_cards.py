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
import re

from django.conf import settings

from jobs.services.skill_extractor import skills_match

from .ats_breakdown import breakdown_for_resume, score_with_edit
from .bullet_validator import _has_quantification, _LEN_MIN
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

    # --- (d) Quantify (Category-2) — ask the user for a REAL number; never
    #     suggest, generate, estimate, or score one. Per-bullet + deterministic:
    #     an achievement-shaped bullet with NO number (the validator's own
    #     _has_quantification, one source of truth) in an experience/project
    #     entry. No delta, no projected_score, no edit, no number field — the
    #     card asks; the user answers verbatim or declines.
    content = resume.content or {}
    grounds_on_regen = (getattr(settings, "RESUME_GENERATOR_PIPELINE", "v1") == "v2")
    for section in ("experience", "projects"):
        for item_idx, item in enumerate(content.get(section) or []):
            if not isinstance(item, dict):
                continue
            for bullet_idx, bullet in enumerate(item.get("description") or []):
                if not isinstance(bullet, str):
                    continue
                b = bullet.strip()
                # Skip bullets that already carry a number, fragments, and
                # non-achievement lines. "Achievement-shaped" is approximated
                # deterministically as ≥ _LEN_MIN chars with an alphabetic start.
                if not b or _has_quantification(b) or len(b) < _LEN_MIN or not b[0].isalpha():
                    continue
                addr = f"{section}:{item_idx}:{bullet_idx}"
                cards.append({
                    "id": _ats_card_id("quantify", addr),
                    "type": "quantify",
                    "kind": "quantify",
                    "section": section,
                    "item_idx": item_idx,
                    "bullet_idx": bullet_idx,
                    "bullet_text": b,
                    # No delta / projected_score / edit / number / suggestion.
                    "message": (
                        "This describes work but has no number. If you have a "
                        "real figure, add it in your own words — SmartCV never "
                        "guesses one for you."
                    ),
                    "save_note": "This adds it to your profile so future résumés can use it.",
                    "grounds_on_regen": grounds_on_regen,
                })

    return cards


# ---------------------------------------------------------------------------
# Category-2 fact-first write (Slice 4). The user's verbatim figure is appended
# to the matched PROFILE experience/project — NOT the résumé — because
# extract_from_structured_profile reads the profile (UserProfile.data_content),
# and that's the only path by which a number becomes a grounded ACHIEVEMENT fact
# number-lock permits. CONFIDENT entity match required: ambiguous/none → drop
# (the user's master profile is at stake). Never transforms the text; no LLM.
# ---------------------------------------------------------------------------
def _norm_entity(s) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _match_profile_entry(data_content: dict, section: str, resume_item: dict):
    """Return ``(profile_key, index)`` of the SINGLE profile entry that matches
    the résumé item by entity, or ``None`` when zero or ambiguous."""
    if not isinstance(data_content, dict) or not isinstance(resume_item, dict):
        return None
    if section == "experience":
        rt = _norm_entity(resume_item.get("title"))
        rc = _norm_entity(resume_item.get("company"))
        if not rt:
            return None
        hits = []
        for i, e in enumerate(data_content.get("experiences") or []):
            if not isinstance(e, dict):
                continue
            if _norm_entity(e.get("title")) != rt:
                continue
            ec = _norm_entity(e.get("company"))
            if rc and ec and rc != ec:        # title matches but companies conflict
                continue
            hits.append(("experiences", i))
        return hits[0] if len(hits) == 1 else None
    if section == "projects":
        rn = _norm_entity(resume_item.get("name"))
        if not rn:
            return None
        hits = []
        for key in ("projects_enriched", "projects"):
            for i, p in enumerate(data_content.get(key) or []):
                if isinstance(p, dict) and _norm_entity(p.get("name")) == rn:
                    hits.append((key, i))
        return hits[0] if len(hits) == 1 else None
    return None


def save_quantification_to_profile(profile, resume_content, *, section, item_idx, bullet_idx, text) -> str:
    """Append the user's VERBATIM quantified achievement to the matched profile
    entry. Returns a status string:
      ``'saved'``       — appended + profile saved
      ``'duplicate'``   — already present (idempotent no-op)
      ``'no_match'``    — no confident entity match → NOT written
      ``'bad_address'`` — the résumé bullet address is invalid → NOT written
      ``'empty'``       — blank text → NOT written
    Stores the string exactly as typed (no rounding, no recomposition, no LLM,
    no splicing a digit into a sentence) and never writes the résumé.
    """
    clean = (text or "").strip()
    if not clean:
        return "empty"
    items = (resume_content or {}).get(section) or []
    if not (isinstance(item_idx, int) and 0 <= item_idx < len(items)):
        return "bad_address"
    resume_item = items[item_idx]
    desc = resume_item.get("description") or []
    if not (isinstance(bullet_idx, int) and 0 <= bullet_idx < len(desc)):
        return "bad_address"

    data_content = profile.data_content or {}
    match = _match_profile_entry(data_content, section, resume_item)
    if match is None:
        return "no_match"
    key, idx = match
    entry = dict(data_content[key][idx])
    entry_desc = list(entry.get("description") or [])
    if any(isinstance(d, str) and d.strip() == clean for d in entry_desc):
        return "duplicate"
    entry_desc.append(clean)            # VERBATIM — the user's words, the user's number
    entry["description"] = entry_desc
    data_content[key][idx] = entry
    profile.data_content = data_content
    profile.save(update_fields=["data_content"])
    return "saved"
