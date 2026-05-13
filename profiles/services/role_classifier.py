"""Shared role/seniority classifier extracted from `benchmarks/jd_generator.py`
for use in the RAG retrieval flow.

Provides:
  - `RoleClassification` — Pydantic schema (with a new `region` field added
    for §3 retrieval flow facet routing).
  - `profile_summary_for_classifier` — compact text summary of a profile dict,
    fed to the role-detection LLM call.
  - `detect_role_seniority` — Groq LLM call that classifies a profile's
    primary role + seniority + dominant tech stack.
  - `infer_region` — deterministic, keyword-based heuristic that tags a JD
    as 'mena' when MENA city/country/job-board names appear, else 'global'.
  - `classify_for_jd` — convenience: summarize profile, call the classifier,
    overlay the region inferred from the JD. Returns a `RoleClassification`
    ready for the knowledge retriever's faceted filter.
"""
from __future__ import annotations

import re
from typing import List

from pydantic import BaseModel, Field

from profiles.services.llm_engine import get_structured_llm


class RoleClassification(BaseModel):
    """Single-call output: candidate's primary role, seniority, tech anchors,
    and the inferred region for KB faceting."""

    primary_role: str = Field(
        description=(
            "Single canonical role label. Examples: 'Backend Engineer', "
            "'Frontend Engineer', 'Mobile Engineer (Flutter)', 'Data Engineer', "
            "'Data Scientist', 'AI/ML Engineer', 'DevOps Engineer', "
            "'Full-Stack Engineer'. Be specific — pick the role the candidate's "
            "evidence most strongly supports."
        ),
    )
    seniority: str = Field(
        description=(
            "One of: 'intern', 'junior', 'mid', 'senior', 'lead'. "
            "Calibrate from years of experience and scope of work."
        ),
    )
    tech_stack_signals: List[str] = Field(
        description=(
            "5–8 dominant technologies detected in the CV (concrete tools, "
            "frameworks, languages). Used as cluster anchors for the JD generator."
        ),
    )
    region: str = Field(
        default="global",
        description=(
            "Geographic region — one of 'global', 'mena', 'us', 'eu'. "
            "Defaults to 'global'. Overridden by `infer_region` after the LLM "
            "call; the LLM itself is not asked to fill this."
        ),
    )


# ---------- Profile-to-text summary ----------

def profile_summary_for_classifier(profile: dict, max_chars: int = 2000) -> str:
    """Compact profile summary fed to the role-detection LLM call.

    Accepts a profile *dict* (typically `UserProfile.data_content`). Falls back
    to a slice of `raw_text` when structured sections are sparse.
    """
    parts = []
    if profile.get("full_name"):
        parts.append(f"Name: {profile['full_name']}")
    if profile.get("location"):
        parts.append(f"Location: {profile['location']}")

    skills = profile.get("skills") or []
    if isinstance(skills, list):
        skill_names = [s.get("name") if isinstance(s, dict) else str(s) for s in skills]
        skill_names = [s for s in skill_names if s]
        if skill_names:
            parts.append("Skills: " + ", ".join(skill_names[:40]))

    exps = profile.get("experiences") or []
    if isinstance(exps, list):
        exp_lines = []
        for e in exps[:5]:
            if not isinstance(e, dict):
                continue
            title = e.get("title") or e.get("position") or ""
            company = e.get("company") or ""
            if title or company:
                exp_lines.append(f"  - {title} at {company}".strip())
        if exp_lines:
            parts.append("Experience:")
            parts.extend(exp_lines)

    edu = profile.get("education") or []
    if isinstance(edu, list):
        for e in edu[:3]:
            if not isinstance(e, dict):
                continue
            deg = e.get("degree") or ""
            inst = e.get("institution") or ""
            if deg or inst:
                parts.append(f"Education: {deg}, {inst}".strip(", "))

    projects = profile.get("projects") or []
    if isinstance(projects, list) and projects:
        names = [p.get("name") if isinstance(p, dict) else str(p) for p in projects[:5]]
        names = [n for n in names if n]
        if names:
            parts.append("Projects: " + ", ".join(names))

    structured = "\n".join(parts)

    # Fallback: if structured profile is too thin (parser missed sections),
    # append a slice of raw_text so the LLM still has signal.
    raw = (profile.get("raw_text") or "").strip()
    if len(structured) < 400 and raw:
        budget = max_chars - len(structured) - 50
        if budget > 0:
            structured += "\n\nRAW CV TEXT (truncated):\n" + raw[:budget]
    elif raw and len(structured) < max_chars - 600:
        structured += "\n\nRAW CV TEXT EXCERPT:\n" + raw[:500]

    return structured[:max_chars]


