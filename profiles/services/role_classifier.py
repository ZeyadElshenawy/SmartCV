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

import hashlib
import logging
import re
import threading
from collections import OrderedDict
from typing import List

from pydantic import BaseModel, Field

from profiles.services.llm_engine import get_structured_llm


# ---------- In-process classify_for_jd memoization ----------
# One generate_resume_content_supervised call invokes classify_for_jd
# 3× with identical inputs (once in _build_standards_section, once per
# round in _build_v2_grounding). Each call fires 2 Groq calls. Cache
# the merged RoleClassification by a stable content hash so the 6
# redundant Groq calls collapse to 2.
#
# Bounded LRU eviction (max 64 entries) keeps long-lived processes
# from growing the cache unbounded. The cache key hashes only the
# classification-relevant slices of the profile (summary, headline,
# title, skills, experience titles) — not the whole 70k-char JSON —
# so identical profiles classify identically without hashing dead
# weight every call.
_CLASSIFY_CACHE_MAX = 64
_CLASSIFY_CACHE: "OrderedDict[str, RoleClassification]" = OrderedDict()
_CLASSIFY_CACHE_LOCK = threading.Lock()


def _classify_cache_key(profile_dict: dict, jd_text: str) -> str:
    p = profile_dict or {}
    exp_titles = '|'.join(
        str((e or {}).get('title', '')) for e in (p.get('experiences') or [])
    )
    pieces = [
        str(p.get('headline', '')),
        str(p.get('title', '')),
        str(p.get('professional_summary', '')),
        ','.join(sorted(str(s) for s in (p.get('skills') or []))),
        exp_titles,
        jd_text or '',
    ]
    return hashlib.sha1(
        '\n'.join(pieces).encode('utf-8', errors='ignore')
    ).hexdigest()


def clear_classify_cache() -> None:
    """Test helper — drop all memoized classifications."""
    with _CLASSIFY_CACHE_LOCK:
        _CLASSIFY_CACHE.clear()


def classify_cache_size() -> int:
    """Test helper — return current cache size."""
    with _CLASSIFY_CACHE_LOCK:
        return len(_CLASSIFY_CACHE)

logger = logging.getLogger(__name__)


class RoleClassification(BaseModel):
    """Single-call output: candidate's primary role, seniority, tech anchors,
    and the inferred region for KB faceting.

    PR2a — ``model_config = {"extra": "allow"}`` so ``classify_for_jd`` can
    stash the candidate's profile-derived role as an extra attribute
    (``profile_role``) alongside the JD-derived ``primary_role``. Declared
    field shapes are unchanged; downstream readers that only access
    declared fields are unaffected.
    """
    model_config = {"extra": "allow"}

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


# ---------- JD classifier (PR2a Fix 2) ----------

