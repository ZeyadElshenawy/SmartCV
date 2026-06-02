import logging
import re

from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator
from typing import List, Optional, Any, Dict, Union
from pydantic import ConfigDict


# ============================================================
# CV / Profile Schemas
# ============================================================

class Skill(BaseModel):
    name: str
    proficiency: Optional[str] = Field(None, description="Beginner, Intermediate, Advanced, Expert")
    years: Optional[float] = None

class Experience(BaseModel):
    """CV-parser experience entry. Bullets canonical in ``description``
    (List[str]).

    PROMPT/SCHEMA COUPLING (PR 3b.1 — 2026-05-19):
    ``extra='forbid'`` on a Pydantic schema handed to Groq becomes
    ``additionalProperties: false`` in the tool-call JSON schema.
    Groq's server-side validator enforces it BEFORE the LLM response
    reaches Python — so the Python-side ``_fold_into_description``
    validator can't canonicalize what Groq already rejected. Strict
    Pydantic-side rejection therefore requires the prompt to enforce
    the same restriction at generation time. PR 3b.1 (this PR) paired
    the schema tightening with a CV-parser prompt update; see
    ``VALIDATION_SYSTEM_PROMPT`` in ``llm_validator.py`` for the
    matching "DO NOT INVENT FIELDS" + "POST-PARSE FIELDS" blocks.

    Historical context: pre-PR-3b the model had THREE declared bullet
    fields (description, highlights, achievements) admitted under
    ``extra='allow'`` silently. The dual/triple-field shape was the
    same audit-thread trap PR 3a fixed for resume-output. PR 3b
    introduced the alias fold + 6 promoted fields; PR 3b's hotfix
    (4769442) relaxed strictness when production CV upload broke;
    PR 3b.1 (now) restores strictness paired with prompt enforcement.

    The validator's input-side fold remains as defense-in-depth
    against model variance — known aliases still get folded into
    ``description`` even when the prompt's "do not invent" rule is
    being followed.
    """
    title: str
    company: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    # Explicit "currently in this role" signal — single source of truth.
    # True ONLY when the source explicitly states ongoing / current /
    # present / now / "till date". A missing end_date is NOT "current";
    # legacy records may carry end_date="Present" without is_current=True
    # (LLM-fabricated) and downstream code treats that as unknown.
    is_current: Optional[bool] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    # Provenance: 'cv' / 'linkedin' / etc. SIGNAL ONLY — not output
    # as a resume field. Promoted from extra='allow' in PR 3b.
    # Per the CV-parser prompt's POST-PARSE FIELDS section, the LLM
    # is instructed not to populate this field; downstream enrichment
    # tags it.
    source: Optional[str] = None
    # 'Full-time' / 'part-time' / 'contract' / 'internship'.
    # Promoted from extra='allow' in PR 3b.
    employment_type: Optional[str] = None
    description: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='before')
    @classmethod
    def coerce_to_canonical(cls, data):
        if not isinstance(data, dict):
            return data
        return _fold_into_description(data)

class Education(BaseModel):
    degree: str
    institution: str
    graduation_year: Optional[str] = None
    field: Optional[str] = None
    gpa: Optional[str] = None
    honors: Optional[List[str]] = Field(default_factory=list)
    location: Optional[str] = None
    model_config = ConfigDict(extra='allow')

class Project(BaseModel):
    """CV-parser project entry. Same canonical pattern as
    :class:`Experience` — see that class for the PR-3b.1 prompt/schema
    coupling rationale.

    Preserves ``role`` as a semantically distinct field (not bullets).
    Promotes four previously-silent extras to explicit fields:
    ``source``, ``source_id``, ``pushed_at``, ``date``. The CV-parser
    prompt instructs the LLM to populate only ``date`` — the other
    three are post-parse enrichment fields (project_enricher writes
    them).
    """
    name: str
    role: Optional[str] = None
    url: Optional[str] = None
    technologies: Optional[List[str]] = Field(default_factory=list)
    # Provenance: 'github' / 'scholar' / 'kaggle' / 'linkedin' / 'cv'.
    # Marks enriched projects as ground truth. SIGNAL ONLY — not output
    # as a resume field. The LLM is instructed via the prompt's
    # POST-PARSE FIELDS section to leave this absent; enrichment tags it.
    source: Optional[str] = None
    # Identifier within the source system: GitHub repo full_name,
    # Scholar paper slug, Kaggle dataset ID. Companion to source.
    # Post-parse only.
    source_id: Optional[str] = None
    # GitHub-API timestamp used by sort_projects_newest_first.
    # Post-parse only.
    pushed_at: Optional[str] = None
    # Project date as declared in the CV (e.g., '2024'). LLM-extractable
    # when the CV mentions one.
    date: Optional[str] = None
    description: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='before')
    @classmethod
    def coerce_to_canonical(cls, data):
        if not isinstance(data, dict):
            return data
        return _fold_into_description(data)

