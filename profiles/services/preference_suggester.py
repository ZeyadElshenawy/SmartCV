"""LLM-driven auto-fill for the JobPreferences form.

Reads everything the profile knows about the user — parsed CV (skills,
experiences, education, projects, summary), profile.location, and the
github/linkedin/scholar/kaggle signal blobs — and asks the LLM to suggest
keyword + locations + experience_levels + workplace_types. Returns a dict
the front-end can drop straight into the form.

Sources / date_posted / max_jobs are NOT suggested — those are user policy,
not profile-derived, and the form's defaults already cover them.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import KeywordCandidate, SuggestedJobPreferences


logger = logging.getLogger(__name__)


VALID_EXPERIENCE_LEVELS = {"internship", "entry", "associate", "mid_senior", "director", "executive"}
VALID_WORKPLACE_TYPES = {"onsite", "remote", "hybrid"}

# Seniority / level tokens that don't belong in a job-board search keyword
# — they're captured by experience_levels instead. Stripped at whole-word
# boundaries so "Internet" survives "Internship".
_SENIORITY_TOKENS = {
    "junior", "jr", "senior", "sr", "lead", "staff", "principal",
    "intern", "interns", "internship", "trainee", "graduate", "grad",
    "entry", "entry-level", "entry level", "mid", "mid-level", "mid level",
    "associate",
}


def _clean_keyword(raw: str) -> str:
    """Normalise an LLM-suggested keyword into a job-board search phrase.

    Strips parens/brackets, seniority words, redundant whitespace, and caps
    length. Empty output means caller should fall back to a profile-derived
    default (most-recent role title)."""
    import re as _re
    if not raw:
        return ""

    # Drop anything in parens or brackets (e.g. "(IoT)", "[Remote]").
    s = _re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", raw)

    # Drop trailing slash-separated alternatives ("Engineer / Developer").
    s = s.split("/")[0]

    # Replace punctuation with spaces, then collapse whitespace.
    s = _re.sub(r"[,;:|]+", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()

    # Tokenise + strip seniority words at word-boundary level.
    tokens = []
    for tok in s.split(" "):
        if not tok:
            continue
        if tok.lower().strip("-") in _SENIORITY_TOKENS:
            continue
        tokens.append(tok)

    # Cap at 4 words — anything longer is over-padded for a search box.
    tokens = tokens[:4]
    cleaned = " ".join(tokens).strip(" -.,:;")
    return cleaned


def _build_profile_summary(profile) -> Dict[str, Any]:
    """Compact profile snapshot the LLM can reason over without going long.

    Trim to the things relevant for matching intent: skills, recent role
    titles, summary, location, plus thin slices of the enrichment signals.
    Includes project titles + technologies so we surface what the user
    actually BUILT, not just what their CV header says.
    """
    data = profile.data_content or {}
    skills_list = []
    for s in (data.get("skills") or [])[:30]:
        if isinstance(s, dict):
            name = s.get("name") or s.get("skill") or ""
        else:
            name = str(s or "")
        if name:
            skills_list.append(name)

    experiences = []
    for exp in (data.get("experiences") or [])[:5]:
        if isinstance(exp, dict):
            experiences.append({
                "title": exp.get("title") or exp.get("role") or "",
                "company": exp.get("company") or "",
                "start": exp.get("start_date") or exp.get("startDate") or "",
                "end": exp.get("end_date") or exp.get("endDate") or "",
                "location": exp.get("location") or "",
            })

    projects = []
    for proj in (data.get("projects") or [])[:6]:
        if isinstance(proj, dict):
            techs = proj.get("technologies") or proj.get("tech_stack") or proj.get("tags") or []
            if isinstance(techs, str):
                techs = [techs]
            projects.append({
                "name": proj.get("name") or proj.get("title") or "",
                "technologies": list(techs)[:8],
            })

    summary = (data.get("normalized_summary") or data.get("summary") or "")[:600]

    github = data.get("github_signals") or {}
    raw_langs = github.get("language_breakdown") or github.get("languages") or []
    if isinstance(raw_langs, dict):
        languages = list(raw_langs.keys())[:6]
    elif isinstance(raw_langs, list):
        languages = []
        for item in raw_langs[:6]:
            if isinstance(item, str):
                languages.append(item)
            elif isinstance(item, dict):
                name = item.get("language") or item.get("name") or ""
                if name:
                    languages.append(name)
    else:
        languages = []
    github_compact = {
        "languages": languages,
        "top_repos": [r.get("name") for r in (github.get("top_repos") or [])[:5] if isinstance(r, dict)],
    }

    linkedin = data.get("linkedin_signals") or {}
    linkedin_compact = {
        "headline": linkedin.get("headline") or "",
        "current_position": linkedin.get("current_position") or "",
    }

    return {
        "location": getattr(profile, "location", "") or "",
        "summary": summary,
        "skills": skills_list,
        "experiences": experiences,
        "projects": projects,
        "github_signals": github_compact,
        "linkedin_signals": linkedin_compact,
    }


def _coerce_suggestion(raw: SuggestedJobPreferences, profile_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Defensive normalisation — clamp enum values, dedupe, fall back when blank."""
    # Build candidate list first (the LLM's primary output now).
    candidates: list[dict[str, str]] = []
    seen_keywords: set[str] = set()
    for cand in (raw.keyword_candidates or []):
        kw = _clean_keyword(getattr(cand, "keyword", "") or "")
        if not kw:
            continue
        kw_key = kw.lower()
        if kw_key in seen_keywords:
            continue
        seen_keywords.add(kw_key)
        why = (getattr(cand, "why", "") or "").strip()
        candidates.append({"keyword": kw, "why": why})
    candidates = candidates[:5]

    # Top keyword falls through: explicit raw.keyword -> first candidate -> most-recent role.
    keyword = _clean_keyword(raw.keyword or "")
    if not keyword and candidates:
        keyword = candidates[0]["keyword"]
    if not keyword:
        exps = profile_summary.get("experiences") or []
        fallback_title = (exps[0].get("title") if exps else "") or ""
        keyword = _clean_keyword(fallback_title)
    # Make sure the top keyword is also present in candidates (front of list).
    if keyword and not any(c["keyword"].lower() == keyword.lower() for c in candidates):
        candidates.insert(0, {"keyword": keyword, "why": ""})
        candidates = candidates[:5]

    locations = []
    seen = set()
    for loc in raw.locations or []:
        loc = (loc or "").strip()
        if loc and loc.lower() not in seen:
            seen.add(loc.lower())
            locations.append(loc)
    if not locations:
        loc = profile_summary.get("location") or ""
        locations = [loc] if loc else ["Remote"]

    levels = [e for e in (raw.experience_levels or []) if e in VALID_EXPERIENCE_LEVELS]
    if not levels:
        # Heuristic fallback when the LLM produced nothing valid.
        years = _estimated_years_of_experience(profile_summary.get("experiences") or [])
        if years < 0.5:
            levels = ["internship", "entry"]
        elif years < 2.5:
            levels = ["entry", "associate"]
        elif years < 5:
            levels = ["associate", "mid_senior"]
        elif years < 9:
            levels = ["mid_senior"]
        else:
            levels = ["mid_senior", "director"]

    wtypes = [w for w in (raw.workplace_types or []) if w in VALID_WORKPLACE_TYPES]
    if not wtypes:
        # If the LLM passed nothing, allow all three so the search isn't over-narrow.
        wtypes = ["onsite", "remote", "hybrid"]

    return {
        "keyword": keyword,
        "keyword_candidates": candidates,
        "locations": locations[:4],
        "experience_levels": levels,
        "workplace_types": wtypes,
        "rationale": (raw.rationale or "").strip(),
    }


