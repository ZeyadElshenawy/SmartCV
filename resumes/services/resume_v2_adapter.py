"""Adapter: ``GeneratedResumeV2`` → v1 template-dict shape.

CONTRACT (post-rewrite — stops being a source-passthrough):

  ``out`` starts as ``{}``. The adapter writes ONLY the keys it
  intends to write. Nothing from ``source`` leaks through except by
  an explicit, field-name-based allow-list. The signal blobs
  (``github_signals`` / ``linkedin_signals`` / ``kaggle_signals`` /
  ``scholar_signals``), the raw ``experiences`` (plural), the raw
  74-skill list, and every other profile field are dropped.

GROUP A — v2 writes these. NEVER from source.

  - ``professional_summary`` — from ``sections["summary"].summary_text``.
    Empty → ``logger.warning`` + omit the key (no source fallback).
  - ``skills`` — from ``sections["skills"].skills_line`` split on
    ``,`` / ``·``. Empty → warn + omit.
  - ``experience`` — from ``sections["experience"].entities`` via
    ``_entity_to_item`` (existing id → normalised title+company match
    chain unchanged); empty → warn + emit ``[]``.
  - ``projects`` — same.
  - ``education`` — hybrid: v2 lines drive count + order (the cap is
    enforced). For each line, match against ``source["education"]``
    by normalised name and merge the source entry's allow-listed
    fields onto the line. No match → ``{"name": line}``.
  - ``certifications`` — same hybrid.
  - ``languages`` — flat string list from ``sections["languages"].lines``.

GROUP B — copy-through allow-list (top-level, source-only).

  Only ``professional_title`` and ``objective`` — both editor-managed
  fields v2 has no analog for. Copied through ONLY when present and
  non-empty. Nothing else.

GROUP C — internal source-lookup tables (NOT copied to output).

  ``source["experience"]`` (the singular alias the dispatcher creates
  from ``experiences``), ``source["projects"]``, ``source["education"]``,
  ``source["certifications"]`` — consulted by the per-item helpers to
  enrich v2 entities, never carried as top-level keys.

GROUP D — dropped (everything else in source).

  Contact fields (``full_name`` / ``email`` / ``phone`` / ``location`` /
  ``*_url``) flow to the PDF template via ``profile.*`` (UserProfile
  model properties) — they don't need to live in the content dict.
  Signal blobs are baggage that inflated the ATS keyword count.
  Both are gone.

EMPTY-SECTION HANDLING.

  v2 producing an empty section is logged at WARNING (so the editor
  surfaces it via the supervisor / logging path) and the output is
  either omitted (string sections) or ``[]`` (list sections). The
  source's value never substitutes. A silent passthrough was the bug.

PER-ITEM ALLOW-LISTS.

  Every item in ``experience`` / ``projects`` / ``education`` /
  ``certifications`` is filtered to its approved key set before
  emission, so nothing arbitrary from ``source`` survives even
  through the per-entity match-and-merge path.

The helpers (``_norm``, ``_split_entity_display``, ``_items_overlap``,
``_find_source_match``, ``_entity_to_item``) are unchanged. The
duration-from-source logic at ``_entity_to_item`` is preserved — it
reads source dates from the Group-C lookups, not from arbitrary
passthrough.
"""
from __future__ import annotations

import logging

from .text_norm import norm as _norm

logger = logging.getLogger(__name__)


# Per-section per-item allow-lists. The adapter never emits any key
# outside these on the corresponding section's items.
_EXPERIENCE_ITEM_KEYS = (
    "title", "company", "location", "industry",
    "start_date", "end_date", "is_current", "duration", "description",
)
_PROJECT_ITEM_KEYS = (
    "name", "url", "technologies",
    "start_date", "end_date", "duration", "description",
)
_EDUCATION_ITEM_KEYS = (
    "degree", "field", "institution", "year", "graduation_year",
    "location", "gpa", "honors",
)
_CERTIFICATION_ITEM_KEYS = (
    "name", "issuer", "date", "url", "duration",
)


def _split_entity_display(s: str):
    """Split a v2 ``entity_display`` string into ``(title, company)``
    on the ``" @ "`` separator. ``"AI Trainee @ DEPI"`` → ``("AI Trainee",
    "DEPI")``. Without the separator (projects: ``"customer-seg-rfmt"``)
    → ``(s, "")``.
    """
    if not isinstance(s, str) or not s:
        return "", ""
    if " @ " in s:
        title, _, company = s.partition(" @ ")
        return title.strip(), company.strip()
    return s.strip(), ""