class Certification(BaseModel):
    name: str
    issuer: Optional[str] = None
    date: Optional[str] = None
    duration: Optional[str] = None
    url: Optional[str] = None

class ItemDetailed(BaseModel):
    title: str = ""
    organization: Optional[str] = None
    date: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None

class ResumeSchema(BaseModel):
    # Core identifying info
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    other_urls: Optional[List[str]] = Field(default_factory=list)
    
    # Generated content
    normalized_summary: Optional[str] = None
    objective: Optional[str] = None

    # Required structured lists
    skills: Optional[List[Skill]] = Field(default_factory=list)
    experiences: Optional[List[Experience]] = Field(default_factory=list)
    education: Optional[List[Education]] = Field(default_factory=list)
    
    # Optional structured lists
    projects: Optional[List[Project]] = Field(default_factory=list)
    certifications: Optional[List[Certification]] = Field(default_factory=list)
    
    # New Extended Fields for detailed extractions
    languages: Optional[List[str]] = Field(default_factory=list)
    volunteer_experience: Optional[List[ItemDetailed]] = Field(default_factory=list)
    awards: Optional[List[ItemDetailed]] = Field(default_factory=list)
    publications: Optional[List[ItemDetailed]] = Field(default_factory=list)
    speaking_engagements: Optional[List[ItemDetailed]] = Field(default_factory=list)
    patents: Optional[List[ItemDetailed]] = Field(default_factory=list)
    military_experience: Optional[List[ItemDetailed]] = Field(default_factory=list)
    hobbies: Optional[List[str]] = Field(default_factory=list)
    references: Optional[List[ItemDetailed]] = Field(default_factory=list)
    courses: Optional[List[ItemDetailed]] = Field(default_factory=list)
    
    # Allow extra fields generated by LLM dynamically
    model_config = {
        "extra": "allow"
    }


# ============================================================
# LLM Output Schemas (used by get_structured_llm)
# ============================================================

class GapAnalysisResult(BaseModel):
    """Legacy flat-list gap analysis output (kept for backward compat with
    benchmarks + any caller that still wants the simple shape). New code
    should use TieredGapAnalysisResult below."""
    critical_missing_skills: List[str] = Field(default_factory=list, description="Hard technical skills the user clearly lacks")
    soft_skill_gaps: List[str] = Field(default_factory=list, description="Soft skills gaps if required and missing")
    matched_skills: List[str] = Field(default_factory=list, description="Skills the user has that match requirements")
    similarity_score: float = Field(default=0.5, description="Overall match score from 0.0 to 1.0 based on skills, experience relevance, and seniority fit")


# --------------------------------------------------------------------------
# Tier-aware gap analysis (v2, 2026-05-14)
# --------------------------------------------------------------------------

class MatchedSkill(BaseModel):
    """A JD-required skill the candidate clearly has.

    The evidence_quote is the verbatim phrase from the profile that proves
    it — surfaced in the chip's hover tooltip on the gap page. String
    fields use Optional + field_validator coercion (rather than max_length=N
    on Field) because Groq's tool-call API enforces max_length BEFORE
    Pydantic gets the response — a 153-char quote there breaks the whole
    structured call. We accept whatever the LLM emits, coerce null → "",
    and truncate in Python.
    """
    name: str = Field(description="Skill name, copied verbatim from the JD's tier list.")
    evidence_source: Optional[str] = Field(
        default="",
        description=(
            "Where the proof was found. One of: 'skills', 'experience', "
            "'projects', 'certifications', 'github', 'scholar', 'kaggle', "
            "'education', 'multiple'. Empty string when no single source "
            "stands out. Pick the strongest single source when possible."
        ),
    )
    evidence_quote: Optional[str] = Field(
        default="",
        description=(
            "Short verbatim quote from the profile proving the match. Target "
            "≤140 chars; longer responses get truncated client-side. Empty "
            "string when no specific quote applies."
        ),
    )

    @field_validator("evidence_source", "evidence_quote", mode="before")
    @classmethod
    def _coerce_str(cls, v):
        if v is None:
            return ""
        s = str(v).strip()
        return s[:140]