def _estimated_years_of_experience(experiences) -> float:
    """Cheap-and-dirty YOE estimate from start/end strings. Returns total years."""
    from datetime import date
    import re as _re
    total_months = 0
    today = date.today()
    for exp in experiences:
        start = exp.get("start") or ""
        end = exp.get("end") or "" or "present"
        sm = _parse_year_month(start)
        em = _parse_year_month(end) if end and end.lower() not in ("", "present", "current", "now") else (today.year, today.month)
        if sm and em:
            months = (em[0] - sm[0]) * 12 + (em[1] - sm[1])
            if months > 0:
                total_months += months
    return total_months / 12.0


def _parse_year_month(s: str):
    """Best-effort '2024-06' / 'Jun 2024' / '2024' -> (year, month). Returns None on miss."""
    import re as _re
    if not s:
        return None
    s = s.strip()
    m = _re.match(r"^(\d{4})[-/](\d{1,2})", s)
    if m:
        return int(m.group(1)), max(1, min(12, int(m.group(2))))
    m = _re.match(r"^([A-Za-z]+)\s+(\d{4})$", s)
    if m:
        months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        try:
            return int(m.group(2)), months.index(m.group(1).lower()[:3]) + 1
        except ValueError:
            return None
    m = _re.match(r"^(\d{4})$", s)
    if m:
        return int(m.group(1)), 1
    return None