def _items_overlap(a: str, b: str) -> bool:
    """Conservative title/company match — normalized equality OR
    substring in either direction. Catches case + whitespace drift
    without admitting unrelated strings."""
    a = _norm(a)
    b = _norm(b)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _find_source_match(entity, source_items):
    """Run the match chain (id → normalised title+company) and return
    the source item to merge with, or ``None`` when no confident match.

    Conservative when ambiguous: if multiple source items pass the
    (title, company) filter, returns ``None`` — better to fall through
    to the no-match branch (which still preserves v2 bullets) than to
    wrongly merge metadata from a different role / project.
    """
    if not source_items:
        return None
    entity_id = getattr(entity, "entity_id", "") or ""
    # Pass 1 — id match (kept for any source that does carry ids).
    if entity_id:
        for item in source_items:
            if isinstance(item, dict) and str(item.get("id") or "") == entity_id:
                return item
    # Pass 2 — normalised (title, company) match. EntityBlock doesn't
    # carry separate title/company fields, so we parse them out of
    # entity_display.
    entity_display = getattr(entity, "entity_display", "") or ""
    ent_title, ent_company = _split_entity_display(entity_display)
    if not _norm(ent_title):
        return None
    candidates = []
    for item in source_items:
        if not isinstance(item, dict):
            continue
        src_title = item.get("title") or item.get("name") or ""
        src_company = item.get("company") or ""
        if not _items_overlap(ent_title, src_title):
            continue
        # When entity_display carries a company hint, require it to
        # overlap with the source row's company. For projects (no
        # company on either side), the title alone matches.
        if _norm(ent_company):
            if not _items_overlap(ent_company, src_company):
                continue
        candidates.append(item)
    # Only return when we have a single confident match.
    if len(candidates) == 1:
        return candidates[0]
    return None


def _filter_to_keys(d, allowed):
    """Keep only ``allowed`` keys from ``d``. Drops everything else —
    the per-item enforcement of the allow-list contract."""
    if not isinstance(d, dict):
        return {}
    return {k: d[k] for k in allowed if k in d}


def _enrich_v2_line_with_source(line, source_items, *, allowed_keys):
    """Hybrid education / certifications: a v2 line drives count +
    order; source enriches by normalised name match.

    For each v2 line, scan ``source_items`` for an entry whose
    name/degree/institution overlaps the line (via the existing
    ``_items_overlap`` — case-insensitive, whitespace-tolerant,
    substring-tolerant). Exactly one confident match → return that
    source entry filtered to ``allowed_keys``. Otherwise → return
    ``{"name": line}`` so the cap is still respected.

    Source entries NOT matched by any v2 line are dropped (this is
    where the cap enforcement bites).
    """
    if not source_items:
        return {"name": line}
    norm_line = _norm(line)
    if not norm_line:
        return {"name": line}
    candidates = []
    for item in source_items:
        if not isinstance(item, dict):
            continue
        # Try the common name-bearing fields, in priority order.
        for field in ("name", "degree", "institution", "title"):
            candidate = item.get(field) or ""
            if _items_overlap(line, candidate):
                candidates.append(item)
                break
    if len(candidates) == 1:
        return _filter_to_keys(candidates[0], allowed_keys)
    # Zero or ambiguous matches → cap-respecting fallback. Do NOT
    # pick one of multiple matches (ambiguity-safe by construction).
    return {"name": line}