class MissingSkill(BaseModel):
    """A JD-required skill the candidate does NOT have, with a proximity score
    indicating how close they are based on adjacent evidence.

    proximity is strictly less than 1.0 — a skill at 1.0 belongs in
    matched_*, not missing_*. The Pydantic validator below enforces this; the
    prompt also tells the LLM to obey it, and gap_analyzer retries once when
    a 1.0 leaks through.

    String fields are Optional with `_coerce_str` rather than `max_length`
    constraints because Groq's tool-call API rejects over-long strings BEFORE
    Pydantic sees them. Truncation happens in the validator.
    """
    name: str = Field(description="Skill name, copied verbatim from the JD's tier list.")
    source_quote: Optional[str] = Field(
        default="",
        description=(
            "Short JD sentence that asked for this skill. Target ≤140 chars; "
            "truncated client-side if longer. Empty string acceptable."
        ),
    )
    proximity: float = Field(
        default=0.0,
        ge=0.0,
        lt=1.0,
        description=(
            "How close the candidate is to having this skill on [0.0, 1.0). "
            "Anchor scale: 0.0 no related evidence; 0.2 vaguely adjacent "
            "domain; 0.4 one adjacent skill present; 0.6 multiple adjacent "
            "OR coursework-level; 0.8 exact skill mentioned but thin evidence "
            "OR lower seniority than asked. 1.0 is FORBIDDEN — those belong "
            "in matched_*."
        ),
    )
    proximity_reason: Optional[str] = Field(
        default="",
        description=(
            "Human reason for the proximity score. Target ≤120 chars; "
            "truncated client-side if longer."
        ),
    )
    bridge_hint: Optional[str] = Field(
        default=None,
        description=(
            "Optional concrete next step the candidate could take to close "
            "this gap. Target ≤140 chars. Omit (use null) when you have "
            "nothing concrete — do not invent generic advice."
        ),
    )

    @field_validator("source_quote", "proximity_reason", mode="before")
    @classmethod
    def _coerce_str_140(cls, v):
        if v is None:
            return ""
        s = str(v).strip()
        return s[:140]

    @field_validator("bridge_hint", mode="before")
    @classmethod
    def _coerce_optional_140(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return s[:140]

    @field_validator("proximity")
    @classmethod
    def reject_one(cls, v: float) -> float:
        # The Field(lt=1.0) constraint already enforces this; we add an
        # explicit message so the retry loop in gap_analyzer can show the
        # LLM exactly why we rejected the value.
        if v >= 1.0:
            raise ValueError(
                "Skills with proximity 1.0 must be in matched_must_have or "
                "matched_nice_to_have, not missing_*. Re-route the skill or "
                "lower the proximity (0.8 = exact skill present but thin "
                "evidence)."
            )
        return v


class TieredGapAnalysisResult(BaseModel):
    """Output schema for gap_analyzer.compute_gap_analysis v2.

    Four tier-split lists drive the three on-screen columns:
        UI MATCHED column      = matched_must_have + matched_nice_to_have
                                  (must-haves rendered with a ★)
        UI CRITICAL MISSING    = missing_must_have
        UI SOFT GAPS           = missing_nice_to_have

    soft_skill_gaps captures non-skill observations (seniority gap, career
    transition risk) that aren't a single skill — surfaced as a banner /
    side-note, not a draggable chip.
    """
    matched_must_have: List[MatchedSkill] = Field(default_factory=list)
    matched_nice_to_have: List[MatchedSkill] = Field(default_factory=list)
    missing_must_have: List[MissingSkill] = Field(default_factory=list)
    missing_nice_to_have: List[MissingSkill] = Field(default_factory=list)
    soft_skill_gaps: List[str] = Field(
        default_factory=list,
        description=(
            "Free-text observations about seniority / career transition / "
            "non-skill fit signals. NOT skill names. ≤20 words each."
        ),
    )

class SkillListResult(BaseModel):
    """Output schema for skill_extractor.py — flat list (kept for backward compat)."""
    skills: List[str] = Field(default_factory=list, description="List of extracted skill names")


class JobExtractionResult(BaseModel):
    """Unified output of jobs.services.skill_extractor.extract_job_info.

    The LLM splits skills into Must-Have vs Nice-to-Have using the JD's section
    cues ("Required" / "Must-have" / "Responsibilities" → must_have;
    "Nice to have" / "Desirable" / "Bonus" / "Plus" → nice_to_have). The flat
    `Job.extracted_skills` field downstream is the deduped union of both.
    `domain` is a short noun phrase (canonicalized post-hoc against an alias
    map) capturing the industry the role serves.
    """
    must_have_skills: List[str] = Field(
        default_factory=list,
        description="Skills the JD lists as required / must-have / core responsibilities.",
    )
    nice_to_have_skills: List[str] = Field(
        default_factory=list,
        description="Skills the JD lists as nice-to-have / desirable / bonus / plus.",
    )
    domain: str = Field(
        default="",
        description=(
            "Industry domain inferred from the JD (free text, canonicalized "
            "post-hoc). Examples: 'Financial Services', 'Healthcare', "
            "'E-commerce', 'Gaming'. Empty when no clear signal."
        ),
    )


class ExtractedExperienceBullet(BaseModel):
    """A generated resume bullet based on the user's conversational reply."""
    company_or_project_name: str = Field(description="The name of the company or project this bullet applies to (must match an existing one if possible, or 'General')")
    bullet_point: str = Field(description="A concise, STAR-format bullet point (max 120 chars) summarizing the achievement or skill application.")

class ChatReplyAnalysis(BaseModel):
    """Sub-schema for interviewer.py — analysis of user's reply"""
    is_valid: bool = True
    quality_score: int = Field(default=5, description="0-10 quality score")
    clarification_prompt: str = Field(default="", description="Ask for more details if is_valid is false")
    skills_to_add: List[Skill] = Field(default_factory=list)
    all_technologies_mentioned: List[str] = Field(default_factory=list)
    new_experience_bullets: List[ExtractedExperienceBullet] = Field(default_factory=list, description="Extract a formal resume bullet ONLY if the user described an actionable achievement. Max 1 item.")

class ChatNextQuestion(BaseModel):
    """Sub-schema for interviewer.py — next question generation"""
    question: str = Field(default="Tell me about your background.", description="Next conversational message. Include a brief acknowledgment of the user's answer, then naturally ask about the next skill. 2-4 sentences, max 60 words. Use **bold** for skill names.")
    topic_skill: str = Field(default="general", description="The skill being asked about")

class ChatTurnResult(BaseModel):
    """Output schema for interviewer.py"""
    reply_analysis: ChatReplyAnalysis = Field(default_factory=ChatReplyAnalysis)
    next_question_generation: ChatNextQuestion = Field(default_factory=ChatNextQuestion)

class SemanticValidationResult(BaseModel):
    """Output schema for semantic_validator.py"""
    makes_sense: bool = True
    clarification_question: str = ""

class GuardrailResult(BaseModel):
    """Output schema for interviewer.py guardrail check"""
    valid: bool = True
    reason: str = ""

class OutreachCampaignResult(BaseModel):
    """Output schema for outreach_generator.py"""
    linkedin_message: str = ""
    cold_email_subject: str = ""
    cold_email_body: str = ""

def _coerce_null_strings(values: dict, fields: tuple) -> dict:
    """Replace None with "" on string-typed fields the LLM may emit as null.

    Groq's tool-call validator strict-checks declared `string` fields against
    JSON null and rejects the call. The model legitimately emits null for
    blank values, so we coerce them in a `before` validator instead of
    making every field Optional[str] (which would loosen the contract for
    every other consumer).
    """
    if not isinstance(values, dict):
        return values
    for f in fields:
        if values.get(f) is None:
            values[f] = ""
    return values


def _flatten_string_list(items, *, prefer_keys: tuple = ('description', 'text', 'name', 'value')) -> list:
    """Coerce a list-of-objects shape into a list-of-strings.

    The Groq model sometimes wraps each list item as a single-key object
    (e.g. `[{"description": "..."}, ...]` instead of `["...", ...]`). The
    schema declares plain strings, so without this coercion the call fails
    with `expected string, but got object`. Tries the standard wrapper
    keys in order; falls back to the first string value or the str() of
    the entire item.
    """
    if items is None:
        return []
    if isinstance(items, str):
        return [line.strip() for line in items.split('\n') if line.strip()]
    out = []
    for item in items:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
            continue
        if isinstance(item, dict):
            for k in prefer_keys:
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
                    break
            else:
                # No preferred key matched; pull the first string value.
                for v in item.values():
                    if isinstance(v, str) and v.strip():
                        out.append(v.strip())
                        break
        else:
            s = str(item).strip()
            if s:
                out.append(s)
    return out


# ─── PR 3a (2026-05-18): bullet-alias folding for resume-output schemas ───
#
# Bullet-bearing field names the LLM has been observed to emit. The
# validator folds all of these INPUTS into the canonical `description`
# output field. This list is input-tolerance scope, NOT a list of fields
# the model emits — output is always single-canonical `description`.
#
# `highlights` is the pre-PR-3a historical second-canonical and folds
# silently (no log spam). Every other alias logs info() so production-log
# frequency can inform whether the LLM prompt needs a stronger nudge.
#
# Adding entries here is purely additive defense-in-depth: it doesn't
# change the schema's output contract, only what inputs are tolerated.
_BULLET_ALIAS_KEYS = (
    'highlights',        # pre-PR-3a second-canonical — silent fold
    'achievements',      # PR 3f wrapper invention
    'responsibilities',  # observed in dev runs
    'accomplishments',
    'bullets',
    'tasks',
    'features',          # project-only invention
    'outcomes',          # project-only invention
    'deliverables',      # project-only invention
)

# Non-bullet LLM inventions the recovery path must DROP (not fold into
# description) so `extra="forbid"` doesn't reject the salvaged payload.
# The prompt already lists these in the "REMOVE FROM RESUMES" / "DO NOT
# INVENT FIELDS" sections, but the model still emits them under retry
# pressure — silently popping them here keeps the recovery path useful
# instead of letting one stray key drop the whole resume to the offline
# fallback (see 2026-05-28 5:16 run, employment_type='Internship').
_NON_BULLET_EXTRA_KEYS_EXPERIENCE = (
    'employment_type',   # 2026-05-28 — Groq emits "Internship" / "Full-time"
    'employment_status',
    'job_type',
    'role_type',
    'work_type',         # remote/hybrid/onsite — captured elsewhere
)
_NON_BULLET_EXTRA_KEYS_PROJECT = (
    'source',            # signal-only field documented in prompt
    'source_id',
    'source_url',
    'role',              # observed: project "role" labels (Author / Maintainer)
    'duration',          # not part of ResumeProject schema
    'date',
)


_DESC_LOGGER = logging.getLogger(__name__)


def _fold_into_description(data: dict) -> dict:
    """Fold all known bullet aliases into the canonical `description` field.

    Input-liberal, output-strict (PR 3a). After this runs:
      • data['description'] is always List[str] (possibly empty)
      • No alias keys remain, so extra="forbid" does not reject the model

    Handles these alias VALUE shapes:
      • str (non-empty)               → appended as single bullet
      • list[str]                     → appended item-by-item
      • list[{description|text|content|body: ...}]
                                      → unwrapped (PR-3f-style wrapper
                                        invention)
      • None / other shapes           → silently dropped (no-op)

    Existing `description` is normalized first:
      • str → single-element list (or [] if blank)
      • None / non-list → []
      • list → flattened via _flatten_string_list
    """
    desc = data.get('description', [])
    if isinstance(desc, str):
        desc = [desc] if desc.strip() else []
    elif desc is None:
        desc = []
    elif isinstance(desc, list):
        desc = _flatten_string_list(desc)
    else:
        desc = []

    for key in _BULLET_ALIAS_KEYS:
        if key not in data:
            continue
        value = data.pop(key)

        if key != 'highlights' and value:
            _DESC_LOGGER.info(
                "schema validator: folded LLM-invented bullets field '%s' "
                "into description (track frequency in case prompt needs a "
                "stronger nudge)",
                key,
            )

        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                desc.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    desc.append(item)
                elif isinstance(item, dict):
                    inner = (
                        item.get('description')
                        or item.get('text')
                        or item.get('content')
                        or item.get('body')
                    )
                    if isinstance(inner, list):
                        desc.extend(
                            s for s in inner
                            if isinstance(s, str) and s.strip()
                        )
                    elif isinstance(inner, str) and inner.strip():
                        desc.append(inner)
                # other item shapes: silent skip
        # other value shapes: silent skip

    data['description'] = desc
    return data


class ResumeExperience(BaseModel):
    """One experience entry in a generated resume. Bullets canonical in
    ``description`` (List[str]).

    DESIGN (PR 3a — 2026-05-18):
    Pre-PR-3a this model declared ``description: Union[str, List[str]]``
    and accepted an undeclared ``highlights`` input that the validator
    merged via richness/single-line heuristics. The dual-field shape was
    the upstream cause of LLM field inventions throughout the audit
    thread — Pydantic v2's default ``extra="ignore"`` admitted
    ``achievements``/``responsibilities``/etc. silently.

    PR 3a applies "input liberal, output strict":
      • ``description: List[str]`` is canonical (always a list).
      • ``extra="forbid"`` cleanly rejects unknown fields.
      • ``coerce_to_canonical`` folds known LLM aliases into description
        and pops the alias keys so extra="forbid" doesn't reject them.
        New inventions get logged.
    """
    title: str = ""
    company: str = ""
    duration: str = ""
    location: str = ""
    # PR 3b.2: Optional because the LLM correctly emits null when the CV
    # doesn't state these (internship industry, ongoing-role end_date).
    # Groq's server-side JSON-schema validator rejects null on plain
    # `str` fields before the Python validator can normalize. The
    # validator below still coerces None -> "" for downstream readers.
    industry: Optional[str] = None
    start_date: str = ""
    end_date: Optional[str] = None
    # Mirrors the profile-side Experience.is_current — explicit ongoing
    # flag, set ONLY when the source data says ongoing/current/present.
    # The render layer and reverse-chronological sort honor "Present"
    # tokens ONLY when this is True; otherwise a "Present" tail on
    # `end_date` / `duration` is treated as legacy LLM fabrication.
    is_current: Optional[bool] = None
    description: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode='before')
    @classmethod
    def coerce_to_canonical(cls, data):
        if not isinstance(data, dict):
            return data
        data = _coerce_null_strings(data, (
            'title', 'company', 'duration', 'location', 'industry',
            'start_date', 'end_date',
        ))
        for k in _NON_BULLET_EXTRA_KEYS_EXPERIENCE:
            if k in data:
                _DESC_LOGGER.info(
                    "schema validator: dropped LLM-invented experience field "
                    "'%s'=%r (not part of ResumeExperience schema)",
                    k, data.get(k),
                )
                data.pop(k, None)
        return _fold_into_description(data)