def suggest_job_preferences(profile) -> Dict[str, Any]:
    """Return suggested keyword/locations/experience_levels/workplace_types for the user.

    Pure read — does not mutate JobPreferences. The view layer pre-fills the
    form and lets the user tweak before saving.
    """
    summary = _build_profile_summary(profile)

    prompt = f"""You are helping a job seeker pick search filters for an automated job-board scan.

USER PROFILE SNAPSHOT (JSON):
{json.dumps(summary, indent=2, default=str)}

YOUR TASK:
Return JSON matching the schema. The user can override anything you suggest.

=== HOW TO PICK keyword_candidates (CRITICAL) ===

Return 3 to 5 candidate roles that best fit this user. Order them by fit. Each is a GENERIC job-board search keyword (1-3 words, max 4). The first item is your top pick — also copy it into the `keyword` field for backward compatibility.

ANCHORING RULES — read carefully, these are where suggestions usually go wrong:

1. **Anchor on the dominant SKILL CLUSTER + what they BUILT (projects)**, not on isolated buzzwords from their summary or one-off keywords on their CV.
   - If `skills` and `projects` are dominated by mobile (Flutter, React Native, Swift, Kotlin, Dart, Android, iOS), the candidates should be mobile-shaped: "Mobile Developer", "Flutter Developer", "Android Developer", "iOS Developer", "Cross-Platform Developer".
   - If `skills` and `projects` are dominated by ML/data (Python, scikit-learn, PyTorch, TensorFlow, pandas, Kaggle, notebooks), the candidates should be data/ML-shaped: "Data Scientist", "Machine Learning Engineer", "ML Engineer", "Data Analyst", "AI Engineer".
   - If they're dominated by web backend (Django, Flask, Node, Spring, Go, FastAPI, REST, SQL), suggest "Backend Engineer", "Software Engineer", "API Engineer", "Full Stack Engineer".
   - If embedded/firmware (C, C++, RTOS, microcontrollers, ESP32, Arduino, hardware) THEN consider "Embedded Engineer", "IoT Engineer", "Firmware Engineer".

2. **DO NOT pick a buzzword from a single project or one summary phrase and pretend it's the role.**
   - If the user has ONE IoT-themed project but FIVE Flutter projects, "IoT Engineer" is wrong. Suggest mobile/Flutter roles.
   - If the summary mentions "digital transformation" once but the skills are pandas/Jupyter/scikit-learn, "Digital Transformation Consultant" is wrong. Suggest "Data Scientist" / "Data Analyst" / "ML Engineer".
   - If the experience header literally says "Data Scientist" or "Backend Engineer", that's a strong anchor — TRUST it unless skills/projects clearly contradict.

3. **Variety across candidates**: each of your 3-5 candidates should be a DIFFERENT angle, not synonyms. Good variety: "Mobile Developer" / "Flutter Developer" / "Frontend Engineer" / "Cross-Platform Developer" / "Software Engineer". Bad variety: "Mobile Developer" / "Mobile Engineer" / "Mobile Software Developer" (those are synonyms).

4. **Forbidden in keyword**: parentheticals like "(IoT)", seniority words (Junior/Senior/Lead/Staff/Principal/Intern/Internship/Trainee), boolean operators ("or", "/", "&"), company-specific phrasing, full tech-stack listings ("Backend Python Django Engineer"). The user's seniority goes in experience_levels — NOT in keyword.

5. **`why` field for each candidate**: ONE short phrase under 12 words, anchored on EVIDENCE from the profile. Good: "matches your Flutter projects + Dart skill". Bad: "good fit for your background" (too generic — not anchored on evidence).

=== OTHER FIELDS ===

- locations: 2-3 places they could plausibly target. Include their current city if known. Add "Remote" if their experience suggests remote-friendly work (tech, software, data). For students/juniors, prefer broader regions.
- experience_levels: choose from internship / entry / associate / mid_senior / director / executive. Estimate from total years of experience. Include CURRENT band PLUS one above. For <1 year experience, return ["internship", "entry"].
- workplace_types: subset of onsite / remote / hybrid. Default to all three when unsure.
- rationale: ONE short sentence (under 25 words) summarising what cluster you anchored on (e.g. "Anchored on your mobile cluster — 5 Flutter projects + Dart/Android skills."). The user reads this.

Be CONSERVATIVE. When uncertain, lean toward broader keywords ("Software Engineer") over narrower guesses.
"""

    try:
        llm = get_structured_llm(SuggestedJobPreferences, temperature=0.2, max_tokens=600)
        raw = llm.invoke(prompt)
    except Exception:
        logger.exception("LLM preference suggestion failed; returning heuristic fallback")
        raw = SuggestedJobPreferences()

    return _coerce_suggestion(raw, summary)
