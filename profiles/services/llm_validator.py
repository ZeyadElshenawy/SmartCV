import logging
import json
import os
from typing import Dict, Any, Optional
from .schemas import ResumeSchema
from .llm_engine import get_structured_llm, get_llm

logger = logging.getLogger(__name__)

# Stage 2: Flexible Extraction System Prompt (ENHANCED)
VALIDATION_SYSTEM_PROMPT = """You are a CV/Resume Data Extraction Expert.
Your task is to extract a complete structured profile from the provided CV text and parsed data.

CRITICAL INSTRUCTIONS:
1. Extract standard fields: full_name, email, phone, location, linkedin_url, github_url, portfolio_url, skills, experiences, education, projects, certifications.
2. **DYNAMIC FIELDS (The Core Logic)**: If you encounter sections that do NOT fit standard categories, create NEW top-level keys for them in snake_case format.
3. Normalize all data (dates in YYYY-MM format, full URLs).
4. Infer proficiency for skills if context is available.
5. **URL EXTRACTION (CRITICAL)**: Pay close attention to `[Embedded Link: ...]` tags in the text.
   - Map certification/course URLs into the `url` field of the matching `certifications` object.
   - Map project URLs into the `url` field of the matching `projects` object.
   - Map portfolio links to `portfolio_url` (look for text like "My portfolio", "Portfolio", or personal website links).
   - Map Kaggle, Twitter, or other profile URLs to `other_urls`.
   - Links are often attached to text labels (e.g., `[Embedded Link: 'LinkedIn' -> https://...]`) — use the label to identify what the link belongs to.
   - Do NOT discard any embedded links. Every `[Embedded Link: ...]` tag must be mapped somewhere.
   - **GitHub URL DISAMBIGUATION (very important)**: a *profile* URL like
     `https://github.com/<username>` (one path segment after the host)
     belongs in `github_url`. A *repository* URL like
     `https://github.com/<owner>/<repo>` (two path segments) is a real
     project and may attach to a `projects[].url`. NEVER paste the
     candidate's `github_url` as a project URL — even if the project
     entry is missing a link, leaving `url` empty is correct. The same
     rule applies to LinkedIn (`linkedin.com/in/<handle>` belongs to
     `linkedin_url`, not to projects).
6. Do NOT omit any information found in the CV. Extract ALL certifications, courses, and items — do not truncate lists.
7. **Summary extraction (CRITICAL — populate `normalized_summary`)**:
   - First, look for an existing summary section in the raw CV text:
     "Summary", "Profile", "About", "About Me", "Professional Summary",
     "Career Objective" (when written as a paragraph, not a one-line
     objective). Copy that paragraph verbatim into `normalized_summary`,
     trimmed to 2–4 sentences.
   - If no such section exists, GENERATE a 2-sentence summary combining:
     years of experience (computed from work_experience start/end dates
     when possible), top 2–3 most-emphasized skills, and the most-recent
     role title. Keep it factual — do not invent metrics or seniority
     claims the CV doesn't support.
   - Never leave `normalized_summary` empty unless the CV is completely
     devoid of work experience AND skills.

STRICT NORMALIZATION RULES (APPLY THESE FIRST):
- "Community Service" → "volunteer_experience" (list of objects with: organization, role, description, dates)
- "Volunteer Work" → "volunteer_experience"
- "Volunteering" → "volunteer_experience"
- "Honors & Awards" → "awards" (list of objects with: title, issuer, date, description)
- "Awards" → "awards"
- "Achievements" → "awards"
- "Speaking Engagements" → "speaking_engagements" (list of objects with: event, title, date, description)
- "Talks" → "speaking_engagements"
- "Presentations" → "speaking_engagements"
- "Research Publications" → "publications" (list of objects with: title, authors, journal, year, url)
- "Publications" → "publications"
- "Papers" → "publications"
- "Languages Spoken" → "languages" (list of strings: e.g., ["English (Native)", "Spanish (Fluent)"])
- "Languages" → "languages"
- "Military Service" → "military_experience" (list of objects with: branch, rank, dates, description)
- "Patents" → "patents" (list of objects with: title, patent_number, date, description)
- "Courses & Certifications" → split into `certifications` for completed credentials and `courses` for standalone courses. Include ALL items from EVERY issuer (e.g., DataCamp, Coursera, DEPI, etc.) — do not stop after the first few.

GENERAL RULES:
- ALL dynamic keys MUST be in snake_case (lowercase with underscores).
- If a section contains structured data (like awards with dates), use a list of objects.
- If a section is simple (like languages), use a list of strings.
- Dates should be in YYYY-MM format or YYYY if only year is available.

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent, add, or imply skills, keywords, achievements, metrics, job titles, or any other content not present in the original raw text.
- Only extract and restructure what actually exists.

=== SCHEMA MAPPING RULES (CRITICAL) ===
- For Work Experience and Projects: If the text contains bullet points or lists of achievements, you MUST put them in the `highlights` array field.
- Do NOT put arrays or bullet points into the `description` field. The `description` field should be used ONLY for a single short paragraph summarizing the role. If there is no summary paragraph, leave `description` null and put everything in `highlights`.
- For Experience: ALWAYS extract both `start_date` AND `end_date`. If the role is ongoing, set `end_date` to "Present". If specific end dates are not stated but the role clearly ended, infer "Present" only if it is the most recent role.
- For Projects: Extract `technologies` as a list of specific tools, frameworks, and languages used (e.g., ["PySpark", "Microsoft Fabric", "TensorFlow"]). Parse these from the project description bullets even if not in a dedicated "Technologies" line.
- For Projects: Extract `highlights` as key accomplishments or features, separate from the `description` bullets.

=== EDUCATION DATE RULES (CRITICAL) ===
- The `graduation_year` field means the END date (graduation or expected graduation), NOT the start date.
- If the CV shows a date range like "October 2022 – June 2026", `graduation_year` MUST be "June 2026" (the end/graduation date).
- Never put the enrollment/start date in `graduation_year`.
- Include the month if available (e.g., "June 2026" not just "2026").

=== SKILL PROFICIENCY INFERENCE ===
- Do NOT default all skills to "Intermediate". Infer proficiency from context:
  - "Expert" or "Advanced": Skills used professionally in multiple roles, or skills with certifications/deep projects.
  - "Intermediate": Skills used in one role or academic projects with meaningful output.
  - "Beginner": Skills only mentioned in coursework or briefly listed without evidence of practical use.
- If no context is available to infer proficiency, set proficiency to null rather than guessing.

=== LANGUAGE EXTRACTION (CRITICAL) ===
- The `languages` field is for HUMAN/SPOKEN languages ONLY (e.g., Arabic, English, French, Spanish).
- PROGRAMMING languages (Python, Java, C++, JavaScript, SQL, HTML/CSS, bash, assembly, etc.) MUST go in the `skills` field, NEVER in `languages`.
- If a CV section labeled "Languages" contains programming languages, those are skills — do not copy them into `languages`.
- If no spoken languages are explicitly listed, you may infer from context (location, university language, CV language) OR leave the list empty — do not fill it with programming languages.
- Format spoken languages as strings with proficiency: e.g., "Arabic (Native)", "English (Fluent)".

=== TYPO CORRECTION ===
- Fix obvious typos in job titles and section headers (e.g., "INFROMATION" → "INFORMATION").
- Do NOT change company names, proper nouns, or technical terms unless clearly misspelled.

=== EXPERIENCE VS TRAINING CLASSIFICATION ===
- `experiences` is for paid jobs, internships, and formal employment. NOT for courses, bootcamps, diplomas, or training programs.
- Training programs, diplomas, and course enrollments (e.g., "SOC analyst diploma", "Training in CyberSecurity at ICTHub") should be placed in `certifications` or `courses`, not `experiences`.
- Student status entries like "Undergraduate Student at University X" should go in `education` (as the degree entry), not `experiences`.
- If unsure, prefer `courses` over `experiences` for any entry that describes learning rather than work output.

=== REMOVE FROM EXTRACTED DATA ===
- Street/home address (extract city and country only)
- Objective statements (exclude them entirely)
- Graduation year if graduation date is more than 10 years ago
- Work experience older than 15 years (20 years max for executive roles)
- High school education
- GPA or university grades
- Headshot or photo references
- Salary expectations
- All soft skills (e.g., "teamwork", "detail-oriented"). Extract ONLY hard technical skills.
"""

