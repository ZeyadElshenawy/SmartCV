"""Render a resume `content` dict to a PNG for multimodal supervisor review.

The supervisor reviews a freshly-generated resume BEFORE it's persisted as a
``GeneratedResume``, so we can't reuse ``pdf_exporter.generate_pdf`` (which
takes a model instance). This mirrors that render — same templates, same
section-order resolution, same cairocffi shim — but works from the raw dict,
then rasterizes the PDF to PNG with PyMuPDF (already installed). Reusing the
xhtml2pdf path renders the *actual shipped artifact* and avoids the
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
    template_name: str = "standard",
    pages: int = 2,
    zoom: float = 2.0,
) -> bytes:
    """Render ``resume_content`` to PNG bytes (up to ``pages`` pages stacked
    vertically so page-break/layout issues are visible in one image).

    Raises RuntimeError on render failure; the caller (supervisor) treats that
    as "no image" and degrades to a text-only review rather than failing the
    whole resume pipeline.
    """
    from resumes.services.pdf_exporter import _shim_cairocffi_if_missing
    _shim_cairocffi_if_missing()

    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    # Local import — resumes.views imports services, so import lazily to avoid
    # a circular import at module load (same pattern as pdf_exporter).
    from resumes.views import RESUME_SECTION_KEYS, DEFAULT_SECTION_ORDER

    if template_name and template_name != "standard":
        template_file = f"resumes/pdf_template_{template_name}.html"
    else:
        template_file = "resumes/pdf_template.html"

    saved = (resume_content or {}).get("section_order") or []
    valid_saved = [s for s in saved if s in RESUME_SECTION_KEYS]
    section_order = valid_saved + [s for s in DEFAULT_SECTION_ORDER if s not in valid_saved]

    user = getattr(profile, "user", None)
    html_string = render_to_string(template_file, {
        "resume": resume_content,
        "user": user,
        "profile": profile,
        "section_order": section_order,
    })

    pdf_buf = io.BytesIO()
    status = pisa.CreatePDF(html_string, dest=pdf_buf)
    if status.err:
        raise RuntimeError(f"xhtml2pdf failed to render resume: {status.err}")
    pdf_bytes = pdf_buf.getvalue()

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


def png_to_data_url(png_bytes: bytes) -> str:
    """Base64 data URL for a langchain image_url content block."""
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
