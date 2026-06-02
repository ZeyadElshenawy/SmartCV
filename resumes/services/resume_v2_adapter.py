"""Adapter: ``GeneratedResumeV2`` → v1 template-dict shape.

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

    Provenance fields (``fact_ids``, ``hedged``, ``anchor_fact_id``) are
    dropped on purpose. When a matching v1 source item exists (by
    ``entity_id`` against the v1 item's ``id``) its structured fields
    (company, duration, location, etc.) are merged in so the rendered
    item keeps that context; otherwise the v2 ``entity_display`` string
    fills the title slot.
    """
    bullets = [
        getattr(b, "text", "")
        for b in (getattr(entity, "bullets", None) or [])
        if str(getattr(b, "text", "")).strip()
    ]
    entity_id = getattr(entity, "entity_id", "") or ""
    matched = None
    for item in (source_items or []):
        if isinstance(item, dict) and str(item.get("id") or "") == entity_id:
            matched = item
            break
    if matched is not None:
        out = dict(matched)
        out["description"] = bullets
        return out
    # No structured source item to merge — drop the v2 display string into
    # the title slot. company / duration / location stay empty; the
    # template renders missing fields as no-ops.
    return {
        "title": getattr(entity, "entity_display", "") or "",
        "company": "",
        "duration": "",
        "location": "",
        "description": bullets,
    }