def perform_reasonableness_check(data: Dict[str, Any]) -> None:
    if data.get('experiences') and not data.get('full_name'):
         pass
    if not data.get('normalized_summary') and data.get('experiences'):
        logger.warning("LLM failed to generate normalized_summary")
    if len(data.get('skills', [])) > 50:
         logger.warning("Suspiciously high skill count detected.")


# ─── Deterministic post-processing safety nets ───────────────────────────────
#
# The LLM prompt is instructed to enforce these invariants, but real-world
# Groq runs sometimes ignore the rules — especially the "GitHub repo URL is
# not a profile URL" disambiguation and the "always populate
# normalized_summary" instruction. Treat the LLM as a best-effort first
# pass and finalize the data here so the user-facing form always has a
# canonical github_url and a non-empty summary when the CV's text supports
# one.

import re as _re

_GITHUB_URL_RE = _re.compile(
    r'https?://(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9-]*)(?:/([^/?#\s]+))?',
    _re.IGNORECASE,
)
_LINKEDIN_URL_RE = _re.compile(
    r'https?://(?:www\.)?linkedin\.com/in/([A-Za-z0-9_-]+)(?:/[^/?#\s]*)*',
    _re.IGNORECASE,
)


def _canonical_github_profile_url(url: str) -> str:
    """Reduce any github.com URL to its canonical profile form
    (`https://github.com/<user>`). Repo URLs lose their /<repo> suffix.
    Returns "" when the URL isn't a github.com URL or has no extractable
    username."""
    if not url or not isinstance(url, str):
        return ''
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        return ''
    username = m.group(1)
    if not username:
        return ''
    return f'https://github.com/{username}'