def classify_jd_role(jd_text: str) -> RoleClassification:
    """LLM call — classify the JOB DESCRIPTION (not the candidate).

    Mirrors ``detect_role_seniority`` but reframes the prompt around the
    JD: what role is the employer hiring for, what seniority do they
    state, what stack do they require. Returned object carries the same
    schema so downstream consumers (``classify_for_jd``,
    ``knowledge_retriever``) read it the same way.

    Region is left unset here — ``classify_for_jd`` overlays the
    deterministic ``infer_region`` result at the merge step.

    Fail-safe: if the LLM raises or returns an unparseable response,
    return a conservative default rather than blowing up the resume
    request. Downstream code expects a valid ``RoleClassification``.
    """
    fallback = RoleClassification(
        primary_role="Software Engineer",
        seniority="junior",
        tech_stack_signals=[],
        region="global",
    )
    if not (jd_text and jd_text.strip()):
        logger.warning(
            "classify_jd_role: empty JD text — returning fail-safe default."
        )
        return fallback
    try:
        llm = get_structured_llm(
            RoleClassification, temperature=0.1, max_tokens=400, task="jd_generator",
        )
        prompt = (
            "You classify job descriptions by the role being hired, the "
            "seniority level the employer is asking for, and the dominant "
            "tech-stack signals they list as requirements. The input is a "
            "JOB DESCRIPTION (an employer's posting), NOT a candidate "
            "profile. Pick the single best-fit role label that names what "
            "the hire will do day-to-day; pick the seniority bucket the JD "
            "states or implies; list 5–8 concrete tools / frameworks / "
            "languages the JD explicitly requires.\n\n"
            f"JOB DESCRIPTION:\n{jd_text}\n"
        )
        return llm.invoke(prompt)
    except Exception as exc:
        logger.warning(
            "classify_jd_role: LLM call failed (%s) — returning fail-safe default.",
            type(exc).__name__,
        )
        return fallback


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
    """Classify BOTH the candidate's profile AND the JD; return a merged
    ``RoleClassification`` driven by the JD.

    Two classifications, one merge:

    * ``detect_role_seniority(profile_summary)`` — what the candidate IS
      (used by downstream readers to help reframe existing experience for
      the target role; surfaced as the ``profile_role`` extra attribute).
    * ``classify_jd_role(jd_text)`` — what the candidate is APPLYING FOR.
      Its role + seniority drive the merged result because retrieval
      should anchor on the target audience, not the candidate's
      current self-classification.

    Tiebreaker policy:
      - If profile_role == jd_role: trivial; both agree on a single role.
      - Otherwise: **JD wins**. Knowledge retrieval is for content the
        candidate is applying for. A data-scientist profile applying to
        an AI-Developer role wants AI-Developer-shaped chunks; the
        profile-derived role is still surfaced (via ``profile_role`` on
        the returned object) so retrieval can union both pools.

    Both LLM calls are independently fail-safe — a failure in one path
    doesn't block the other, and both have local defaults so the resume
    request never raises out of this function.

    Region is set by the deterministic ``infer_region`` overlay on the
    JD text — neither LLM call is asked for region.

    Cached in-process by a content-hash of the classification-relevant
    slices of the profile + the JD text. Identical (profile, jd) →
    identical result. One supervised generation makes 3 calls with the
    same inputs; the cache collapses 6 Groq calls (2 per invocation)
    into 2 (first call only). Drop with ``clear_classify_cache()``.
    """
    cache_key = _classify_cache_key(profile_dict, jd_text or '')
    with _CLASSIFY_CACHE_LOCK:
        cached = _CLASSIFY_CACHE.get(cache_key)
        if cached is not None:
            # Move to MRU position.
            _CLASSIFY_CACHE.move_to_end(cache_key)
            logger.info(
                "classify_for_jd: cache hit -> primary_role=%r seniority=%r region=%r",
                cached.primary_role, cached.seniority, cached.region,
            )
            return cached
    # Profile-side classification (fail-safe).
    profile_summary = profile_summary_for_classifier(profile_dict or {})
    if profile_summary.strip():
        try:
            profile_cls = detect_role_seniority(profile_summary)
        except Exception as exc:
            logger.warning(
                "classify_for_jd: profile classifier failed (%s) — using default.",
                type(exc).__name__,
            )
            profile_cls = RoleClassification(
                primary_role="Software Engineer",
                seniority="mid",
                tech_stack_signals=[],
                region="global",
            )
    else:
        profile_cls = RoleClassification(
            primary_role="Software Engineer",
            seniority="mid",
            tech_stack_signals=[],
            region="global",
        )

    # JD-side classification (fail-safe internally).
    jd_cls = classify_jd_role(jd_text or "")

    # Merge: JD-derived role + seniority win the canonical slots; the
    # profile-derived role is surfaced as an extra attribute so
    # downstream code (retrieve_chunks) can union both pools.
    merged = RoleClassification(
        primary_role=jd_cls.primary_role,
        seniority=jd_cls.seniority,
        # Union the two stacks; preserve order, dedupe canonical-lower.
        tech_stack_signals=_dedupe_preserving(
            (jd_cls.tech_stack_signals or []) + (profile_cls.tech_stack_signals or [])
        )[:8],
        region=infer_region(jd_text or ""),
    )
    # `extra="allow"` lets us stash the candidate's profile-derived role
    # on the merged object without changing the declared schema. Readers
    # that need it use getattr(merged, 'profile_role', '').
    merged.profile_role = profile_cls.primary_role

    logger.info(
        "classify_for_jd: profile_role=%r jd_role=%r -> primary_role=%r "
        "seniority=%r region=%r",
        profile_cls.primary_role, jd_cls.primary_role,
        merged.primary_role, merged.seniority, merged.region,
    )
    with _CLASSIFY_CACHE_LOCK:
        _CLASSIFY_CACHE[cache_key] = merged
        _CLASSIFY_CACHE.move_to_end(cache_key)
        while len(_CLASSIFY_CACHE) > _CLASSIFY_CACHE_MAX:
            _CLASSIFY_CACHE.popitem(last=False)
    return merged


def _dedupe_preserving(items: list) -> list:
    """Order-preserving dedupe on case-insensitive equality. Used to
    union profile + JD tech-stack signals without creating duplicates
    like 'Python' / 'python'."""
    seen: set[str] = set()
    out: list = []
    for it in items:
        if not it:
            continue
        key = str(it).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
