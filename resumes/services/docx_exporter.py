"""DOCX exporter for tailored resumes.

Mirrors the same `section_order`, the same fields, and the same content
ordering as the PDF exporter — but produces a single ATS-friendly DOCX
style rather than six visual variants. DOCX is overwhelmingly consumed
by ATS pipelines (LinkedIn Easy Apply, Workday, Greenhouse) where
visual flourish is at best ignored and at worst stripped/mangled, so
trying to replicate `pdf_template_zeyad`'s bold sans-serif character in
DOCX would just add code without helping the user.

Builds the document programmatically via python-docx (already in
requirements.txt for CV-parsing reads). No new dependency.
"""
from __future__ import annotations

import io
import logging
from typing import Iterable, Optional

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Inches

logger = logging.getLogger(__name__)


# Mirror the PDF exporter's whitelist + default order. Importing from
# resumes.views would create a tighter coupling; redefining here keeps
# the docx exporter standalone-runnable from a Django shell or script.
RESUME_SECTION_KEYS = (
    'summary', 'skills', 'experience', 'education',
    'projects', 'certifications', 'languages',
)
DEFAULT_SECTION_ORDER = list(RESUME_SECTION_KEYS)


# --- Style helpers ----------------------------------------------------------

# All visual choices live here so a future tweak (e.g. brand color) is
# one-line. Sizes intentionally restrained — ATS parsers do better with
# plain text than with stylized blocks.
NAME_PT = 18
TITLE_PT = 12
CONTACT_PT = 9
SECTION_HEADING_PT = 11
ITEM_TITLE_PT = 10.5
ITEM_SUB_PT = 10
BODY_PT = 10
BULLET_PT = 10

ACCENT_RGB = RGBColor(0x1E, 0x3A, 0x8A)  # brand-900 — used sparingly


def _add_hyperlink(paragraph, url: str, text: str, color: Optional[RGBColor] = None) -> None:
    """Add a clickable hyperlink to a paragraph.

    python-docx doesn't expose hyperlinks directly; we have to build the
    underlying XML. This helper keeps the inline call clean.
    """
    part = paragraph.part
    r_id = part.relate_to(
        url,
        'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
        is_external=True,
    )
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)
    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    if color is not None:
        c = OxmlElement('w:color')
        c.set(qn('w:val'), '%02X%02X%02X' % (color[0], color[1], color[2]))
        rPr.append(c)
    u = OxmlElement('w:u')
    u.set(qn('w:val'), 'single')
    rPr.append(u)
    new_run.append(rPr)
    t = OxmlElement('w:t')
    t.text = text
    t.set(qn('xml:space'), 'preserve')
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def _set_run_font(run, *, size_pt: float = BODY_PT, bold: bool = False,
                  italic: bool = False, color: Optional[RGBColor] = None) -> None:
    run.font.name = 'Calibri'
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    if color is not None:
        run.font.color.rgb = color


def _add_section_heading(doc: Document, text: str) -> None:
    """An uppercase, bordered, brand-color heading. Restrained — DOCX
    section headings get aggressively re-styled by ATS pipelines anyway."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text.upper())
    _set_run_font(run, size_pt=SECTION_HEADING_PT, bold=True, color=ACCENT_RGB)
    # Bottom border on the heading paragraph for the underlined look.
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '6')
    bottom.set(qn('w:space'), '1')
    bottom.set(qn('w:color'), '94A3B8')  # slate-400
    pBdr.append(bottom)
    pPr.append(pBdr)


def _bullet(doc: Document, text: str) -> None:
    """Bulleted body paragraph. python-docx's built-in 'List Bullet' style
    is consistent across Word versions and doesn't depend on a numbering
    definition we'd have to ship."""
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.space_after = Pt(2)
    run = p.runs[0] if p.runs else p.add_run()
    if not p.runs[0].text:
        # python-docx creates an empty run with the style; use it.
        p.runs[0].text = text
    else:
        p.runs[0].text = text
    _set_run_font(p.runs[0], size_pt=BULLET_PT)


def _ensure_list(value) -> list:
    """description fields can be either a list or a multi-line string —
    normalize so the writer doesn't have to branch."""
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.split('\n') if line.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _resolve_section_order(content: dict) -> list:
    saved = (content or {}).get('section_order') or []
    valid = [s for s in saved if s in RESUME_SECTION_KEYS]
    return valid + [s for s in DEFAULT_SECTION_ORDER if s not in valid]


