"""HR/CV specialist supervisor — the final review layer.

Automates the manual quality loop the user ran by hand (render the resume,
paste the screenshot + profile + JD + logs into Claude chat, get a recruiter
review back, apply fixes). ``review_resume`` renders the generated resume to an
image and asks the LLM, wearing a senior-recruiter hat and grounded in the KB
standards block, to flag fixable, evidence-grounded problems.

Two-step by necessity: the default Groq model serves vision in a *plain* call
but rejects vision combined with structured (tool-call) output (confirmed in
the Step 0 spike — tool-call schema validation 400s). So:

  Step A  get_llm()            image + prompt   → free-form recruiter critique
  Step B  get_structured_llm() critique text    → SupervisorReview (JSON)

The whole thing fails OPEN: any unrecoverable error yields an "advance" verdict
with no findings, so the supervisor can never block a resume from shipping.
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage

from profiles.services.llm_engine import get_llm, get_structured_llm
from profiles.services.schemas import SupervisorReview
from resumes.services.resume_render import render_resume_png, png_to_data_url
from resumes.services.resume_generator import (
    _build_standards_section,
    _is_token_limit_error,
    _extract_failed_generation,
    _tolerant_json_parse,
)

logger = logging.getLogger(__name__)


SUPERVISOR_PROMPT = """You are a SENIOR HR / CV SPECIALIST doing a final pre-ship review of a tailored \
resume, exactly as a recruiter would before forwarding a candidate. Be rigorous but FAIR.

THE NON-NEGOTIABLE GROUNDING RULE — read first:
- Flag ONLY problems that are real, fixable, and grounded in the evidence below.
- NEVER demand a metric, employer, tool, certification, or number that is not already in the evidence. \
Suggesting the candidate "add a metric" they don't have is fabrication — do NOT do it.
- The MISSING skills are skills the candidate does NOT have. NEVER suggest adding them or claiming them.
- "Could be stronger if more data existed" is NOT a blocking issue — at most a WARNING, and only when a \
concrete grounded fix exists.
- If you are unsure whether something is a problem, mark it WARNING, not BLOCKING.