class ResumeProject(BaseModel):
    """One project entry in a generated resume. Same canonical pattern
    as :class:`ResumeExperience` — see that class for the PR-3a design
    rationale."""
    name: str = ""
    url: str = ""
    technologies: List[str] = Field(default_factory=list)
    description: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode='before')
    @classmethod
    def coerce_to_canonical(cls, data):
        if not isinstance(data, dict):
            return data
        data = _coerce_null_strings(data, ('name', 'url'))
        # Technologies tolerance: comma-separated string from the editor
        # form, and list-of-objects from a tool-call mode the LLM sometimes
        # uses. Kept inside this validator (rather than a sibling) to
        # consolidate input-tolerance logic in one place.
        tech = data.get('technologies', [])
        if isinstance(tech, str):
            data['technologies'] = [t.strip() for t in tech.split(',') if t.strip()]
        elif isinstance(tech, list):
            data['technologies'] = _flatten_string_list(tech)
        for k in _NON_BULLET_EXTRA_KEYS_PROJECT:
            if k in data:
                _DESC_LOGGER.info(
                    "schema validator: dropped LLM-invented project field "
                    "'%s'=%r (not part of ResumeProject schema)",
                    k, data.get(k),
                )
                data.pop(k, None)
        return _fold_into_description(data)