# --- Header ---------------------------------------------------------------

def _write_header(doc: Document, profile, content: dict) -> None:
    """Name + professional title + contact line. Renders the full set of
    URLs the parser extracts (LinkedIn, GitHub, Portfolio, Kaggle, Scholar,
    other_urls) — same surface area the PDF templates show, so the DOCX
    doesn't silently drop links the candidate carefully filled in."""
    name = (getattr(profile, 'full_name', None) or 'Your Name').strip()
    title = (content or {}).get('professional_title') or ''

    # Name
    p = doc.add_paragraph()
    p.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(name.upper())
    _set_run_font(run, size_pt=NAME_PT, bold=True)

    # Professional title (skip when empty so we don't render a blank line)
    if title:
        pt = doc.add_paragraph()
        pt.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        pt.paragraph_format.space_after = Pt(2)
        run = pt.add_run(title)
        _set_run_font(run, size_pt=TITLE_PT, color=ACCENT_RGB)

    # Contact line — assemble plain-text bits + clickable hyperlinks.
    # We mix plain runs and hyperlink runs so emails/phones stay
    # readable while LinkedIn/GitHub/etc. stay clickable.
    contact_p = doc.add_paragraph()
    contact_p.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    contact_p.paragraph_format.space_after = Pt(8)

    SEP = ' · '
    first = True

    def _add_text(text: str):
        nonlocal first
        if not text:
            return
        if not first:
            sep_run = contact_p.add_run(SEP)
            _set_run_font(sep_run, size_pt=CONTACT_PT)
        run = contact_p.add_run(text)
        _set_run_font(run, size_pt=CONTACT_PT)
        first = False

    def _add_link(url: str, label: str):
        nonlocal first
        if not url:
            return
        if not first:
            sep_run = contact_p.add_run(SEP)
            _set_run_font(sep_run, size_pt=CONTACT_PT)
        _add_hyperlink(contact_p, url, label, color=ACCENT_RGB)
        first = False

    _add_text(getattr(profile, 'email', '') or '')
    _add_text(getattr(profile, 'phone', '') or '')
    _add_text(getattr(profile, 'location', '') or '')
    _add_link(getattr(profile, 'linkedin_url', '') or '', 'LinkedIn')
    _add_link(getattr(profile, 'github_url', '') or '', 'GitHub')
    _add_link(getattr(profile, 'portfolio_url', '') or '', 'Portfolio')
    _add_link(getattr(profile, 'kaggle_url', '') or '', 'Kaggle')
    _add_link(getattr(profile, 'scholar_url', '') or '', 'Scholar')
    for u in (getattr(profile, 'other_urls', None) or []):
        # Strip protocol/www. so the visible label stays compact, same
        # transformation the PDF templates do.
        label = u
        for prefix in ('https://', 'http://', 'www.'):
            if label.startswith(prefix):
                label = label[len(prefix):]
        _add_link(u, label[:24])


# --- Section writers ------------------------------------------------------

def _write_summary(doc: Document, content: dict) -> None:
    text = (content or {}).get('professional_summary') or ''
    objective = (content or {}).get('objective') or ''
    if not text and not objective:
        return
    _add_section_heading(doc, 'Professional Summary')
    if text:
        p = doc.add_paragraph()
        run = p.add_run(text)
        _set_run_font(run, size_pt=BODY_PT)
    if objective:
        p = doc.add_paragraph()
        label = p.add_run('Objective: ')
        _set_run_font(label, size_pt=BODY_PT, bold=True)
        run = p.add_run(objective)
        _set_run_font(run, size_pt=BODY_PT)


def _write_skills(doc: Document, content: dict) -> None:
    skills = (content or {}).get('skills') or []
    if not skills:
        return
    _add_section_heading(doc, 'Skills')
    p = doc.add_paragraph()
    label = p.add_run('Core Competencies: ')
    _set_run_font(label, size_pt=BODY_PT, bold=True)
    body = p.add_run(', '.join(str(s) for s in skills))
    _set_run_font(body, size_pt=BODY_PT)