def _canonical_linkedin_profile_url(url: str) -> str:
    """Same idea for LinkedIn — strip any deep-link path beyond /in/<handle>."""
    if not url or not isinstance(url, str):
        return ''
    m = _LINKEDIN_URL_RE.match(url.strip())
    if not m:
        return ''
    handle = m.group(1)
    if not handle:
        return ''
    return f'https://www.linkedin.com/in/{handle}'


def _is_github_profile_url(url: str) -> bool:
    """True iff the URL is a bare github.com profile URL (no repo path)."""
    if not url:
        return False
    m = _GITHUB_URL_RE.match(url.strip())
    return bool(m and m.group(1) and not m.group(2))


def _scrub_profile_urls_from_projects(data: Dict[str, Any]) -> None:
    """Remove the candidate's own github_url / linkedin_url from any
    `projects[].url`. The LLM occasionally pastes the header's profile
    link onto the first project when the project entry was missing a
    URL. Better to leave the project URL empty than to leak the profile
    link."""
    profile_github = (data.get('github_url') or '').strip().lower().rstrip('/')
    profile_linkedin = (data.get('linkedin_url') or '').strip().lower().rstrip('/')
    for proj in (data.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        url = (proj.get('url') or '').strip()
        if not url:
            continue
        norm = url.lower().rstrip('/')
        # Strip if it's literally the candidate's profile URL...
        if profile_github and norm == profile_github:
            proj['url'] = ''
            continue
        if profile_linkedin and norm == profile_linkedin:
            proj['url'] = ''
            continue
        # ...or if it's a github.com URL with no repo path (= a profile URL).
        if _is_github_profile_url(url):
            proj['url'] = ''


def _heuristic_summary_from_raw(raw_text: str) -> str:
    """Best-effort summary extraction when the LLM leaves it empty.

    Looks for a paragraph under any of: SUMMARY / PROFILE / ABOUT / ABOUT ME /
    CAREER OBJECTIVE / PROFESSIONAL SUMMARY headings, takes everything up to
    the next ALL-CAPS heading, and returns the first 2-4 sentences. Returns
    "" when no plausible section header is found."""
    if not raw_text:
        return ''
    # Match a heading line (mostly uppercase, optional surrounding decoration)
    # followed by paragraph text up to the next heading.
    headings = (
        'PROFESSIONAL SUMMARY', 'CAREER OBJECTIVE', 'CAREER SUMMARY',
        'SUMMARY', 'PROFILE', 'ABOUT ME', 'ABOUT',
    )
    pattern = _re.compile(
        r'^\s*(?:' + '|'.join(_re.escape(h) for h in headings) + r')\s*$\n+'
        r'(.+?)'
        r'(?=^\s*[A-Z][A-Z &/]{3,}\s*$|\Z)',
        _re.IGNORECASE | _re.MULTILINE | _re.DOTALL,
    )
    m = pattern.search(raw_text)
    if not m:
        return ''
    body = m.group(1).strip()
    # Strip embedded-link tags the parser leaves in raw text.
    body = _re.sub(r'\[Embedded Link:[^\]]*\]', '', body)
    body = _re.sub(r'\s+', ' ', body).strip()
    if not body:
        return ''
    # Cap at ~4 sentences / 600 chars so we don't dump a whole CV body
    # into the summary field.
    sentences = _re.split(r'(?<=[.!?])\s+', body)
    out = ' '.join(sentences[:4]).strip()
    return out[:600]


def _finalize_extraction(data: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    """Apply the deterministic safety nets after the LLM returns. Idempotent."""
    if not isinstance(data, dict):
        return data

    # 1. Canonicalize github_url / linkedin_url to profile form.
    if data.get('github_url'):
        canonical = _canonical_github_profile_url(data['github_url'])
        if canonical:
            data['github_url'] = canonical
    if data.get('linkedin_url'):
        canonical = _canonical_linkedin_profile_url(data['linkedin_url'])
        if canonical:
            data['linkedin_url'] = canonical

    # 2. Strip the candidate's profile URL from any project entry.
    _scrub_profile_urls_from_projects(data)

    # 3. Backfill normalized_summary when the LLM left it empty but the
    #    raw text plausibly has a summary section.
    if not (data.get('normalized_summary') or '').strip():
        recovered = _heuristic_summary_from_raw(raw_text)
        if recovered:
            data['normalized_summary'] = recovered
            logger.info(
                "Backfilled normalized_summary from raw CV text "
                "(%d chars).", len(recovered),
            )

    return data

def validate_and_map_cv_data(parsed_data: Dict[str, Any], raw_cv_text: str) -> Dict[str, Any]:
    """
    Uses LangChain + Groq with structured output to extract and validate CV data.
    Output is guaranteed to match ResumeSchema via Pydantic.
    """
    try:
        logger.info("Preparing LLM validation. Raw text length: %d", len(raw_cv_text))

        schema_definition = json.dumps(ResumeSchema.model_json_schema(), indent=2)

        # Build a slim hint from parsed_data — drop raw_text (already sent separately)
        # and empty values to reduce noise.
        hint_data = {k: v for k, v in parsed_data.items()
                     if k != 'raw_text' and v}

        prompt = f"""
{VALIDATION_SYSTEM_PROMPT}

Please extract the COMPLETE profile from this CV information.

TARGET JSON SCHEMA (use EXACT field names — e.g. `title` not `position`, `graduation_year` not `graduation_date`, `field` not `field_of_study`):
{schema_definition}

PARSED DATA (Use as hint/baseline — may have errors):
{json.dumps(hint_data, indent=2)}

RAW CV TEXT (Primary Source — this is authoritative):
{raw_cv_text[:100000]}

IMPORTANT: Do NOT truncate any list. Include every certification, course, project, and experience found in the text.
Use EXACT field names from the schema above.
"""
        
        structured_llm = get_structured_llm(ResumeSchema, temperature=0.1, max_tokens=8192, task="validator")
        result = structured_llm.invoke(prompt)

        # result is already a validated ResumeSchema instance
        final_data = result.model_dump()

        # Deterministic post-processing: canonicalize URLs, strip the
        # candidate's own profile URL from projects, and backfill the
        # summary heuristically if the LLM left it empty. The LLM prompt
        # asks for these invariants, but real runs occasionally violate
        # them — this layer guarantees the user-facing form is correct.
        final_data = _finalize_extraction(final_data, raw_cv_text)

        # Reasonableness check
        perform_reasonableness_check(final_data)

        logger.info(f"✓ LLM extraction successful. Extracted {len(final_data.get('skills', []))} skills")
        return final_data

    except Exception as e:
        logger.error(f"LLM extraction failed: {e}")
        # Apply the same safety nets to the parser's raw output so the
        # form-driven user gets canonical URLs even when the LLM is down.
        return _finalize_extraction(parsed_data, raw_cv_text)

def get_missing_fields(data: Dict[str, Any]) -> list:
    """Identify key fields missing from the extracted data."""
    required_fields = ['full_name', 'email', 'phone', 'skills', 'experiences', 'education']
    missing = []
    
    for field in required_fields:
        value = data.get(field)
        if not value:
            missing.append(field)
            
    return missing