class ResumeCertification(BaseModel):
    name: str = ""
    issuer: str = ""
    # PR 3b.2: Optional because real CVs commonly omit cert verification
    # URLs, completion dates, and durations. The LLM correctly emits null
    # rather than fabricating empty strings. Validator normalizes
    # None -> "" for downstream readers.
    date: Optional[str] = None
    duration: Optional[str] = None
    url: Optional[str] = None

    @model_validator(mode='before')
    @classmethod
    def normalize(cls, values):
        if not isinstance(values, dict):
            return values
        return _coerce_null_strings(values, ('name', 'issuer', 'date', 'duration', 'url'))

class ResumeEducation(BaseModel):
    degree: str = ""
    institution: str = ""
    year: str = ""
    field: str = ""
    gpa: str = ""
    location: str = ""
    # PR 3b.2: Optional[List[str]] = None because the LLM correctly
    # emits null when the CV doesn't list honors. Validator normalizes
    # None -> [] for downstream readers.
    honors: Optional[List[str]] = None

    @model_validator(mode='before')
    @classmethod
    def normalize(cls, values):
        if not isinstance(values, dict):
            return values
        values = _coerce_null_strings(values, (
            'degree', 'institution', 'year', 'field', 'gpa', 'location',
        ))
        h = values.get('honors', [])
        # Groq emits null for empty list fields — handle it like
        # ResumeExperience.description does, so the failed_generation
        # recovery path can salvage the LLM's output instead of falling
        # all the way through to the offline renderer.
        if h is None:
            values['honors'] = []
        elif isinstance(h, str):
            values['honors'] = [line.strip() for line in h.split('\n') if line.strip()]
        elif isinstance(h, list):
            values['honors'] = _flatten_string_list(h)
        return values

