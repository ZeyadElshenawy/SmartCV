"""HR/CV specialist supervisor - the final review layer.

Automates the manual quality loop the user ran by hand (render the resume,
paste the screenshot + profile + JD + logs into Claude chat, get a recruiter
review back, apply fixes). ``review_resume`` renders the generated resume to an
image and asks the LLM, wearing a senior-recruiter hat and grounded in the KB
standards block, to flag fixable, evidence-grounded problems.

Two-step by necessity: the default Groq model serves vision in a *plain* call
but rejects vision combined with structured (tool-call) output (confirmed in
the Step 0 spike - tool-call schema validation 400s). So:

  Step A  get_llm()            image + prompt   -> free-form recruiter critique
  Step B  get_structured_llm() critique text    -> SupervisorReview (JSON)

The whole thing fails OPEN: any unrecoverable error yields an "advance" verdict
with no findings, so the supervisor can never block a resume from shipping.
"""
from __future__ import annotations

import difflib
import json
import logging
import re

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


# The prompt encodes a real recruiter's checklist. A generic "review the resume"
# instruction makes the model surface only generic findings (lacks metrics,
# etc.); the explicit checklist is what lets a 17B model catch the high-value
# issues a senior reviewer catches - redundant/padded bullets, overclaiming,
# skill-list duplicates, date inconsistency, internal jargon, role relevance.
SUPERVISOR_PROMPT = """You are a SENIOR HR / CV SPECIALIST and technical recruiter doing the FINAL \
pre-ship review of a tailored resume. You read resumes all day and you are hard to impress. Catch every \
issue a real recruiter would judge the candidate on. Be rigorous, specific, and FAIR.

THE NON-NEGOTIABLE GROUNDING RULE - read first:
- Flag ONLY problems that are real and grounded in the evidence below.
- NEVER tell the candidate to ADD a metric, employer, tool, or achievement they do not have. Telling them \
to "add a number" they lack is fabrication - do NOT do it.
- The MISSING skills are skills the candidate does NOT have. NEVER suggest adding or claiming them.
- BUT catching the resume for OVER-claiming IS your job: if the summary or a bullet claims more than the \
evidence supports, that is a grounding violation - flag it (see A and B below).
- If unsure whether something is a problem, mark it WARNING, not BLOCKING.

JUDGE SENIORITY FIRST: infer the candidate's real seniority from the experience (internship / trainee \
roles, student status, total years). Judge every claim against THAT level. A candidate with no full-time \
professional years CANNOT honestly claim "experienced in deploying ML models", "at scale", or "working \
with large datasets" - those are inflation.

RUN THIS CHECKLIST. Emit one finding per issue.

A. PROFESSIONAL SUMMARY
   - GENERIC: could this summary describe ANY candidate in this field? If it has no concrete anchor (the \
candidate's real status, a signature project, a specific credential), flag it as too generic.
   - OVERCLAIMING: does it claim experience / scale / outcomes the evidence does not support? Quote the \
exact phrase (e.g. "deploying machine learning models", "large datasets" for a 0-year candidate). BLOCKING.
   - JD FIT: does it reflect what THIS job emphasizes? If the JD stresses things the summary ignores, note \
it - but only suggest GROUNDED additions, never fabricated keywords.
   - TRUNCATION: does it end mid-thought or abruptly? BLOCKING.

B. EXPERIENCE - check EACH role separately
   - REDUNDANT BULLETS: do any two bullets in the SAME role describe the same accomplishment in different \
words? Redundancy reads as padding. Name the duplicate pair and say which to cut. BLOCKING if a role reads \
as padded.
   - BULLET COUNT vs TENURE: a short role (a few weeks / one or two months, an internship) with 5+ bullets \
looks inflated. Flag any role whose bullet count exceeds what its tenure justifies; say how many to keep \
(2-3 for a short internship).
   - DATES: does EVERY role show a COMPLETE, consistently-formatted date range (e.g. "Jun 2025 - Dec 2025")? \
Flag any role missing an end date, missing dates entirely, or formatted differently from the other roles.
   - VAGUE / JD-MIRROR FILLER: flag bullets that echo the JD's marketing language with no substance (vague \
verbs like "improve performance, increase efficiency, enhance customer experience"). They read as shallow \
AI-tailoring and a recruiter spots it instantly.
   - RELEVANCE: flag any role that does not support the TARGET role and dilutes the page (e.g. short \
unrelated workshops on a specialist resume). Suggest moving it to a "Training / Workshops" section or cutting.

C. SKILLS
   - DUPLICATES / NEAR-DUPLICATES: flag repeated or overlapping entries (e.g. "SQL" and "Databases & SQL"; \
"Supervised Learning" and "Supervised & Unsupervised Learning").
   - UNGROUNDED: flag a listed skill with no supporting evidence anywhere in the resume.

D. PROJECTS - check EACH
   - AUDIENCE / JARGON: bullets are read by recruiters, not your teammates. Flag commit-message-style \
bullets or internal jargon a non-technical recruiter cannot parse (e.g. "matched / missing / partial", \
"in-context bonus"). Say what the bullet should convey to an outsider (what the system DOES, the tech \
depth, the scale).
   - LEAD VALUE: the strongest, most differentiated project should lead, and its first bullet should say \
what the system DOES, not how its internals work.
   - OVER-GRANULAR TECH: flag overly granular tech tokens (e.g. a full model id like \
"meta-llama/llama-4-scout-17b-16e-instruct") where a category ("Groq + LLaMA-4") reads better.

E. CERTIFICATIONS / EDUCATION
   - WEAK / REDUNDANT CERTS: when the list is long, flag low-signal or redundant entries (e.g. several \
overlapping "fundamentals" certs).
   - DATES: flag certs / education missing dates when the others have them.

F. OVERALL
   - ATS: keyword stuffing, or grounded JD keywords that are missing.
   - ORDERING: is the most role-relevant content first in each section?

LAYER tagging (this decides whether regenerating the text can fix it):
- CONTENT = wording / structure fixable by regenerating: summary, bullet wording, redundant bullets, \
ordering, jargon, overclaiming, role relevance, a role MISSING its dates entirely (the data is gone).
- RENDER = a property of the rendered page in the attached image: page-break orphans, content overflow, \
date-FORMAT inconsistency between sections, header / separator styling, spacing, alignment.
{image_note}

SEVERITY:
- BLOCKING = a recruiter would reject or seriously downgrade on this: overclaiming / inflation, redundant \
or padded bullets, a truncated summary, the most relevant experience buried below filler, a role missing \
its dates.
- WARNING = a real but non-fatal weakness: generic-but-honest phrasing, minor ordering, one weak cert.

For EACH problem write ONE line in EXACTLY this form:
- SECTION=<section> | SEVERITY=<BLOCKING|WARNING> | LAYER=<CONTENT|RENDER> | ISSUE=<specific - name the exact bullet, phrase, skill, or role> | FIX=<the concrete grounded fix>

Then a final line: VERDICT=<ADVANCE if there are no blocking content issues, otherwise REVISE> followed by \
one sentence. If the resume is genuinely clean, returning VERDICT=ADVANCE with no problem lines is correct - \
do NOT invent problems to look thorough.

=== EVIDENCE (the ONLY facts you may treat as true) ===
{context}
"""

