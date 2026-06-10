"""Render a resume `content` dict to a PNG for multimodal supervisor review.

The supervisor reviews a freshly-generated resume BEFORE it's persisted as a
``GeneratedResume``, so we can't reuse ``pdf_exporter.generate_pdf`` (which
takes a model instance). This mirrors that render — same templates, same
section-order resolution, same WeasyPrint engine — but works from the raw dict,
then rasterizes the PDF to PNG with PyMuPDF (already installed). Reusing the
WeasyPrint path renders the *actual shipped artifact* and avoids the
undetected-chromedriver launch fragility a headless-screenshot path would add.
"""
from __future__ import annotations

import base64
import io
import logging

logger = logging.getLogger(__name__)


def render_resume_png(
    resume_content: dict,
    profile=None,
    *,
    template_name: str = "ats_clean",
    pages: int = 2,
    zoom: float = 2.0,
) -> bytes:
    """Render ``resume_content`` to PNG bytes (up to ``pages`` pages stacked
    vertically so page-break/layout issues are visible in one image).

    Raises RuntimeError on render failure; the caller (supervisor) treats that
    as "no image" and degrades to a text-only review rather than failing the
    whole resume pipeline.
    """
    from django.template.loader import render_to_string
    from weasyprint import HTML
    # Local import — resumes.views imports services, so import lazily to avoid
    # a circular import at module load (same pattern as pdf_exporter).
    from resumes.views import RESUME_SECTION_KEYS, DEFAULT_SECTION_ORDER
    from .pdf_exporter import resolve_template
    from .skill_categorizer import group_skills_for_display, should_show_grouped
    from .resume_normalizer import (
        heal_experience_durations, sort_experience_reverse_chronological,
    )

    theme, template_file = resolve_template(template_name)

    content = resume_content or {}
    # Defensive heal + re-sort — same pass as pdf_exporter so the
    # supervisor PNG reflects what the recruiter PDF will actually show.
    if content.get("experience"):
        content = {**content, "experience": heal_experience_durations(content["experience"])}
        content = sort_experience_reverse_chronological(content)
    saved = content.get("section_order") or []
    valid_saved = [s for s in saved if s in RESUME_SECTION_KEYS]
    section_order = valid_saved + [s for s in DEFAULT_SECTION_ORDER if s not in valid_saved]

    skills_list = content.get("skills") or []
    skill_groups = group_skills_for_display(skills_list)
    show_grouped_skills = should_show_grouped(skill_groups, len(skills_list))

    user = getattr(profile, "user", None)
    html_string = render_to_string(template_file, {
        "resume": content,
        "user": user,
        "profile": profile,
        "section_order": section_order,
        "skill_groups": skill_groups,
        "show_grouped_skills": show_grouped_skills,
        "theme": theme,
    })

    pdf_bytes = HTML(string=html_string).write_pdf()

    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        mat = fitz.Matrix(zoom, zoom)
        page_pngs = [
            page.get_pixmap(matrix=mat).tobytes("png")
            for i, page in enumerate(doc)
            if i < max(1, pages)
        ]
    finally:
        doc.close()

    if not page_pngs:
        raise RuntimeError("resume render produced zero pages")
    if len(page_pngs) == 1:
        return page_pngs[0]

    # Stack pages vertically so a single image shows multi-page layout.
    from PIL import Image

    imgs = [Image.open(io.BytesIO(p)).convert("RGB") for p in page_pngs]
    width = max(im.width for im in imgs)
    total_h = sum(im.height for im in imgs)
    canvas = Image.new("RGB", (width, total_h), "white")
    y = 0
    for im in imgs:
        canvas.paste(im, (0, y))
        y += im.height
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def render_resume_html(
    resume_content: dict,
    profile=None,
    *,
    template_name: str = "ats_clean",
) -> str:
    """Render ``resume_content`` to the shared PDF template's HTML string.

    The editor live preview (and the read-only view page) call this so they
    render the EXACT template the downloaded PDF renders — same
    ``resolve_template`` + the same context-prep ``generate_pdf`` /
    ``render_resume_png`` use (heal + reverse-chronological sort, section-order
    resolution, skill grouping). Preview and PDF therefore CANNOT drift on
    labels, structure, ordering, grouping, bullets, or theme CSS.

    Honest caveat: this HTML is rendered by the *browser*; the download is
    rendered by *WeasyPrint*. Same template, different engines — a close
    approximation (~90-95%), never pixel-identical.
    """
    from django.template.loader import render_to_string
    # Local imports mirror render_resume_png — resumes.views imports services,
    # so import lazily to avoid a circular import at module load.
    from resumes.views import RESUME_SECTION_KEYS, DEFAULT_SECTION_ORDER
    from .pdf_exporter import resolve_template
    from .skill_categorizer import group_skills_for_display, should_show_grouped
    from .resume_normalizer import (
        heal_experience_durations, sort_experience_reverse_chronological,
    )

    theme, template_file = resolve_template(template_name)

    content = resume_content or {}
    if content.get("experience"):
        content = {**content, "experience": heal_experience_durations(content["experience"])}
        content = sort_experience_reverse_chronological(content)
    saved = content.get("section_order") or []
    valid_saved = [s for s in saved if s in RESUME_SECTION_KEYS]
    section_order = valid_saved + [s for s in DEFAULT_SECTION_ORDER if s not in valid_saved]

    skills_list = content.get("skills") or []
    skill_groups = group_skills_for_display(skills_list)
    show_grouped_skills = should_show_grouped(skill_groups, len(skills_list))

    user = getattr(profile, "user", None)
    return render_to_string(template_file, {
        "resume": content,
        "user": user,
        "profile": profile,
        "section_order": section_order,
        "skill_groups": skill_groups,
        "show_grouped_skills": show_grouped_skills,
        "theme": theme,
    })


def png_to_data_url(png_bytes: bytes) -> str:
    """Base64 data URL for a langchain image_url content block."""
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