class ResumeContentResult(BaseModel):
    """Output schema for resume_generator.py — superset of master profile fields.

    The editor at /resumes/edit/ surfaces every field defined here. Master-profile
    fields (UserProfile.data_content) get pulled into the matching field on
    generation or via sync_from_master. The LLM may still drop ATS-discouraged
    fields (objective, GPA) for its own resume drafts; users can re-add them
    via the editor.
    """
    professional_title: str = ""
    professional_summary: str = ""
    objective: str = ""
    skills: List[str] = Field(default_factory=list)
    experience: List[ResumeExperience] = Field(default_factory=list)
    education: List[ResumeEducation] = Field(default_factory=list)
    projects: List[ResumeProject] = Field(default_factory=list)
    certifications: List[ResumeCertification] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    # Round 1.5: surface honors / awards (ICPC, hackathons, scholarships)
    # in their own section. The CV parser stores these under various
    # keys (awards, honors, achievements) and the recruiter does scan
    # for them on entry-level resumes.
    awards: List[str] = Field(default_factory=list)
    model_config = {"extra": "allow"}

    @model_validator(mode='before')
    @classmethod
    def normalize(cls, values):
        if not isinstance(values, dict):
            return values
        # Top-level string fields may come back as null.
        values = _coerce_null_strings(values, (
            'professional_title', 'professional_summary', 'objective',
        ))
        # `skills` is List[str] but the LLM sometimes wraps each entry as
        # {name: "...", proficiency: null, years: null}. Flatten.
        s = values.get('skills', [])
        if isinstance(s, list):
            values['skills'] = _flatten_string_list(s)
        # `languages` same shape risk.
        lg = values.get('languages', [])
        if isinstance(lg, list):
            values['languages'] = _flatten_string_list(lg)
        # Awards / honors — accept both keys and the same flattening
        # the LLM tends to wrap (single-key objects, etc.).
        aw = values.get('awards', None)
        if aw is None:
            aw = values.get('honors')
        if isinstance(aw, list):
            values['awards'] = _flatten_string_list(aw)
        elif isinstance(aw, str) and aw.strip():
            values['awards'] = [aw.strip()]
        return values