SEVERITY:
- BLOCKING = a genuine deal-breaker a recruiter would reject on (e.g. a fabricated/unsupported claim, the \
most relevant experience buried below filler, the professional summary truncated mid-sentence, ordering \
that hides the candidate's best fit for THIS job).
- WARNING = a real but non-fatal weakness (thin phrasing, mild ordering, minor ATS nit).

LAYER — this decides whether the issue can be fixed by re-generating the resume text:
- CONTENT = anything in the resume's words/structure: summary, skills, experience, projects, certs, \
education, ordering, JD-fit, grounding. These CAN be fixed by regenerating.
- RENDER = a property of the rendered page in the attached image ONLY: page-break orphans, content \
overflowing the page, date format inconsistency between sections, header/separator styling, spacing, \
alignment. These CANNOT be fixed by regenerating the text — flag them for visibility, tagged RENDER.
{image_note}

REVIEW EVERY SECTION (summary, skills, experience, projects, certifications, education) plus overall \
ordering and ATS-friendliness and JD fit. For each problem you find, write ONE line in this exact form:

- SECTION=<section> | SEVERITY=<BLOCKING|WARNING> | LAYER=<CONTENT|RENDER> | ISSUE=<what's wrong, specific> | FIX=<the concrete grounded fix>

Then a final line: VERDICT=<ADVANCE if no blocking content issues, otherwise REVISE> followed by one \
sentence of summary. If the resume is genuinely clean, it is correct to return VERDICT=ADVANCE with no \
problem lines — do not invent problems to look thorough.

=== EVIDENCE (the ONLY facts you may treat as true) ===
{context}
"""

_IMAGE_NOTE_WITH = (
    "\nAn IMAGE of the rendered resume is attached. Use it to judge RENDER/layout issues "
    "(page breaks, overflow, date-format consistency, header styling). Judge CONTENT from the "
    "JSON and evidence below."
)
_IMAGE_NOTE_WITHOUT = (
    "\nNO rendered image is available this round. Review CONTENT ONLY. Do NOT raise any "
    "RENDER/layout findings — you cannot see the rendered page."
)


STRUCTURE_PROMPT = """Convert the recruiter review below into structured JSON.

OUTPUT SHAPE — a single object with EXACTLY these keys and NOTHING else:
{{
  "verdict": "advance" | "revise",
  "summary": "<one sentence>",
  "findings": [
    {{
      "layer": "content" | "render",
      "severity": "blocking" | "warning",
      "category": "<summary|skills|experience|projects|certs|education|ats|layout|grounding|ordering>",
      "location": "<where in the resume>",
      "issue": "<what is wrong>",
      "fix": "<the concrete fix>"
    }}
  ]
}}

RULES:
- One findings entry per problem line in the review. If the review lists no problems, return "findings": [].
- Map SEVERITY=BLOCKING -> "blocking", everything else -> "warning".
- Map LAYER=RENDER -> "render", everything else -> "content".
- Map VERDICT=ADVANCE -> "advance", otherwise "revise".
- Do NOT add any field beyond layer, severity, category, location, issue, fix. Do NOT invent findings \
that are not in the review.

=== RECRUITER REVIEW ===
{critique}
"""


def _compact_gap_lines(gap_analysis) -> str:
    """Gap-analysis lines ONLY (matched/missing/soft) — deliberately excludes
    the GitHub/Kaggle/LinkedIn signal blobs that bloat the generator prompt
    into 413 territory. Mirrors the gap section of ``_build_evidence_context``.
    """
    matched = list(getattr(gap_analysis, 'matched_skills', None) or [])
    missing = list(getattr(gap_analysis, 'critical_missing_skills', None) or
                   getattr(gap_analysis, 'missing_skills', None) or [])
    soft = list(getattr(gap_analysis, 'soft_skill_gaps', None) or [])
    lines = []
    if matched:
        lines.append(f"MATCHED (candidate HAS these): {', '.join(map(str, matched))}")
    if missing:
        lines.append(f"MISSING (candidate does NOT have — never suggest adding): {', '.join(map(str, missing))}")
    if soft:
        lines.append(f"SOFT GAPS: {'; '.join(map(str, soft))}")
    return "\n".join(lines)


def _build_review_context(resume_content, job, gap_analysis, standards_block) -> str:
    parts = []
    if standards_block:
        parts.append("=== KB STANDARDS (the bar to judge against) ===\n" + standards_block)

    jd_title = (getattr(job, 'title', '') or '').strip()
    jd_company = (getattr(job, 'company', '') or '').strip()
    jd_skills = getattr(job, 'extracted_skills', None) or []
    jd_desc = (getattr(job, 'description', '') or '')[:1500]
    jd_lines = ["=== TARGET JOB ==="]
    if jd_title:
        jd_lines.append(f"Title: {jd_title}")
    if jd_company:
        jd_lines.append(f"Company: {jd_company}")
    if jd_skills:
        jd_lines.append(f"Required skills: {', '.join(map(str, jd_skills))}")
    if jd_desc:
        jd_lines.append(f"Description (truncated):\n{jd_desc}")
    parts.append("\n".join(jd_lines))

    gap = _compact_gap_lines(gap_analysis)
    if gap:
        parts.append("=== GAP ANALYSIS ===\n" + gap)

    # The deterministic validators' findings — the structured proxy for the
    # pipeline logs the manual reviewer read.
    vr = (resume_content or {}).get('validation_report') or {}
    if vr:
        parts.append("=== DETERMINISTIC VALIDATION REPORT (already-detected issues) ===\n"
                     + json.dumps(vr, ensure_ascii=False, default=str)[:1500])

    resume_json = json.dumps(resume_content, ensure_ascii=False, default=str)[:6000]
    parts.append("=== GENERATED RESUME (JSON) ===\n" + resume_json)

    return "\n\n".join(parts)


def _vision_critique(context: str, image_data_url) -> str:
    """Step A — free-form recruiter critique. Multimodal when an image is
    available, text-only otherwise."""
    llm = get_llm(temperature=0.2, max_tokens=2048, task="supervisor")
    prompt = SUPERVISOR_PROMPT.format(
        context=context,
        image_note=_IMAGE_NOTE_WITH if image_data_url else _IMAGE_NOTE_WITHOUT,
    )
    content = [{"type": "text", "text": prompt}]
    if image_data_url:
        content.append({"type": "image_url", "image_url": {"url": image_data_url}})
    resp = llm.invoke([HumanMessage(content=content)])
    return getattr(resp, 'content', '') or ''


def _structure_critique(critique_text: str) -> SupervisorReview:
    """Step B — structure the critique into a SupervisorReview (text-only;
    structured output can't combine with the image)."""
    llm = get_structured_llm(SupervisorReview, temperature=0.1, max_tokens=2048, task="supervisor")
    return llm.invoke(STRUCTURE_PROMPT.format(critique=critique_text))


def _recover_review_from_failed_generation(exc) -> SupervisorReview | None:
    """Salvage a SupervisorReview from a failed structured-output call.

    Groq stashes the rejected tool-call payload in the exception; reuse the
    generator's extraction + tolerant parse, then unwrap the bare-list /
    ``{name, parameters}`` tool-call envelope before validating.
    """
    raw = _extract_failed_generation(exc)
    if not raw:
        return None
    try:
        parsed = _tolerant_json_parse(raw)
    except Exception:
        return None

    # Unwrap a tool-call envelope: [{"name": ..., "parameters": {...}}] or {...}.
    # Only unwrap a single-element LIST when that element is an envelope (has
    # "parameters") — otherwise a one-item findings list would be misread as
    # an envelope and collapsed into an empty review.
    if (isinstance(parsed, list) and len(parsed) == 1
            and isinstance(parsed[0], dict) and 'parameters' in parsed[0]):
        parsed = parsed[0]
    if isinstance(parsed, dict) and 'parameters' in parsed and isinstance(parsed['parameters'], dict):
        parsed = parsed['parameters']

    try:
        return SupervisorReview.model_validate(parsed)
    except Exception as exc2:  # noqa: BLE001
        logger.warning("supervisor: recovery validate failed (%s)", exc2)
        return None


def review_resume(resume_content, profile, job, gap_analysis, *, standards_block=None) -> SupervisorReview:
    """Render the resume, review it as an HR/CV specialist, and return a
    structured verdict. Never raises — fails open to an 'advance' verdict."""
    image_data_url = None
    try:
        png = render_resume_png(resume_content, profile, pages=2)
        image_data_url = png_to_data_url(png)
    except Exception as exc:  # noqa: BLE001 — render must not block review
        logger.warning("supervisor: render failed (%s) — degrading to text-only review", exc)

    if standards_block is None:
        try:
            standards_block, _, _ = _build_standards_section(profile, job)
        except Exception:  # noqa: BLE001
            standards_block = ""

    context = _build_review_context(resume_content, job, gap_analysis, standards_block)

    try:
        critique = _vision_critique(context, image_data_url)
        return _structure_critique(critique)
    except Exception as exc:  # noqa: BLE001
        if _is_token_limit_error(exc):
            # Drop the image and the standards block and retry text-only.
            try:
                slim_context = _build_review_context(resume_content, job, gap_analysis, "")
                critique = _vision_critique(slim_context, None)
                return _structure_critique(critique)
            except Exception as exc2:  # noqa: BLE001
                exc = exc2
        recovered = _recover_review_from_failed_generation(exc)
        if recovered is not None:
            return recovered
        logger.warning("supervisor: review failed (%s) — failing open (advance)", exc)
        return SupervisorReview(verdict="advance", summary="review unavailable", findings=[])