def _write_experience(doc: Document, content: dict) -> None:
    rows = (content or {}).get('experience') or []
    if not rows:
        return
    _add_section_heading(doc, 'Professional Experience')
    for exp in rows:
        if not isinstance(exp, dict):
            continue
        # Title + duration on the same paragraph: title left-aligned,
        # duration right-aligned via a tab stop.
        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(4)
        head.paragraph_format.space_after = Pt(0)
        # Set a right tab stop at the page width minus margins (~6.5 in).
        head.paragraph_format.tab_stops.add_tab_stop(Inches(6.5), WD_PARAGRAPH_ALIGNMENT.RIGHT)
        title_run = head.add_run(exp.get('title', '') or '')
        _set_run_font(title_run, size_pt=ITEM_TITLE_PT, bold=True)
        if exp.get('duration'):
            head.add_run('\t')
            date_run = head.add_run(exp.get('duration', ''))
            _set_run_font(date_run, size_pt=ITEM_TITLE_PT)
        # Company · location · industry on the next line, italic accent
        sub_bits = [b for b in (exp.get('company'), exp.get('location'), exp.get('industry')) if b]
        if sub_bits:
            sub = doc.add_paragraph()
            sub.paragraph_format.space_after = Pt(2)
            run = sub.add_run(' · '.join(sub_bits))
            _set_run_font(run, size_pt=ITEM_SUB_PT, italic=True, color=ACCENT_RGB)
        for bullet in _ensure_list(exp.get('description')):
            _bullet(doc, bullet)


def _write_education(doc: Document, content: dict) -> None:
    rows = (content or {}).get('education') or []
    if not rows:
        return
    _add_section_heading(doc, 'Education')
    for edu in rows:
        if not isinstance(edu, dict):
            continue
        degree = edu.get('degree') or ''
        field = edu.get('field') or ''
        institution = edu.get('institution') or ''
        location = edu.get('location') or ''
        gpa = edu.get('gpa') or ''
        honors = edu.get('honors') or []
        if isinstance(honors, str):
            honors = [line.strip() for line in honors.split('\n') if line.strip()]
        year = edu.get('graduation_year') or edu.get('year') or ''
        if field:
            degree_text = f"{degree} in {field}".strip()
        else:
            degree_text = degree
        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(2)
        head.paragraph_format.space_after = Pt(0)
        head.paragraph_format.tab_stops.add_tab_stop(Inches(6.5), WD_PARAGRAPH_ALIGNMENT.RIGHT)
        title_run = head.add_run(degree_text)
        _set_run_font(title_run, size_pt=ITEM_TITLE_PT, bold=True)
        if year:
            head.add_run('\t')
            date_run = head.add_run(str(year))
            _set_run_font(date_run, size_pt=ITEM_TITLE_PT)
        sub_bits = [b for b in (institution, location, (f"GPA {gpa}" if gpa else '')) if b]
        if sub_bits:
            sub = doc.add_paragraph()
            sub.paragraph_format.space_after = Pt(2)
            run = sub.add_run(' · '.join(sub_bits))
            _set_run_font(run, size_pt=ITEM_SUB_PT, italic=True, color=ACCENT_RGB)
        if honors:
            h = doc.add_paragraph()
            h.paragraph_format.space_after = Pt(2)
            run = h.add_run(' · '.join(honors))
            _set_run_font(run, size_pt=BODY_PT)


def _write_projects(doc: Document, content: dict) -> None:
    rows = (content or {}).get('projects') or []
    if not rows:
        return
    _add_section_heading(doc, 'Projects')
    for proj in rows:
        if not isinstance(proj, dict):
            continue
        name = proj.get('name', '')
        url = proj.get('url', '')
        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(2)
        head.paragraph_format.space_after = Pt(2)
        if url:
            _add_hyperlink(head, url, name or url, color=ACCENT_RGB)
            # Make the hyperlink bold to mirror the PDF's project-name weight
            for r in head._p.iter(qn('w:r')):
                rPr = r.find(qn('w:rPr'))
                if rPr is None:
                    rPr = OxmlElement('w:rPr')
                    r.insert(0, rPr)
                if rPr.find(qn('w:b')) is None:
                    rPr.append(OxmlElement('w:b'))
        else:
            run = head.add_run(name)
            _set_run_font(run, size_pt=ITEM_TITLE_PT, bold=True)
        # Tech stack on its own italic line under the project name (ATS keywords)
        techs = proj.get('technologies') or []
        if isinstance(techs, str):
            techs = [t.strip() for t in techs.split(',') if t.strip()]
        if techs:
            sub = doc.add_paragraph()
            sub.paragraph_format.space_after = Pt(2)
            run = sub.add_run(' · '.join(techs))
            _set_run_font(run, size_pt=ITEM_SUB_PT, italic=True, color=ACCENT_RGB)
        for bullet in _ensure_list(proj.get('description')):
            _bullet(doc, bullet)