class SectionFilterResult(BaseModel):
    """Output schema for resume_generator.py section filtering"""
    include_sections: List[str] = Field(default_factory=list)
    exclude_sections: List[str] = Field(default_factory=list)
    reasoning: str = ""

class LearningPathItem(BaseModel):
    """Single item in a learning path.

    `resources` is a list of dicts with the keys `name` / `url` / `provider`
    (typed loosely as Dict[str, str] for LLM-friendly output). The template
    renders each resource as a clickable link when `url` is non-empty.
    """
    skill: str = ""
    importance: str = ""
    resources: List[Dict[str, str]] = Field(default_factory=list)
    project_idea: str = ""
    time_estimate: str = ""

class LearningPathResult(BaseModel):
    """Output schema for learning_path_generator.py"""
    items: List[LearningPathItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Project enrichment + dedupe (Phase 1 of the GitHub/Scholar/Kaggle → resume
# pipeline). The aggregators pull raw signal data; project_enricher.py turns
# each repo / paper / competition into a project-shaped artifact with
# resume bullets; project_dedupe.py decides per pair whether the enriched
# project is the same as something the user already has typed in.
# ---------------------------------------------------------------------------

class EnrichedProject(BaseModel):
    """One source-derived project with resume-ready content.

    `source` is one of {"github", "scholar", "kaggle"}; `source_id` is the
    aggregator's stable identifier for the item (repo full_name, paper
    citation_id, competition slug). `source_url` is the canonical web URL.
    """
    name: str = ""
    summary: str = ""
    tech_stack: List[str] = Field(default_factory=list)
    bullets: List[str] = Field(default_factory=list)
    source: str = ""
    source_id: str = ""
    source_url: str = ""

    @model_validator(mode='before')
    @classmethod
    def normalize(cls, values):
        # Allow LLM to return tech_stack as a comma-separated string
        ts = values.get('tech_stack', [])
        if isinstance(ts, str):
            values['tech_stack'] = [t.strip() for t in ts.split(',') if t.strip()]
        # Allow bullets as newline-separated string too
        b = values.get('bullets', [])
        if isinstance(b, str):
            values['bullets'] = [line.strip() for line in b.split('\n') if line.strip()]
        return values


class EnrichedProjectBatch(BaseModel):
    """LLM output schema for batch enrichment of multiple repos at once."""
    projects: List[EnrichedProject] = Field(default_factory=list)


class DedupeDecision(BaseModel):
    """One per-pair dedupe verdict from the LLM.

    `enriched_index` is the index into the enriched-projects list; `typed_index`
    is the index into the user's typed projects (matched_skills if action is
    not "add_new"). When `action == "add_new"`, typed_index is -1.

    `action`:
      - "merge": the two represent the same project; keep both signals
        (we'll union tech stacks + concatenate bullets, prefer typed name).
      - "keep_existing": same project; keep the typed version, drop enriched.
      - "keep_new": same project; replace typed with enriched.
      - "add_new": enriched has no typed counterpart; add it as a new project.

    `confidence` is the LLM's stated confidence in the verdict (0–1).
    """
    enriched_index: int = -1
    typed_index: int = -1
    action: str = "add_new"
    confidence: float = 0.0
    reason: str = ""


class DedupeBatch(BaseModel):
    """LLM output schema for batched dedupe across all (typed, enriched) pairs."""
    decisions: List[DedupeDecision] = Field(default_factory=list)


class KeywordCandidate(BaseModel):
    """One suggested keyword with a brief plain-language reason."""
    keyword: str = Field(default="", description="Job-board search keyword. 1-3 words, no parens, no seniority words.")
    why: str = Field(default="", description="One short phrase (under 12 words) explaining why this fits.")


class SuggestedJobPreferences(BaseModel):
    """LLM output for the 'auto-fill preferences from my profile' button.

    Restricted to fields the LLM can genuinely reason about from a CV +
    signals — sources/date_posted/max_jobs are user policy, not profile-derived,
    so they're left to the form's defaults.
    """
    keyword: str = Field(default="", description="The single TOP keyword (also the first item in keyword_candidates).")
    keyword_candidates: List[KeywordCandidate] = Field(
        default_factory=list,
        description="3-5 distinct candidate roles ranked by fit — different angles on the user's profile, not synonyms. Each anchored on a skill cluster the user actually demonstrates.",
    )
    locations: List[str] = Field(default_factory=list, description="2-3 locations the user could realistically target, ordered by likely fit. Include 'Remote' if appropriate.")
    experience_levels: List[str] = Field(default_factory=list, description="One or more of: internship, entry, associate, mid_senior, director, executive. Pick the user's current band plus the next one up.")
    workplace_types: List[str] = Field(default_factory=list, description="Subset of: onsite, remote, hybrid. Reflect what's plausible given location + recent roles.")
    rationale: str = Field(default="", description="One short sentence explaining the choices, surfaced to the user.")


# --- HR/CV specialist supervisor (final review layer) ---------------------

# Severity words the model may emit for a deal-breaker. Everything else
# (medium/low/minor/needs-work/strong, 🟡/🟢) collapses to "warning" so an
# uncertain reviewer never blocks shipping by accident.
_SUPERVISOR_BLOCKING_SEVERITY = {
    "blocking", "block", "blocker", "critical", "high", "severe",
    "fail", "failure", "major", "deal-breaker", "dealbreaker", "red",
    "🔴",
}
# Words that mean the finding is about the RENDERED artifact (layout /
# cross-format), which full re-generation cannot fix — so these are surfaced
# but never drive the regen loop. Everything else is "content".
_SUPERVISOR_RENDER_LAYER = {
    "render", "layout", "format", "formatting", "visual", "visualisation",
    "pdf", "docx", "page", "page-break", "pagebreak", "spacing",
    "alignment", "typography", "design", "overflow", "margins",
}
_SUPERVISOR_ADVANCE_VERDICT = {
    "advance", "pass", "ship", "ok", "okay", "approve", "approved",
    "clean", "good", "accept", "accepted",
}


class SupervisorFinding(BaseModel):
    """One issue raised by the HR/CV specialist supervisor.

    `layer` decides whether the regen loop can act on it:
      - "content" → fixable by re-generating resume_content (summary, skills,
        ordering, bullets, JD-fit, grounding). Blocking content findings drive
        the loop.
      - "render"  → a property of the rendered artifact (page breaks, date
        format, header separators). Surfaced for visibility, but regeneration
        reproduces it, so it never triggers another round.
    `severity` is "blocking" (🔴 deal-breaker) or "warning" (everything else).
    """
    layer: str = "content"
    severity: str = "warning"
    category: str = ""
    location: str = ""
    issue: str = ""
    fix: str = ""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode='before')
    @classmethod
    def normalize(cls, values):
        if not isinstance(values, dict):
            return values
        values = _coerce_null_strings(
            values, ('layer', 'severity', 'category', 'location', 'issue', 'fix')
        )
        sev = str(values.get('severity', '') or '').strip().lower()
        values['severity'] = 'blocking' if sev in _SUPERVISOR_BLOCKING_SEVERITY else 'warning'
        lyr = str(values.get('layer', '') or '').strip().lower()
        values['layer'] = 'render' if lyr in _SUPERVISOR_RENDER_LAYER else 'content'
        return values


class SupervisorReview(BaseModel):
    """The supervisor's verdict on a generated resume.

    `verdict` ("advance"|"revise") mirrors the manual reviewer's ship/no-ship
    call and is for reporting only — the regen loop is driven by
    `blocking_content_findings()`, not the verdict string.
    """
    verdict: str = "advance"
    summary: str = ""
    findings: List[SupervisorFinding] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    @model_validator(mode='before')
    @classmethod
    def normalize(cls, values):
        # The tool-call output sometimes arrives as a bare list of findings.
        if isinstance(values, list):
            values = {"findings": values}
        if not isinstance(values, dict):
            return values
        # Common aliases for the findings list.
        if 'findings' not in values:
            for alias in ('issues', 'problems', 'findings_list', 'results'):
                if alias in values:
                    values['findings'] = values[alias]
                    break
        f = values.get('findings')
        if isinstance(f, dict):
            values['findings'] = [f]
        elif f is None:
            values['findings'] = []
        values = _coerce_null_strings(values, ('verdict', 'summary'))
        v = str(values.get('verdict', '') or '').strip().lower()
        values['verdict'] = 'advance' if v in _SUPERVISOR_ADVANCE_VERDICT else 'revise'
        return values

    def blocking_content_findings(self) -> List["SupervisorFinding"]:
        """Findings that should trigger another generation round."""
        return [f for f in self.findings
                if f.severity == 'blocking' and f.layer == 'content']

    def all_blocking(self) -> List["SupervisorFinding"]:
        """All deal-breakers (content + render) — for reporting/surfacing."""
        return [f for f in self.findings if f.severity == 'blocking']