_IMAGE_NOTE_WITH = (
    "\nAn IMAGE of the rendered resume is attached. Use it to judge RENDER/layout issues "
    "(page breaks, overflow, date-format consistency across sections, header styling). Judge "
    "CONTENT from the JSON and evidence below."
)
_IMAGE_NOTE_WITHOUT = (
    "\nNO rendered image is available this round. Review CONTENT ONLY. Do NOT raise any "
    "RENDER/layout findings - you cannot see the rendered page."
)


STRUCTURE_PROMPT = """Convert the recruiter review below into structured JSON.

OUTPUT SHAPE - a single object with EXACTLY these keys and NOTHING else:
{{
  "verdict": "advance" | "revise",
  "summary": "<one sentence>",
  "findings": [
    {{
      "layer": "content" | "render",
      "severity": "blocking" | "warning",
      "category": "<summary|skills|experience|projects|certs|education|ats|layout|grounding|ordering|redundancy|relevance>",
      "location": "<where in the resume - which role / bullet / skill>",
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
- Do NOT add any field beyond layer, severity, category, location, issue, fix. Do NOT invent findings that \
are not in the review.

=== RECRUITER REVIEW ===
{critique}
"""


def _norm_text(s) -> str:
    """Lowercase, strip punctuation, collapse whitespace - for comparison."""
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', str(s).lower())).strip()


