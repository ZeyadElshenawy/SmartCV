"""Adapter: ``GeneratedResumeV2`` → v1 template-dict shape.

Match chain for entities (experience / projects):

  1. ID match — if both the v2 entity_id and the source item's ``id``
     equal, use that source item. Kept for any source that does carry
     ids; in the profile-data flow this branch never fires because
     ``profile.data_content.experiences[]`` / ``projects[]`` don't
     have ``id`` fields.

  2. Normalised (title, company) match — the v2 ``EntityBlock`` has
     no separate title / company fields (only ``entity_display``), so
     we parse ``entity_display`` on the ``" @ "`` separator into
     (title, company). Both sides are lowercased + whitespace-collapsed
     before comparison. We require BOTH title overlap AND company
     overlap (when entity_display carries a company hint); for
     projects, which have no company, the title alone matches by name.
     Ambiguous (multiple candidates) → no merge (fall through).

  3. No match — keep the v2 bullets, but split ``entity_display`` so
     ``title`` and ``company`` (or ``name`` for projects) are populated
     separately rather than as the "Title @ Company" composite.

When matched, source metadata (company / location / industry /
start_date / end_date / is_current / url / technologies / pushed_at /
duration if present) flows through to the template dict; v2 provides
the description bullets. ``duration`` is built honestly from
start/end via ``assemble_duration_honest`` when the source carries
dates but no pre-computed duration — so dates always reach the
template ready for the existing render-time heal.


The PDF templates consume the v1 dict shape::

    {
        "professional_summary": str,
        "skills": list[str],
        "experience":     [{"title", "company", "duration", "location",
                            "industry", "description": list[str]}, ...],
        "projects":       [{"name", "url", "technologies", "duration",
                            "description": list[str]}, ...],
        "education":      [{"degree", "field", "institution", "year",
                            "location", "gpa", "honors"}, ...],
        "certifications": [{"name", "issuer", "date", "url"}, ...],
        "languages":      list[str],
        "professional_title": str,
        "objective": str,
        "section_order": list[str],
        "template_name": str,
    }

The v2 generator (``resume_generator_v2.GeneratedResumeV2``) is structurally
richer: sections carry ``GeneratedBullet`` objects with ``fact_ids`` and
``hedged`` flags for downstream grounding / supervisor use. This adapter
FLATTENS to the v1 dict — provenance stays inside the v2 model and is
NEVER emitted into the HTML / PDF the recruiter sees.

Education / certifications / languages are not enriched by the v2
content pipeline; for those the adapter prefers the ``source`` v1 dict
when available, falling back to v2's ``lines`` list otherwise.
"""
from __future__ import annotations

import re


def _norm(s) -> str:
    """Normalise a string for comparison — lowercase, whitespace collapsed,
    leading/trailing whitespace stripped. ``None`` / non-strings → ``""``."""
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s.lower()).strip()


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


def resume_v2_to_template_dict(generated, source=None):
    """Flatten a ``GeneratedResumeV2`` into the v1 template-dict shape.

    Parameters
    ----------
    generated : GeneratedResumeV2
        The v2 generator output.
    source : dict | None
        The original v1 content dict, if any. Header fields
        (professional_title, objective), education, certifications, and
        languages are copied through from here when the v2 sections don't
        carry richer data.

    Returns
    -------
    dict
        v1 template-dict — safe to feed to the same PDF templates the
        v1 path renders. Carries no ``fact_ids`` or other provenance.
    """
    out = dict(source or {})
    sections = getattr(generated, "sections", None) or {}

    summary_sec = sections.get("summary")
    if summary_sec is not None:
        text = (getattr(summary_sec, "summary_text", "") or "").strip()
        if text:
            out["professional_summary"] = text

    skills_sec = sections.get("skills")
    if skills_sec is not None:
        line = (getattr(skills_sec, "skills_line", "") or "").strip()
        if line:
            # Generator emits a single delimited line; split for the
            # template, which categorises + joins downstream. Accept ','
            # and '·' as delimiters.
            items = [s.strip() for s in line.replace("·", ",").split(",") if s.strip()]
            if items:
                out["skills"] = items

    exp_sec = sections.get("experience")
    if exp_sec is not None:
        out["experience"] = [
            _entity_to_item(e, source_items=(source or {}).get("experience"))
            for e in (getattr(exp_sec, "entities", None) or [])
        ]

    proj_sec = sections.get("projects")
    if proj_sec is not None:
        out["projects"] = [
            _entity_to_item(e, source_items=(source or {}).get("projects"))
            for e in (getattr(proj_sec, "entities", None) or [])
        ]

    # Education / Certifications / Languages: prefer the source v1 dict
    # (the templates' dicts have richer fields than v2's flat ``lines``).
    # Fall back to v2 lines only when v1 didn't carry the section.
    for sec_key in ("education", "certifications", "languages"):
        sec = sections.get(sec_key)
        if sec is None or out.get(sec_key):
            continue
        lines = [str(l).strip() for l in (getattr(sec, "lines", None) or []) if str(l).strip()]
        if not lines:
            continue
        if sec_key == "languages":
            out["languages"] = lines
        else:
            out[sec_key] = [{"name": l} for l in lines]

    return out


def _entity_to_item(entity, source_items=None):
    """Convert one v2 ``EntityBlock`` to the v1 experience/project dict.

    Provenance fields (``fact_ids``, ``hedged``, ``anchor_fact_id``)
    are dropped on purpose. The match chain (id → normalised title +
    company) decides whether a source item is merged in; when matched,
    SOURCE metadata flows through (company, location, industry,
    start_date, end_date, is_current, url, technologies, …) and v2
    bullets become ``description``. When unmatched, ``entity_display``
    is split into ``title`` / ``company`` (and ``name`` for projects)
    so the template renders separate cells rather than the composite.
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