def resume_v2_to_template_dict(generated, source=None):
    """Flatten a ``GeneratedResumeV2`` into the v1 template-dict shape.

    Parameters
    ----------
    generated : GeneratedResumeV2
        The v2 generator output.
    source : dict | None
        The original v1 content dict — consulted ONLY for the explicit
        allow-list (Group B top-level + Group C per-section lookups).
        Nothing else from ``source`` reaches the output.

    Returns
    -------
    dict
        v1 template-dict — safe to feed to the same PDF templates the
        v1 path renders. Built fresh from ``{}``; carries no
        provenance, no signal blobs, no unused profile fields.
    """
    out: dict = {}
    src = source or {}
    sections = getattr(generated, "sections", None) or {}

    # ---- Group A.1 — Professional summary ---------------------------------
    summary_text = ""
    summary_sec = sections.get("summary")
    if summary_sec is not None:
        summary_text = (getattr(summary_sec, "summary_text", "") or "").strip()
    if summary_text:
        out["professional_summary"] = summary_text
    else:
        logger.warning(
            "resume_v2_adapter: v2 summary section empty — omitting "
            "professional_summary (no source fallback)."
        )

    # ---- Group A.2 — Skills ----------------------------------------------
    skills_items: list[str] = []
    skills_sec = sections.get("skills")
    if skills_sec is not None:
        line = (getattr(skills_sec, "skills_line", "") or "").strip()
        if line:
            # Generator emits a single delimited line; split for the
            # template. Accept ',' and '·' as delimiters.
            skills_items = [s.strip() for s in line.replace("·", ",").split(",") if s.strip()]
    if skills_items:
        out["skills"] = skills_items
    else:
        logger.warning(
            "resume_v2_adapter: v2 skills section empty — omitting skills "
            "(no source fallback)."
        )

    # ---- Group A.3 — Experience ------------------------------------------
    exp_sec = sections.get("experience")
    exp_entities = (
        getattr(exp_sec, "entities", None) or []
    ) if exp_sec is not None else []
    exp_source_items = src.get("experience") or []
    if exp_entities:
        out["experience"] = [
            _filter_to_keys(
                _entity_to_item(e, source_items=exp_source_items),
                _EXPERIENCE_ITEM_KEYS,
            )
            for e in exp_entities
        ]
    else:
        out["experience"] = []
        logger.warning(
            "resume_v2_adapter: v2 experience section empty — emitting []."
        )

    # ---- Group A.4 — Projects --------------------------------------------
    proj_sec = sections.get("projects")
    proj_entities = (
        getattr(proj_sec, "entities", None) or []
    ) if proj_sec is not None else []
    proj_source_items = src.get("projects") or []
    if proj_entities:
        out["projects"] = [
            _filter_to_keys(
                _entity_to_item(e, source_items=proj_source_items),
                _PROJECT_ITEM_KEYS,
            )
            for e in proj_entities
        ]
    else:
        out["projects"] = []
        logger.warning(
            "resume_v2_adapter: v2 projects section empty — emitting []."
        )

    # ---- Group A.5 — Education (hybrid: v2 lines drive, source enriches) -
    edu_sec = sections.get("education")
    edu_lines: list[str] = []
    if edu_sec is not None:
        edu_lines = [
            str(l).strip()
            for l in (getattr(edu_sec, "lines", None) or [])
            if str(l).strip()
        ]
    edu_source_items = src.get("education") or []
    if edu_lines:
        out["education"] = [
            _enrich_v2_line_with_source(
                line, edu_source_items, allowed_keys=_EDUCATION_ITEM_KEYS,
            )
            for line in edu_lines
        ]
    else:
        out["education"] = []
        logger.warning(
            "resume_v2_adapter: v2 education section empty — emitting []."
        )

    # ---- Group A.6 — Certifications (hybrid) -----------------------------
    cert_sec = sections.get("certifications")
    cert_lines: list[str] = []
    if cert_sec is not None:
        cert_lines = [
            str(l).strip()
            for l in (getattr(cert_sec, "lines", None) or [])
            if str(l).strip()
        ]
    cert_source_items = src.get("certifications") or []
    if cert_lines:
        out["certifications"] = [
            _enrich_v2_line_with_source(
                line, cert_source_items, allowed_keys=_CERTIFICATION_ITEM_KEYS,
            )
            for line in cert_lines
        ]
    else:
        out["certifications"] = []
        logger.warning(
            "resume_v2_adapter: v2 certifications section empty — emitting []."
        )

    # ---- Group A.7 — Languages -------------------------------------------
    lang_sec = sections.get("languages")
    lang_lines: list[str] = []
    if lang_sec is not None:
        lang_lines = [
            str(l).strip()
            for l in (getattr(lang_sec, "lines", None) or [])
            if str(l).strip()
        ]
    if lang_lines:
        out["languages"] = lang_lines
    else:
        out["languages"] = []
        logger.warning(
            "resume_v2_adapter: v2 languages section empty — emitting []."
        )

    # ---- Group B — copy-through allow-list (source-only, non-empty) ------
    for key in ("professional_title", "objective"):
        val = src.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val

    return out


def _entity_to_item(entity, source_items=None):
    """Convert one v2 ``EntityBlock`` to a v1 experience/project dict.

    Unchanged from the prior adapter: id → normalised title + company
    match chain via ``_find_source_match``. On match, source metadata
    flows through (company, location, industry, start_date, end_date,
    is_current, url, technologies, …) and v2 bullets become
    ``description``. On no match, ``entity_display`` is split into
    ``title`` / ``company`` (and ``name`` for projects).

    The CALLER is responsible for filtering the returned dict to the
    per-section allow-list — this helper still returns the full
    matched-source dict so the allow-list can be applied uniformly
    upstream.
    """
    bullets = [
        getattr(b, "text", "")
        for b in (getattr(entity, "bullets", None) or [])
        if str(getattr(b, "text", "")).strip()
    ]
    matched = _find_source_match(entity, source_items)
    if matched is not None:
        out = dict(matched)
        # v2 bullets always win — never overwrite with source description.
        out["description"] = bullets
        # Source profiles rarely pre-compute ``duration`` (the CV parser
        # stores start_date / end_date only). Build it honestly from
        # the source dates via the existing helper so the render-time
        # heal has something to act on — and so AOI-style "end_date=None"
        # roles render the start alone, not "Present".
        if not out.get("duration"):
            try:
                from resumes.services.resume_normalizer import (
                    assemble_duration_honest,
                )
                dur = assemble_duration_honest(
                    out.get("start_date") or "",
                    out.get("end_date") or "",
                    out.get("is_current") is True,
                )
                if dur:
                    out["duration"] = dur
            except Exception:  # noqa: BLE001 — best-effort; render-time heal is the safety net
                pass
        return out
    # No confident source match — keep v2 bullets and split the
    # entity_display so title / company render in separate cells.
    ent_title, ent_company = _split_entity_display(
        getattr(entity, "entity_display", "") or ""
    )
    return {
        "title": ent_title,
        # ``name`` is what the projects template reads — set both so the
        # same dict shape works for experience (``title``) AND projects
        # (``name``) without the adapter needing to know which.
        "name": ent_title,
        "company": ent_company,
        "duration": "",
        "location": "",
        "description": bullets,
    }