def _structural_observations(resume_content) -> str:
    """Deterministic pre-scan that surfaces the structural defects a 17B model
    won't reliably catch from prose alone: bullet bloat, near-duplicate bullets,
    missing dates, duplicate skills. These are HINTS for the LLM (it still judges
    severity, wording, and grounding, and can dismiss false positives) - the same
    deterministic-detection + LLM-judgment split the gap analysis uses.
    """
    obs: list[str] = []
    rc = resume_content or {}

    for e in (rc.get('experience') or []):
        if not isinstance(e, dict):
            continue
        role = (e.get('title') or '?').strip()
        bullets = e.get('description') or []
        if isinstance(bullets, str):
            bullets = [bullets]
        bullets = [b for b in bullets if isinstance(b, str) and b.strip()]
        n = len(bullets)
        if n >= 5:
            obs.append(
                f'Role "{role}" has {n} bullets - HIGH. Check whether its tenure justifies '
                f'this; a short internship should have 2-3.'
            )
        normed = [_norm_text(b) for b in bullets]
        for i in range(len(normed)):
            for j in range(i + 1, len(normed)):
                a, b = normed[i], normed[j]
                if not a or not b:
                    continue
                ratio = difflib.SequenceMatcher(None, a, b).ratio()
                ta, tb = set(a.split()), set(b.split())
                overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
                if ratio >= 0.6 or overlap >= 0.6:
                    obs.append(
                        f'Role "{role}": bullets {i + 1} and {j + 1} look REDUNDANT (same '
                        f'accomplishment, different words) - merge or cut one.'
                    )
        # Dates: a role missing dates, or showing a single date with no end.
        dur = (e.get('duration') or '').strip() if isinstance(e.get('duration'), str) else e.get('duration')
        sd, ed = e.get('start_date'), e.get('end_date')
        if not dur:
            if sd and not ed:
                obs.append(f'Role "{role}" has a start date but NO end date.')
            elif not sd and not ed:
                obs.append(f'Role "{role}" is MISSING its dates entirely.')
        elif isinstance(dur, str) and not re.search(r'[-–—]|\bto\b|present|current|now', dur, re.I):
            obs.append(
                f'Role "{role}" shows only a single date ("{dur}") with no end - '
                f'add an end date or "Present" so it matches the other roles.'
            )

    # Duplicate / subset skills (e.g. "SQL" within "Databases & SQL").
    skills = [s for s in (rc.get('skills') or []) if isinstance(s, str) and s.strip()]
    sn = [(s, set(_norm_text(s).split())) for s in skills]
    seen_pairs = 0
    for i in range(len(sn)):
        for j in range(i + 1, len(sn)):
            ta, tb = sn[i][1], sn[j][1]
            if not ta or not tb:
                continue
            if ta == tb or ta <= tb or tb <= ta:
                obs.append(f'Possible duplicate skills: "{sn[i][0]}" and "{sn[j][0]}".')
                seen_pairs += 1
                if seen_pairs >= 5:
                    break
        if seen_pairs >= 5:
            break

    if not obs:
        return ""
    return "=== STRUCTURAL OBSERVATIONS (deterministic pre-scan - verify and judge each) ===\n" + \
        "\n".join(f"- {o}" for o in obs[:12])


def _compact_gap_lines(gap_analysis) -> str:
    """Gap-analysis lines ONLY (matched/missing/soft) - deliberately excludes
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
        lines.append(f"MISSING (candidate does NOT have - never suggest adding): {', '.join(map(str, missing))}")
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
    jd_desc = (getattr(job, 'description', '') or '')[:2000]
    jd_lines = ["=== TARGET JOB (judge JD fit against this) ==="]
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

    structural = _structural_observations(resume_content)
    if structural:
        parts.append(structural)

    # The deterministic validators' findings - the structured proxy for the
    # pipeline logs the manual reviewer read.
    vr = (resume_content or {}).get('validation_report') or {}
    if vr:
        parts.append("=== DETERMINISTIC VALIDATION REPORT (already-detected issues) ===\n"
                     + json.dumps(vr, ensure_ascii=False, default=str)[:1500])

    # Wide enough that every experience/project bullet is in the text context -
    # the LLM compares exact bullet wording for redundancy from this, not the image.
    resume_json = json.dumps(resume_content, ensure_ascii=False, default=str)[:9000]
    parts.append("=== GENERATED RESUME (JSON) ===\n" + resume_json)

    return "\n\n".join(parts)


def _vision_critique(context: str, image_data_url) -> str:
    """Step A - free-form recruiter critique. Multimodal when an image is
    available, text-only otherwise. The checklist produces a longer critique
    than a generic review, so the token budget is generous."""
    llm = get_llm(temperature=0.2, max_tokens=3072, task="supervisor")
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
    """Step B - structure the critique into a SupervisorReview (text-only;
    structured output can't combine with the image)."""
    llm = get_structured_llm(SupervisorReview, temperature=0.1, max_tokens=2560, task="supervisor")
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
    # "parameters") - otherwise a one-item findings list would be misread as
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
    structured verdict. Never raises - fails open to an 'advance' verdict."""
    image_data_url = None
    try:
        png = render_resume_png(resume_content, profile, pages=2)
        image_data_url = png_to_data_url(png)
    except Exception as exc:  # noqa: BLE001 - render must not block review
        logger.warning("supervisor: render failed (%s) - degrading to text-only review", exc)

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
        logger.warning("supervisor: review failed (%s) - failing open (advance)", exc)
        return SupervisorReview(verdict="advance", summary="review unavailable", findings=[])