def _write_certifications(doc: Document, content: dict) -> None:
    rows = (content or {}).get('certifications') or []
    if not rows:
        return
    _add_section_heading(doc, 'Certifications')
    for cert in rows:
        if not isinstance(cert, dict):
            continue
        name = cert.get('name', '')
        issuer = cert.get('issuer', '')
        date = cert.get('date', '')
        url = cert.get('url', '')
        p = doc.add_paragraph(style='List Bullet')
        p.paragraph_format.space_after = Pt(1)
        if url and name:
            _add_hyperlink(p, url, name, color=ACCENT_RGB)
            # Bold the hyperlink to match the PDF
            last_run = p._p.findall(qn('w:hyperlink'))[-1] if p._p.findall(qn('w:hyperlink')) else None
            if last_run is not None:
                for r in last_run.iter(qn('w:r')):
                    rPr = r.find(qn('w:rPr'))
                    if rPr is None:
                        rPr = OxmlElement('w:rPr')
                        r.insert(0, rPr)
                    if rPr.find(qn('w:b')) is None:
                        rPr.append(OxmlElement('w:b'))
        else:
            run = p.add_run(name)
            _set_run_font(run, size_pt=BODY_PT, bold=True)
        duration = cert.get('duration', '')
        suffix_bits = []
        if issuer:
            suffix_bits.append(f' - {issuer}')
        if duration:
            suffix_bits.append(f' · {duration}')
        if date:
            suffix_bits.append(f' ({date})')
        if suffix_bits:
            run = p.add_run(''.join(suffix_bits))
            _set_run_font(run, size_pt=BODY_PT)


def _write_languages(doc: Document, content: dict) -> None:
    langs = (content or {}).get('languages') or []
    if not langs:
        return
    _add_section_heading(doc, 'Languages')
    p = doc.add_paragraph()
    run = p.add_run(', '.join(str(l) for l in langs))
    _set_run_font(run, size_pt=BODY_PT)


_SECTION_WRITERS = {
    'summary': _write_summary,
    'skills': _write_skills,
    'experience': _write_experience,
    'education': _write_education,
    'projects': _write_projects,
    'certifications': _write_certifications,
    'languages': _write_languages,
}


# --- Entry point ----------------------------------------------------------

def generate_docx(resume_obj, output: io.BytesIO | str | None = None) -> io.BytesIO:
    """Generate a DOCX from a GeneratedResume row.

    `output` can be:
      - a path string → docx is written there, BytesIO returned anyway
        for symmetry with the PDF exporter's API
      - a BytesIO → docx written into it
      - None → a fresh BytesIO is created and returned

    Honors the user's saved section_order from resume.content with the
    same defensive resolution as the PDF exporter.
    """
    user = resume_obj.gap_analysis.job.user
    profile = user.profile
    content = resume_obj.content or {}
    section_order = _resolve_section_order(content)

    doc = Document()
    # Tighten the default margins so a one-page resume actually fits on
    # one page in Word's default view. Match the PDF templates' visual
    # density.
    for section in doc.sections:
        section.top_margin = Inches(0.5)
        section.bottom_margin = Inches(0.5)
        section.left_margin = Inches(0.6)
        section.right_margin = Inches(0.6)

    _write_header(doc, profile, content)
    for key in section_order:
        writer = _SECTION_WRITERS.get(key)
        if writer is None:
            continue
        try:
            writer(doc, content)
        except Exception:
            # One section failing must not torpedo the whole DOCX export.
            # Log and continue — the user gets a slightly-shorter doc
            # rather than a 500.
            logger.exception("DOCX export: section '%s' failed; skipping", key)

    # Persist
    if isinstance(output, str):
        doc.save(output)
        # Read back into BytesIO so callers that want bytes don't have to
        # re-open the file.
        with open(output, 'rb') as f:
            return io.BytesIO(f.read())
    buf = output if isinstance(output, io.BytesIO) else io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