# Back-compat alias — `benchmarks/jd_generator.py` historically imported
# this under its private name. Keep the alias so older call sites still work.
_profile_summary_for_llm = profile_summary_for_classifier


# ---------- LLM classifier ----------

def detect_role_seniority(profile_summary: str) -> RoleClassification:
    """LLM call — classify the candidate's role + seniority + tech stack.

    Region is NOT asked from the LLM here; it's overlaid post-hoc by
    `infer_region` against the JD text (deterministic / cheaper).
    """
    llm = get_structured_llm(RoleClassification, temperature=0.1, max_tokens=400, task="jd_generator")
    prompt = (
        "You classify candidate profiles by primary role and seniority. "
        "Read the profile below and pick the most-supported role label, the "
        "seniority bucket, and 5–8 dominant tech-stack signals. Anchor your "
        "answer on the strongest cluster of evidence — do not pick a role from "
        "a single buzzword.\n\n"
        f"PROFILE:\n{profile_summary}\n"
    )
    return llm.invoke(prompt)


# ---------- Region inference ----------

# Word-boundary patterns. MENA detection is deliberately keyword-based (not
# LLM) to keep it deterministic and free — region is a single facet, not
# worth burning a Groq call per resume request.
_MENA_PATTERNS = re.compile(
    r"\b("
    r"egypt|cairo|alexandria|giza|"
    r"saudi|ksa|riyadh|jeddah|dammam|mecca|medina|"
    r"uae|emirates|dubai|abu\s*dhabi|sharjah|"
    r"qatar|doha|kuwait|bahrain|manama|oman|muscat|"
    r"jordan|amman|lebanon|beirut|"
    r"morocco|casablanca|rabat|algeria|algiers|tunisia|tunis|"
    r"wuzzuf|bayt\.com|bayt|naukrigulf|laimoon|forasna"
    r")\b",
    flags=re.IGNORECASE,
)

_US_PATTERNS = re.compile(
    r"\b(united\s+states|usa|u\.s\.|new\s+york|san\s+francisco|silicon\s+valley|"
    r"seattle|boston|austin|chicago|los\s+angeles)\b",
    flags=re.IGNORECASE,
)

_EU_PATTERNS = re.compile(
    r"\b(european\s+union|berlin|munich|amsterdam|paris|madrid|barcelona|"
    r"london|dublin|stockholm|copenhagen|warsaw|prague|vienna|zurich)\b",
    flags=re.IGNORECASE,
)


def infer_region(text: str) -> str:
    """Deterministic region tag for the KB facet filter.

    MENA wins if any MENA keyword appears (defensible original-research region
    is the priority signal). Otherwise US, EU, else 'global'.
    """
    if not text:
        return "global"
    if _MENA_PATTERNS.search(text):
        return "mena"
    if _US_PATTERNS.search(text):
        return "us"
    if _EU_PATTERNS.search(text):
        return "eu"
    return "global"


# ---------- Convenience: one call from a request handler ----------

def classify_for_jd(profile_dict: dict, jd_text: str) -> RoleClassification:
    """Summarize a profile, run the LLM classifier, overlay region from JD.

    This is the single entry point used by `resumes/services/resume_generator.py`
    when `RAG_ENABLED=True`. Tolerates a missing/empty profile by returning a
    minimal classification rather than blowing up the resume request.
    """
    summary = profile_summary_for_classifier(profile_dict or {})
    if not summary.strip():
        return RoleClassification(
            primary_role="Software Engineer",
            seniority="mid",
            tech_stack_signals=[],
            region=infer_region(jd_text or ""),
        )
    cls = detect_role_seniority(summary)
    cls.region = infer_region(jd_text or "")
    return cls
