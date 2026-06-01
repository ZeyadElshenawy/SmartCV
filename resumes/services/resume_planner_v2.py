"""Global plan stage of the v2 evidence-first resume pipeline.

Pipeline shape (this module = step 3):

    ingest → extract → FactStore → GLOBAL PLAN (this)
        → section generation → review → assemble

This is the **first and only** stage that sees ALL facts at once.
The global view is the whole point:

  - **Keyword over-representation prevention.** v1 sprayed "Python"
    across every section because no component had the global picture.
    The planner tracks per-skill mention counts across sections and
    refuses to allocate past a cap. The mechanism is in CODE here,
    not "please don't repeat skills" in the prompt.

  - **JD-relevance × source_reliability combined.** A platform-verified
    achievement (Kaggle rank) outranks a tutorial-derived one for the
    same slot. Hedged facts are tagged forward so the generator phrases
    them cautiously.

The planner does NOT produce prose. It produces a structured PlanResult
that the next stage (section generator) renders. Prose generation is
explicitly deferred: this stage's job is "which fact, in which section,
in what order, with what rationale".

This module is **isolated**. Nothing in v1 (resume_generator,
inclusion_planner, normalizer, views) depends on it. The fact store
and extractors are read-only inputs.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from resumes.services.fact_store import (
    FactRecord,
    FactStore,
    FactType,
    SourceReliability,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema (pydantic — matches the codebase style).
# ---------------------------------------------------------------------------


class FactAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    rationale: str = Field(
        default="",
        description="Short explanation of why this fact was placed here. "
                    "Surfaces in the plan output for defense / debugging.",
    )
    hedged: bool = False


class EntityAllocation(BaseModel):
    """Per-entity grouping for experience and project sections.

    metric_fact_ids comes from ``store.metrics_for(entity_id)`` — the
    fact store's safety accessor. By construction, no metric from a
    different entity can appear here, so cross-attachment is
    structurally impossible at the plan-output level too.
    """
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    entity_display: str
    anchor_fact_id: Optional[str] = Field(
        default=None,
        description="The project/role/education/credential fact that "
                    "anchors this entity (the 'parent' fact).",
    )
    facts: list[FactAllocation] = Field(default_factory=list)
    metric_fact_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    hedged_any: bool = False


class SectionPlan(BaseModel):
    """One section's allocation. Flat sections (summary, skills,
    education, certifications) populate ``facts``. Grouped sections
    (experience, projects) populate ``entities``."""
    model_config = ConfigDict(extra="forbid")
    section: str
    facts: list[FactAllocation] = Field(default_factory=list)
    entities: list[EntityAllocation] = Field(default_factory=list)


class ValidationAnomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    reason: str


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid_fact_ids: list[str]
    anomalies: list[ValidationAnomaly] = Field(default_factory=list)


class PlanResult(BaseModel):
    """The full structured plan handed to the section-generation stage."""
    model_config = ConfigDict(extra="forbid")
    sections: dict[str, SectionPlan]
    validation: ValidationReport
    anti_overrep_stats: dict[str, int] = Field(
        default_factory=dict,
        description="Per-skill mention count across all sections. "
                    "Useful for diagnostics — proves the cap held.",
    )
    ranking_method: str = "lexical_jd_overlap_v1"
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Caps + tunables. Documented; not magic.
# ---------------------------------------------------------------------------


# Anchor types: a metric is "orphaned" if its entity_id has no fact of
# any of these types. Achievements alone don't qualify — they're the
# CHILDREN of an entity, not its anchor. A real role/project/education/
# credential entry is what proves the entity exists in the source.
_ANCHOR_TYPES = {
    FactType.ROLE, FactType.PROJECT,
    FactType.EDUCATION, FactType.CREDENTIAL,
}


# Default section caps (count of facts/bullets allocated per section).
# Tunable per call via ``section_caps`` arg to ``build_plan``.
DEFAULT_SECTION_CAPS = {
    "summary": 3,
    "skills": 15,
    "experience": 12,        # total bullets across all role entities
    "projects": 8,           # total bullets across all project entities
    "education": 4,
    "certifications": 8,
}


# Per-skill mention cap across the whole resume — the v1 keyword-
# stuffing prevention. A skill may appear ONCE in the skills section
# and at most ``PER_SKILL_MENTION_CAP - 1`` times in other-section
# bullets. Default 3 ⇒ 1 in skills + up to 2 elsewhere. Configurable.
DEFAULT_PER_SKILL_MENTION_CAP = 3


# Per-entity cap for the experience section. Without this cap, a
# single verbose role can consume the entire ``caps['experience']``
# budget and starve real subsequent roles — a real job going missing
# is a real defect. Setting this to 4 yields balanced allocation in
# the common 2-3 role case (default budget 12 ÷ 3 ≈ 4) while still
# matching what a recruiter expects per role (3-5 bullets). This is
# an ALLOCATION-fairness cap; the generator still writes only
# grounded bullets. Configurable per call via build_plan kwarg.
DEFAULT_PER_ENTITY_EXPERIENCE_CAP = 4


# Reliability rank — copied logic from FactStore to avoid coupling the
# planner to the store's private map. Higher wins.
_RELIABILITY_RANK = {
    SourceReliability.PLATFORM_VERIFIED: 4,
    SourceReliability.USER_ORIGINAL: 3,
    SourceReliability.TUTORIAL_DERIVED: 2,
    SourceReliability.INFERRED: 1,
}

# Hedge multiplier — hedged facts rank lower for the same slot.
_HEDGE_FACTOR = 0.7


# Date parsing for reverse-chronological role ordering. Self-contained;
# the v1 normalizer has equivalent logic but we deliberately don't
# import it (the planner is a parallel module).
_PRESENT_RE = re.compile(
    r"\b(present|current|currently|ongoing|now|to\s+date|till\s+now)\b",
    re.IGNORECASE,
)
_RANGE_SEP_RE = re.compile(r"\s+(?:[-–—]|to|until|through)\s+", re.IGNORECASE)
_ISO_YM_RE = re.compile(r"\b(20\d{2}|19\d{2})[-/](\d{1,2})(?:[-/]\d{1,2})?\b")
_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}
_MONTH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec|"
    r"january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b\.?",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")


# ---------------------------------------------------------------------------
# validate_fact_store — fail loud, drop anomalous facts from planning.
# ---------------------------------------------------------------------------


def validate_fact_store(store: FactStore) -> ValidationReport:
    """Pre-pass that walks every fact and flags anomalies.

    Policy: log every anomaly loudly; the caller drops the anomalous
    fact from planning. We don't crash the whole plan for one bad
    fact — but if the store is empty or all-anomalous, ``build_plan``
    raises ``ValueError`` (that's a real failure, not skippable).

    Anomaly checks:
      1. ``source_reliability`` is a known tier (Pydantic should have
         enforced this at construction; verify anyway as belt-and-
         braces).
      2. ``claim`` and ``evidence_quote`` are non-empty (same
         belt-and-braces — FactRecord rejects empties).
      3. **Anchor existence**: every metric's ``entity_id`` must be
         populated by at least one ``role``/``project``/``education``/
         ``credential`` fact in the same store. An anchor-less metric
         is orphaned and gets dropped — that's the structural
         equivalent of the v1 "Banque Misr" role guard: a metric
         claiming to belong to a project that doesn't otherwise
         appear in the store is treated as suspect.

    Returns: ``ValidationReport`` with ``valid_fact_ids`` (everything
    that survived) and ``anomalies`` (each dropped fact + reason).
    """
    anomalies: list[ValidationAnomaly] = []
    # Build the anchor entity-id set up front: any entity that has a
    # role/project/education/credential fact bound to it.
    anchor_entity_ids: set[str] = {
        f.entity_id for f in store.all()
        if f.type in _ANCHOR_TYPES and f.entity_id
    }

    valid: list[str] = []
    for f in store.all():
        # Check 1 — reliability tier.
        if f.source_reliability not in _RELIABILITY_RANK:
            anomalies.append(ValidationAnomaly(
                fact_id=f.id,
                reason=f"unknown source_reliability tier: {f.source_reliability!r}",
            ))
            logger.warning(
                "resume_planner_v2: dropping fact %s (anomaly: unknown "
                "reliability tier %r)",
                f.id, f.source_reliability,
            )
            continue

        # Check 2 — claim and evidence non-empty.
        if not (f.claim and f.claim.strip()):
            anomalies.append(ValidationAnomaly(
                fact_id=f.id, reason="empty claim",
            ))
            logger.warning("resume_planner_v2: dropping fact %s (empty claim)", f.id)
            continue
        if not (f.evidence_quote and f.evidence_quote.strip()):
            anomalies.append(ValidationAnomaly(
                fact_id=f.id, reason="empty evidence_quote",
            ))
            logger.warning(
                "resume_planner_v2: dropping fact %s (empty evidence_quote)",
                f.id,
            )
            continue

        # Check 3 — anchor existence for metrics. A metric bound to an
        # entity_id that has no project/role/education/credential fact
        # is an orphan. (FactRecord already rejects metrics WITHOUT an
        # entity_id; this catches metrics WITH an entity_id that no
        # other fact backs up.)
        if f.type == FactType.METRIC and f.entity_id not in anchor_entity_ids:
            anomalies.append(ValidationAnomaly(
                fact_id=f.id,
                reason=(
                    f"orphan metric: entity_id {f.entity_id!r} has no "
                    f"role/project/education/credential anchor in the store"
                ),
            ))
            logger.warning(
                "resume_planner_v2: dropping orphan metric %s (entity %r "
                "has no anchor fact)",
                f.id, f.entity_id,
            )
            continue

        valid.append(f.id)

    if anomalies:
        logger.warning(
            "resume_planner_v2: validate_fact_store flagged %d anomalies "
            "out of %d facts.",
            len(anomalies), len(store),
        )

    return ValidationReport(valid_fact_ids=valid, anomalies=anomalies)


# ---------------------------------------------------------------------------
# Lexical JD-relevance scoring. Documented; simple by design.
# ---------------------------------------------------------------------------


def _normalize_skill(s: str) -> str:
    """Skill name normalization used both for relevance matching and
    for the per-skill mention counter. Lowercase, whitespace-collapsed."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _text_mentions(text: str, term: str) -> bool:
    """Word-boundary substring match for ``term`` in ``text``.

    Boundary is "no adjacent A-Z, a-z, or 0-9 character". This means:
      - "python" matches inside "Built with Python." (trailing dot is
        not an identifier char, so it counts as a boundary).
      - "python" does NOT match inside "Cython" or "MyPythonDemo"
        (adjacent letters block the boundary).
      - "C++" / "node.js" / "machine learning" all work because
        re.escape preserves their characters and the surrounding
        boundary check is on identifier chars only.

    This replaces the prior naïve token-set-intersection approach,
    which broke on trailing punctuation (the dev test caught it:
    'Python.' tokenized to 'python.' which set-doesn't-equal
    'python' → mention counter under-counted → cap over-spent).
    """
    if not text or not term:
        return False
    return bool(re.search(
        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
        text, re.IGNORECASE,
    ))


def _jd_relevance_score(
    fact: FactRecord,
    *,
    must_have_terms: list[str],
    nice_to_have_terms: list[str],
) -> float:
    """Lexical JD-relevance: count of must-have / nice-to-have terms
    appearing as word-boundary matches in the fact's claim +
    evidence_quote. Must-haves count double.

    Skill facts get a small bonus when their normalized claim is
    itself a JD skill (exact match) — Python on a Python JD goes to
    the top of the skills list regardless of relevance overlap with
    other facts.
    """
    text = (fact.claim or "") + " " + (fact.evidence_quote or "")
    must_hits = sum(1 for t in must_have_terms if _text_mentions(text, t))
    nice_hits = sum(1 for t in nice_to_have_terms if _text_mentions(text, t))
    score = (must_hits * 2.0) + (nice_hits * 1.0)
    if fact.type == FactType.SKILL:
        norm = _normalize_skill(fact.claim)
        if any(_text_mentions(norm, t) for t in must_have_terms):
            score += 5.0
        elif any(_text_mentions(norm, t) for t in nice_to_have_terms):
            score += 2.0
    return score


def _final_rank(
    fact: FactRecord,
    *,
    must_have_terms: list[str],
    nice_to_have_terms: list[str],
) -> tuple[float, str]:
    """Combined ranking score for one fact. Higher first.

    Formula:
        (relevance + 0.5) * reliability_rank * hedge_factor

    The +0.5 baseline ensures zero-overlap facts still order by
    reliability + hedge alone (so a verified achievement with no JD
    keyword still beats a tutorial-derived achievement with no JD
    keyword).

    Returns ``(score, fact_id)`` — fact_id is the deterministic
    tiebreaker so two facts at the same score sort stably."""
    relevance = _jd_relevance_score(
        fact,
        must_have_terms=must_have_terms,
        nice_to_have_terms=nice_to_have_terms,
    )
    rank = _RELIABILITY_RANK.get(fact.source_reliability, 1)
    hedge = _HEDGE_FACTOR if fact.hedged else 1.0
    return ((relevance + 0.5) * rank * hedge, fact.id)


# ---------------------------------------------------------------------------
# Per-skill mention cap — the anti-over-representation enforcement.
# ---------------------------------------------------------------------------


class _MentionCounter:
    """Tracks how many times each skill has been mentioned across the
    full plan. When a candidate fact would push a tracked skill past
    its cap, that fact is refused at the allocation site.

    "Mention" semantics: a skill is mentioned by a fact when its
    normalized form appears as a tokenized substring of the fact's
    claim + evidence_quote (lowercase + alphanumeric token boundary).
    """

    def __init__(self, tracked_skills: set[str], cap: int):
        self._tracked = {_normalize_skill(s) for s in tracked_skills if s}
        self._cap = max(1, int(cap))
        self._counts: dict[str, int] = {s: 0 for s in self._tracked}

    def mentions_in(self, fact: FactRecord) -> set[str]:
        """Return the subset of tracked skills mentioned by this fact.
        Word-boundary match — trailing punctuation, multi-token skills
        ('Machine Learning'), and special chars ('C++', 'node.js') all
        work."""
        text = (fact.claim or "") + " " + (fact.evidence_quote or "")
        return {s for s in self._tracked if _text_mentions(text, s)}

    def would_overflow(self, fact: FactRecord) -> set[str]:
        """Which tracked skills would exceed the cap if this fact were
        allocated. Empty set ⇒ fact is safe to allocate."""
        return {
            s for s in self.mentions_in(fact)
            if self._counts.get(s, 0) + 1 > self._cap
        }

    def record(self, fact: FactRecord) -> None:
        """Increment counters for every tracked skill this fact
        mentions. Caller invokes this after deciding to keep the fact."""
        for s in self.mentions_in(fact):
            self._counts[s] = self._counts.get(s, 0) + 1

    def stats(self) -> dict[str, int]:
        return dict(self._counts)


# ---------------------------------------------------------------------------
# Reverse-chronological ordering for role entities.
# ---------------------------------------------------------------------------


def _parse_yearmonth(s: str, today_ym: tuple[int, int]) -> Optional[tuple[int, int]]:
    """Parse a date-like string to ``(year, month)``. None on failure.
    Mirrors the v1 normalizer's logic but stays in this module to
    avoid coupling. Handles ISO, named-month, year-only, and
    Present/Current/Ongoing/Now."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    if _PRESENT_RE.search(s):
        return today_ym
    m = _ISO_YM_RE.search(s)
    if m:
        return int(m.group(1)), max(1, min(12, int(m.group(2))))
    month = None
    month_match = _MONTH_RE.search(s)
    if month_match:
        month = _MONTH_NAMES.get(month_match.group(1).lower().rstrip("."))
    year_match = _YEAR_RE.search(s)
    if month and year_match:
        return int(year_match.group(0)), month
    if year_match:
        return int(year_match.group(0)), 12
    return None


def _role_end_yearmonth(
    role_fact: FactRecord, today_ym: tuple[int, int],
) -> tuple[int, int]:
    """Best-effort end-date extraction from a role fact's evidence_quote.

    Walks past EVERY range separator and takes whatever comes after the
    LAST one. Handles the nested-range case
    ``"AI Trainee, DEPI — Jun 2025 - Dec 2025"``:
      first split on ' — ' → tail "Jun 2025 - Dec 2025"
      next split on ' - ' → tail "Dec 2025"  ← this is what we want
    A previous single-split implementation stopped at "Jun 2025 - Dec
    2025" and `_parse_yearmonth` picked up the FIRST month, returning
    (2025, 6) — putting DEPI behind NTI's (2025, 9). This walk-to-last
    fixes the ordering.

    Returns (0, 0) sentinel when no date is parseable — those entities
    sink to the bottom but preserve their relative order (stable sort).
    """
    quote = role_fact.evidence_quote or ""
    # Walk past every range separator; the END is whatever follows the
    # last one. Bound the loop so a pathological input can't spin.
    tail = quote
    for _ in range(8):
        parts = _RANGE_SEP_RE.split(tail, maxsplit=1)
        if len(parts) <= 1:
            break
        tail = parts[-1].strip()
    ym = _parse_yearmonth(tail, today_ym)
    if ym:
        return ym
    # Fall back to single-date parse of the whole quote.
    ym = _parse_yearmonth(quote, today_ym)
    return ym or (0, 0)


# ---------------------------------------------------------------------------
# Same-entity cross-section conflict resolver.
#
# The same real-world entity can arrive as facts of DIFFERENT TYPES
# from different sources. A multi-month training program might be a
# ROLE fact (scraped from LinkedIn's experience section) AND a
# CREDENTIAL fact (parsed from the CV's "courses" list). Without
# intervention, that program appears in TWO sections of the resume:
# experience AND certifications. The resolver:
#
#   1. DETECTS same-entity duplicates across types (conservative
#      org+name match; ambiguous matches stay separate, mirroring the
#      profile-README rebind / role-identity-guard principle).
#   2. SCORES the merged cluster's EXPERIENCE SUBSTANCE on four
#      signals: duration, org relationship, deliverables, applied
#      language.
#   3. CHOOSES the section: >=2 signals → experience (real work);
#      0-1 signals → certifications (genuinely thin OR truly
#      ambiguous middle → conservative default to NOT inflate to a
#      job). The two-signal threshold is intentional anti-timidity:
#      an entity with real substance (e.g. duration + org +
#      deliverable) MUST land in experience even if one source
#      labelled it a "course"/"track"/"certificate".
#   4. EMITS one entity in the chosen section; the other-section
#      anchors are SUPPRESSED so the entity surfaces once, not twice.
#
# General: no profile/entity-name hardcoding. Match thresholds align
# with signal_merger's existing entity dedup (canonical name + 0.85
# fuzzy ratio).
# ---------------------------------------------------------------------------


# Cert-issuer platforms — when an org matches one of these, that
# platform name doesn't count as an "org relationship" substance
# signal. A DataCamp / Coursera "issuer" is administrative metadata,
# not an employer / training-initiative affiliation. Canonical form:
# alphanumeric-only, lowercase.
_CERT_PLATFORM_TOKENS: set[str] = {
    "coursera", "udemy", "datacamp", "edx", "linkedinlearning",
    "kagglelearn", "fastai", "udacity", "pluralsight", "codecademy",
    "freecodecamp", "mooc", "youtube",
}


# Distinctive-token filter for cross-type name matching. Strips
# stopwords + generic cert/program scaffolding words so the overlap
# check focuses on real entity-naming tokens. "Python" / "DEPI" /
# "Capstone" survive; "course" / "completion" / "the" don't.
_NAME_STOPWORDS: set[str] = {
    # Functional stopwords.
    "and", "or", "the", "an", "for", "with", "by", "from", "into",
    "as", "is", "are", "be", "of", "in", "on", "at", "to",
    # Generic cert / program scaffolding.
    "completion", "certificate", "certified", "training", "program",
    "course", "fundamentals", "introduction", "intro", "basics",
    "level", "tier", "track", "associate", "professional",
}


# Substance regexes. Conservative — they fire on clear signal words,
# not on weak hints.
_DATE_RANGE_RE = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?|20\d{2}|19\d{2})\s*"
    r"(?:[-–—]|to|until|through)\s*"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?|20\d{2}|19\d{2}|present|now|"
    r"current(?:ly)?)",
    re.IGNORECASE,
)
_DELIVERABLE_TOKENS_RE = re.compile(
    r"\b(?:capstone|pipeline|deliverable|delivered|shipped|deployed|"
    r"prototype|implementation|integration|end[-\s]to[-\s]end)\b",
    re.IGNORECASE,
)
_APPLIED_VERBS_RE = re.compile(
    r"\b(?:built|developed|deployed|analyzed|engineered|designed|"
    r"implemented|shipped|architected|automated|spearheaded|crafted|"
    r"led|created|trained|fine[-\s]tuned)\b",
    re.IGNORECASE,
)
_DISTINCTIVE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _canonical_alnum(text: str) -> str:
    """Lowercase, strip non-alphanumeric. Mirrors signal_merger's
    canonicalization for entity keys."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _distinctive_tokens(text: str) -> set[str]:
    """Tokens for cross-type name-overlap matching. Length >= 3,
    stopwords + scaffolding words stripped, lowercase."""
    return {
        t for t in (m.group(0).lower()
                    for m in _DISTINCTIVE_TOKEN_RE.finditer(text or ""))
        if len(t) >= 3 and t not in _NAME_STOPWORDS
    }


def _extract_org(fact: FactRecord) -> str:
    """Best-effort org extraction from an anchor fact.

    The structured-profile reader builds entity_display in known
    shapes — for ROLE/EDUCATION as ``"<lhs> @ <rhs>"`` (where rhs is
    the org), and for CREDENTIAL as ``"<name>"`` with the issuer
    appended to claim after ``" — "``. Falls back to entity_display
    when no separator present.
    """
    disp = (fact.entity_display or "").strip()
    if fact.type == FactType.CREDENTIAL:
        # CREDENTIAL claims carry the issuer: ``"<name> — <issuer>"``.
        c = fact.claim or ""
        for sep in (" — ", " - ", " – "):
            if sep in c:
                return c.split(sep, 1)[1].strip()
        return disp
    # ROLE / EDUCATION: split on the entity_display separator.
    if " @ " in disp:
        return disp.split(" @ ", 1)[1].strip()
    return disp


def _is_cert_platform(org_canon: str) -> bool:
    """``True`` iff the canonical org token names a known cert /
    course platform (not a real employer / training initiative)."""
    if not org_canon:
        return False
    if org_canon in _CERT_PLATFORM_TOKENS:
        return True
    # Catch ``"linkedinlearningacademy"`` etc.
    for tok in _CERT_PLATFORM_TOKENS:
        if tok in org_canon and len(tok) >= 6:
            return True
    return False


_RESOLVER_ANCHOR_TYPES = {
    FactType.ROLE, FactType.CREDENTIAL, FactType.EDUCATION,
}


def _same_entity(a: FactRecord, b: FactRecord) -> bool:
    """Conservative cross-type same-entity decision.

    Returns ``True`` iff:
      - the two anchors are of DIFFERENT types (same-type dedup is
        the signal_merger's job and already ran upstream), AND
      - their canonical orgs match (exact or SequenceMatcher ratio
        >= 0.85 — the same threshold signal_merger uses for entity
        dedup), AND
      - either:
          * they share at least one distinctive name token (length
            >= 3, not stopword / scaffolding), OR
          * their full canonical name strings have SequenceMatcher
            ratio >= 0.85.

    Returns ``False`` whenever any signal is missing — ambiguous
    matches stay separate. This mirrors the unambiguous-match
    principle the profile-README rebinder uses ("never guess").
    """
    if a.type == b.type:
        return False
    if a.type not in _RESOLVER_ANCHOR_TYPES:
        return False
    if b.type not in _RESOLVER_ANCHOR_TYPES:
        return False
    org_a = _canonical_alnum(_extract_org(a))
    org_b = _canonical_alnum(_extract_org(b))
    if not org_a or not org_b:
        return False
    if org_a != org_b:
        if SequenceMatcher(None, org_a, org_b).ratio() < 0.85:
            return False
    # Org matched. Now require name overlap to avoid collapsing two
    # unrelated artifacts from the SAME issuer (e.g. two different
    # DataCamp courses share the issuer but are different courses).
    name_a = (a.entity_display or "") + " " + (a.claim or "")
    name_b = (b.entity_display or "") + " " + (b.claim or "")
    tokens_a = _distinctive_tokens(name_a)
    tokens_b = _distinctive_tokens(name_b)
    if tokens_a & tokens_b:
        return True
    canon_a = _canonical_alnum(name_a)
    canon_b = _canonical_alnum(name_b)
    if not canon_a or not canon_b:
        return False
    return SequenceMatcher(None, canon_a, canon_b).ratio() >= 0.85


def _evidence_text_for_cluster(
    cluster: list[FactRecord], store: FactStore,
) -> str:
    """Combined searchable text for a merged cluster: every anchor's
    claim + evidence_quote, plus every child fact at any of the
    cluster's entity_ids."""
    parts: list[str] = []
    seen_ids: set[str] = set()
    for anchor in cluster:
        if anchor.id in seen_ids:
            continue
        seen_ids.add(anchor.id)
        parts.append(anchor.claim or "")
        parts.append(anchor.evidence_quote or "")
        for child in store.by_entity(anchor.entity_id or ""):
            if child.id in seen_ids:
                continue
            seen_ids.add(child.id)
            parts.append(child.claim or "")
            parts.append(child.evidence_quote or "")
    return " ".join(p for p in parts if p)


def _score_substance(
    cluster: list[FactRecord], store: FactStore,
) -> tuple[dict[str, bool], str]:
    """Compute the four experience-substance signals for a merged
    cluster.

    Signals:
      - ``duration``: a multi-month/year date range present anywhere
        in the cluster's combined text.
      - ``org_relationship``: at least one anchor's org is a real
        affiliation (not a cert-issuer platform).
      - ``deliverables``: deliverable nouns (capstone, pipeline,
        shipped, deployed, prototype, end-to-end) anywhere.
      - ``applied_language``: action verbs (built, developed,
        deployed, engineered, ...) — distinct from "completed" /
        "studied" / "attended".

    Returns ``(signals, rationale_prefix)`` — the rationale prefix
    names which signals fired so a downstream reader can audit the
    decision."""
    text = _evidence_text_for_cluster(cluster, store)
    signals = {
        "duration": bool(_DATE_RANGE_RE.search(text)),
        "org_relationship": False,
        "deliverables": bool(_DELIVERABLE_TOKENS_RE.search(text)),
        "applied_language": bool(_APPLIED_VERBS_RE.search(text)),
    }
    for anchor in cluster:
        org_canon = _canonical_alnum(_extract_org(anchor))
        if org_canon and not _is_cert_platform(org_canon):
            signals["org_relationship"] = True
            break
    fired = sorted(k for k, v in signals.items() if v)
    rationale_prefix = (
        f"substance signals fired: {fired} ({len(fired)} of 4). "
    )
    return signals, rationale_prefix


def _decide_section(signals: dict[str, bool]) -> str:
    """Substance-based section decision.

    - **>=2 signals → ``"experience"``** (real work; anti-timidity).
    - **0-1 signals → ``"certifications"``** (genuinely thin / truly
      ambiguous middle → conservative default to NOT inflate to a
      job). Underclaiming a substantial multi-month program by
      burying it in a cert line is a real failure; the threshold of
      2 keeps the bar low enough to surface substance without
      promoting a bare certificate."""
    fired = sum(1 for v in signals.values() if v)
    return "experience" if fired >= 2 else "certifications"


class _ResolvedEntity(BaseModel):
    """One cluster's resolution. Internal to the planner — does NOT
    appear in the public ``PlanResult`` shape, but its decisions are
    surfaced via the chosen section's ``EntityAllocation.rationale``
    / the plan's ``notes``."""
    model_config = ConfigDict(extra="forbid")
    primary_anchor_id: str
    primary_entity_id: str
    primary_entity_display: str
    primary_anchor_type: FactType
    merged_anchor_ids: set[str]
    merged_entity_ids: set[str]
    chosen_section: str
    signals_fired: list[str]
    rationale: str


_SECTION_FOR_TYPE: dict[FactType, str] = {
    FactType.ROLE: "experience",
    FactType.CREDENTIAL: "certifications",
    FactType.EDUCATION: "education",
}


def _resolve_cross_section_conflicts(
    store: FactStore, valid_ids: set[str],
) -> tuple[list[_ResolvedEntity], dict[str, str]]:
    """Find same-entity facts of different anchor types and decide
    which section the merged entity should land in.

    Returns ``(resolved, suppressed)``:
      - ``resolved`` — one ``_ResolvedEntity`` per cross-type
        cluster, telling the section allocator which anchor wins
        and where.
      - ``suppressed`` — ``fact_id -> chosen_section`` for the
        NON-primary anchors of each cluster. A suppressed anchor's
        natural section (its type's section) must SKIP that fact
        so the entity appears once (in the chosen section), not
        twice.
    """
    candidates = [
        f for f in store.all()
        if f.id in valid_ids and f.type in _RESOLVER_ANCHOR_TYPES
    ]

    # Greedy clustering — walk the candidate list, place each fact in
    # the first cluster that contains a matching member, otherwise
    # start a new cluster. Conservative match (_same_entity) keeps
    # transitive closure honest enough for this scale (typical resume
    # has < 30 anchors).
    clusters: list[list[FactRecord]] = []
    for fact in candidates:
        placed = False
        for cluster in clusters:
            if any(_same_entity(fact, c) for c in cluster):
                cluster.append(fact)
                placed = True
                break
        if not placed:
            clusters.append([fact])

    resolved: list[_ResolvedEntity] = []
    suppressed: dict[str, str] = {}

    for cluster in clusters:
        types_present = {c.type for c in cluster}
        if len(types_present) < 2:
            # Single-type cluster — nothing to resolve; allocators
            # handle it via the usual path.
            continue

        signals, rationale_prefix = _score_substance(cluster, store)
        chosen = _decide_section(signals)

        # Pick the primary anchor:
        #   1. Prefer one whose natural section IS the chosen section.
        #   2. Then highest reliability (PLATFORM_VERIFIED >
        #      USER_ORIGINAL > ...).
        #   3. Then most children at its entity_id (richer evidence).
        #   4. Stable tiebreak on fact id.
        preferred_type_pool = [
            c for c in cluster
            if _SECTION_FOR_TYPE.get(c.type) == chosen
        ]
        if preferred_type_pool:
            primary_pool = preferred_type_pool
        else:
            # Promotion case: chosen section has no native anchor in
            # this cluster (e.g. all-credential cluster scored as
            # experience). Promote the strongest non-native anchor.
            primary_pool = list(cluster)
        primary = max(
            primary_pool,
            key=lambda c: (
                _RELIABILITY_RANK.get(c.source_reliability, 0),
                sum(
                    1 for f in store.by_entity(c.entity_id or "")
                    if f.id in valid_ids
                ),
                c.id,
            ),
        )

        rationale = (
            rationale_prefix
            + f"chose section={chosen!r} "
              f"(>=2 signals -> experience, else certifications). "
            + f"primary={primary.type.value} "
              f"{(primary.entity_display or primary.claim)!r} "
              f"(reliability={primary.source_reliability.value}). "
            + f"merged with anchors: "
              f"{sorted(c.type.value for c in cluster if c.id != primary.id)}."
        )
        resolved.append(_ResolvedEntity(
            primary_anchor_id=primary.id,
            primary_entity_id=primary.entity_id or "",
            primary_entity_display=(
                primary.entity_display or primary.claim
            ),
            primary_anchor_type=primary.type,
            merged_anchor_ids={c.id for c in cluster},
            merged_entity_ids={c.entity_id for c in cluster if c.entity_id},
            chosen_section=chosen,
            signals_fired=[k for k, v in signals.items() if v],
            rationale=rationale,
        ))

        # Suppress the non-primary anchors in their natural section
        # so the entity surfaces ONCE, in the chosen section.
        for c in cluster:
            if c.id == primary.id:
                continue
            suppressed[c.id] = chosen

    return resolved, suppressed


# ---------------------------------------------------------------------------
# build_plan — the main entry point.
# ---------------------------------------------------------------------------


def _filter_valid(
    facts: list[FactRecord], valid_ids: set[str],
) -> list[FactRecord]:
    return [f for f in facts if f.id in valid_ids]


def _ranked(
    facts: list[FactRecord],
    *,
    must_have_terms: list[str],
    nice_to_have_terms: list[str],
) -> list[FactRecord]:
    """Sort facts by combined rank, highest first. Stable tiebreak by id."""
    return sorted(
        facts,
        key=lambda f: (
            -_final_rank(f, must_have_terms=must_have_terms,
                         nice_to_have_terms=nice_to_have_terms)[0],
            f.id,
        ),
    )


def _rationale_for(fact: FactRecord, kind: str, extras: str = "") -> str:
    """One-line rationale string for the allocation. Surfaces in the
    plan output for explainability."""
    parts = [
        f"section={kind}",
        f"reliability={fact.source_reliability.value}",
    ]
    if fact.hedged:
        parts.append("hedged")
    if extras:
        parts.append(extras)
    return "; ".join(parts)


def build_plan(
    store: FactStore,
    *,
    job_must_have_skills: Optional[list[str]] = None,
    job_nice_to_have_skills: Optional[list[str]] = None,
    job_description: str = "",
    section_caps: Optional[dict[str, int]] = None,
    per_skill_mention_cap: int = DEFAULT_PER_SKILL_MENTION_CAP,
    per_entity_experience_cap: int = DEFAULT_PER_ENTITY_EXPERIENCE_CAP,
    today_ym: Optional[tuple[int, int]] = None,
) -> PlanResult:
    """Build the structured plan from a populated FactStore + a JD.

    Returns a ``PlanResult`` ready to hand to the section generator.
    Raises ``ValueError`` when the store is empty or every fact in it
    is anomalous (real failure, not skippable).

    JD signal: ``job_must_have_skills`` count double in the relevance
    score; ``job_nice_to_have_skills`` count single. ``job_description``
    is currently unused by the lexical scorer (reserved for an
    embedding-based replacement). When all three are absent, the
    planner falls back to a JD-relevance score of 0 for every fact —
    reliability + hedge alone drive the order.

    Caps:
      - ``section_caps`` (per-section count) defaults to
        ``DEFAULT_SECTION_CAPS``; override per-call as needed.
      - ``per_skill_mention_cap`` (global keyword anti-stuffing)
        defaults to ``DEFAULT_PER_SKILL_MENTION_CAP = 3``. A skill
        may appear once in the skills section plus up to N-1 times
        in other-section bullets.
    """
    if len(store) == 0:
        raise ValueError("resume_planner_v2: fact store is empty — nothing to plan")

    validation = validate_fact_store(store)
    valid_ids = set(validation.valid_fact_ids)
    if not valid_ids:
        raise ValueError(
            "resume_planner_v2: every fact in the store was anomalous — "
            f"plan aborted ({len(validation.anomalies)} anomalies)"
        )

    caps = {**DEFAULT_SECTION_CAPS, **(section_caps or {})}
    must = job_must_have_skills or []
    nice = job_nice_to_have_skills or []
    must_have_terms = [t for t in must if t]
    nice_to_have_terms = [t for t in nice if t]
    tracked_skills = set(must) | set(nice)
    mention_counter = _MentionCounter(tracked_skills, per_skill_mention_cap)

    all_valid = _filter_valid(store.all(), valid_ids)
    sections: dict[str, SectionPlan] = {}
    notes: list[str] = []

    # ---- Cross-section conflict resolver ----
    # Detect same-entity duplicates across anchor types (ROLE /
    # CREDENTIAL / EDUCATION) and decide which section the merged
    # entity belongs to by EXPERIENCE SUBSTANCE. Non-primary anchors
    # of each merged cluster are SUPPRESSED in their natural section
    # so the entity surfaces once, not twice. ``promoted_by_section``
    # lets a chosen section pick up anchors whose natural section was
    # different (e.g. a CREDENTIAL anchor promoted to experience).
    resolved_entities, suppressed_anchors = (
        _resolve_cross_section_conflicts(store, valid_ids)
    )
    promoted_by_section: dict[str, list[_ResolvedEntity]] = {
        "experience": [], "certifications": [], "education": [],
    }
    resolver_rationale_by_anchor: dict[str, str] = {}
    for re_ in resolved_entities:
        # The primary anchor's NATURAL section might or might not be
        # the chosen one. When they DIFFER, the primary is "promoted"
        # — the chosen section consumes it as an entity.
        natural = _SECTION_FOR_TYPE.get(re_.primary_anchor_type)
        if natural != re_.chosen_section:
            promoted_by_section[re_.chosen_section].append(re_)
        resolver_rationale_by_anchor[re_.primary_anchor_id] = re_.rationale
        notes.append(
            f"resolver: merged {len(re_.merged_anchor_ids)} anchor(s) "
            f"into one entity → section={re_.chosen_section!r}; "
            f"primary={re_.primary_entity_display!r}; "
            f"{re_.rationale}"
        )

    # ---- SKILLS section ----
    skill_facts = [f for f in all_valid if f.type == FactType.SKILL]
    ranked_skills = _ranked(
        skill_facts,
        must_have_terms=must_have_terms,
        nice_to_have_terms=nice_to_have_terms,
    )
    skills_alloc: list[FactAllocation] = []
    for fact in ranked_skills:
        if len(skills_alloc) >= caps["skills"]:
            break
        skills_alloc.append(FactAllocation(
            fact_id=fact.id,
            rationale=_rationale_for(fact, "skills",
                                     extras=f"claim={fact.claim!r}"),
            hedged=fact.hedged,
        ))
        mention_counter.record(fact)
    sections["skills"] = SectionPlan(section="skills", facts=skills_alloc)

    # ---- SUMMARY section ----
    # Pick the highest-ranked achievements + credentials; summary
    # establishes the candidate, so we draw from "marquee" facts.
    summary_pool = [
        f for f in all_valid
        if f.type in (FactType.ACHIEVEMENT, FactType.CREDENTIAL,
                      FactType.PROJECT, FactType.METRIC)
    ]
    ranked_summary = _ranked(
        summary_pool,
        must_have_terms=must_have_terms,
        nice_to_have_terms=nice_to_have_terms,
    )
    summary_alloc: list[FactAllocation] = []
    for fact in ranked_summary:
        if len(summary_alloc) >= caps["summary"]:
            break
        overflow = mention_counter.would_overflow(fact)
        if overflow:
            notes.append(
                f"summary: skipped fact {fact.id} (would exceed per-skill "
                f"mention cap on {sorted(overflow)})"
            )
            continue
        summary_alloc.append(FactAllocation(
            fact_id=fact.id,
            rationale=_rationale_for(fact, "summary"),
            hedged=fact.hedged,
        ))
        mention_counter.record(fact)
    sections["summary"] = SectionPlan(section="summary", facts=summary_alloc)

    # ---- EXPERIENCE section ----
    # Role-anchored entities, reverse-chronological by parsed end date.
    # The resolver may have:
    #   (a) suppressed a ROLE anchor (it got merged into a cluster
    #       whose section is certifications — skip it here), or
    #   (b) promoted a non-ROLE anchor (a CREDENTIAL/EDUCATION
    #       cluster scored as experience — surface it here as an
    #       entity).
    role_anchors = [
        f for f in all_valid
        if f.type == FactType.ROLE
        and suppressed_anchors.get(f.id) != "certifications"
        and suppressed_anchors.get(f.id) != "education"
    ]
    for promoted in promoted_by_section["experience"]:
        promoted_fact = store.get(promoted.primary_anchor_id)
        if promoted_fact is not None and promoted_fact.id in valid_ids:
            role_anchors.append(promoted_fact)
    if today_ym is None:
        import datetime as _dt
        d = _dt.date.today()
        today_ym = (d.year, d.month)
    # Stable sort: end_ym desc, then original-order asc (preserve
    # insertion order when dates are equal or missing).
    role_anchors.sort(
        key=lambda r: (
            -_role_end_yearmonth(r, today_ym)[0],
            -_role_end_yearmonth(r, today_ym)[1],
        )
    )
    # Map primary_anchor_id -> _ResolvedEntity for quick lookup so the
    # entity allocator can pull children from the WHOLE merged
    # cluster (a promoted CREDENTIAL's children + the role's
    # children, etc.) when this role was the result of resolution.
    resolver_by_primary: dict[str, _ResolvedEntity] = {
        re_.primary_anchor_id: re_ for re_ in resolved_entities
    }
    experience_entities: list[EntityAllocation] = []
    exp_budget = caps["experience"]
    used = 0
    for role in role_anchors:
        if used >= exp_budget:
            break
        eid = role.entity_id
        if not eid:
            continue
        # Pull the role's children (achievements + metrics + anything
        # else at the same entity_id, excluding the role anchor
        # itself). If the resolver merged this role with anchors at
        # OTHER entity_ids, union those entity_ids' children too —
        # so the merged entity's bullets surface together.
        resolved_for_role = resolver_by_primary.get(role.id)
        entity_ids_for_children = {eid}
        if resolved_for_role is not None:
            entity_ids_for_children |= resolved_for_role.merged_entity_ids
        children_all: list[FactRecord] = []
        seen_child_ids: set[str] = set()
        for ent_id in entity_ids_for_children:
            for f in store.by_entity(ent_id):
                if f.id in seen_child_ids:
                    continue
                seen_child_ids.add(f.id)
                children_all.append(f)
        merged_anchor_ids = (
            resolved_for_role.merged_anchor_ids
            if resolved_for_role is not None
            else {role.id}
        )
        children = [
            f for f in children_all
            if f.id not in merged_anchor_ids
            and f.id in valid_ids
            and f.type != FactType.METRIC
        ]
        ranked_children = _ranked(
            children,
            must_have_terms=must_have_terms,
            nice_to_have_terms=nice_to_have_terms,
        )
        slot_facts: list[FactAllocation] = []
        hedged_any = role.hedged
        # Per-entity cap — a single verbose role cannot monopolize
        # the section budget. The cap is on ALLOCATED facts, not on
        # the ranked-children pool size, so the highest-ranked facts
        # for each role still win the slots.
        per_entity_used = 0
        for fact in ranked_children:
            if used >= exp_budget:
                break
            if per_entity_used >= per_entity_experience_cap:
                notes.append(
                    f"experience[{eid}]: hit per-entity cap "
                    f"({per_entity_experience_cap}); remaining facts "
                    f"yielded to next role"
                )
                break
            overflow = mention_counter.would_overflow(fact)
            if overflow:
                notes.append(
                    f"experience[{eid}]: skipped fact {fact.id} "
                    f"(mention cap on {sorted(overflow)})"
                )
                continue
            slot_facts.append(FactAllocation(
                fact_id=fact.id,
                rationale=_rationale_for(fact, "experience",
                                         extras=f"entity={eid}"),
                hedged=fact.hedged,
            ))
            mention_counter.record(fact)
            hedged_any = hedged_any or fact.hedged
            used += 1
            per_entity_used += 1
        # Metric facts at this entity — pulled via the SAFETY accessor.
        # If the resolver merged additional entity_ids into this
        # entity, union their metrics too. The accessor's
        # cross-entity isolation still holds: each call only returns
        # the metrics bound to the specific entity_id queried.
        metric_ids: list[str] = []
        seen_metric_ids: set[str] = set()
        for ent_id in entity_ids_for_children:
            for m in store.metrics_for(ent_id):
                if m.id in valid_ids and m.id not in seen_metric_ids:
                    seen_metric_ids.add(m.id)
                    metric_ids.append(m.id)
        if any(store.get(mid).hedged for mid in metric_ids if store.get(mid)):
            hedged_any = True
        # Rationale picks up the resolver's substance-decision note
        # when this entity came from a cross-section merge.
        resolver_note = resolver_rationale_by_anchor.get(role.id, "")
        rationale_str = _rationale_for(
            role, "experience", extras=f"anchor entity={eid}",
        )
        if resolver_note:
            rationale_str += f"; resolver: {resolver_note}"
        experience_entities.append(EntityAllocation(
            entity_id=eid,
            entity_display=role.entity_display or eid,
            anchor_fact_id=role.id,
            facts=slot_facts,
            metric_fact_ids=metric_ids,
            rationale=rationale_str,
            hedged_any=hedged_any,
        ))
    sections["experience"] = SectionPlan(
        section="experience", entities=experience_entities,
    )

    # ---- PROJECTS section ----
    project_anchors = [f for f in all_valid if f.type == FactType.PROJECT]
    # Rank projects by combined score (JD-relevance + reliability +
    # evidence depth — proxied by the number of supporting facts at
    # the entity).
    def _project_rank(p: FactRecord) -> tuple[float, str]:
        base, _ = _final_rank(
            p,
            must_have_terms=must_have_terms,
            nice_to_have_terms=nice_to_have_terms,
        )
        # Evidence depth: count of valid non-anchor facts at the
        # project's entity_id. More facts → richer story.
        depth = sum(
            1 for f in store.by_entity(p.entity_id or "")
            if f.id in valid_ids and f.id != p.id
        )
        return (base + depth * 0.5, p.id)
    project_anchors.sort(key=lambda p: (-_project_rank(p)[0], p.id))
    proj_entities: list[EntityAllocation] = []
    proj_budget = caps["projects"]
    used = 0
    for project in project_anchors:
        if used >= proj_budget:
            break
        eid = project.entity_id
        if not eid:
            continue
        children_all = store.by_entity(eid)
        children = [f for f in children_all
                    if f.id != project.id and f.id in valid_ids
                    and f.type != FactType.METRIC]
        ranked_children = _ranked(
            children,
            must_have_terms=must_have_terms,
            nice_to_have_terms=nice_to_have_terms,
        )
        slot_facts: list[FactAllocation] = []
        hedged_any = project.hedged
        for fact in ranked_children:
            if used >= proj_budget:
                break
            overflow = mention_counter.would_overflow(fact)
            if overflow:
                notes.append(
                    f"projects[{eid}]: skipped fact {fact.id} "
                    f"(mention cap on {sorted(overflow)})"
                )
                continue
            slot_facts.append(FactAllocation(
                fact_id=fact.id,
                rationale=_rationale_for(fact, "projects",
                                         extras=f"entity={eid}"),
                hedged=fact.hedged,
            ))
            mention_counter.record(fact)
            hedged_any = hedged_any or fact.hedged
            used += 1
        metric_ids = [
            m.id for m in store.metrics_for(eid) if m.id in valid_ids
        ]
        if any(store.get(mid).hedged for mid in metric_ids if store.get(mid)):
            hedged_any = True
        proj_entities.append(EntityAllocation(
            entity_id=eid,
            entity_display=project.entity_display or eid,
            anchor_fact_id=project.id,
            facts=slot_facts,
            metric_fact_ids=metric_ids,
            rationale=_rationale_for(project, "projects",
                                     extras=f"anchor entity={eid}"),
            hedged_any=hedged_any,
        ))
    sections["projects"] = SectionPlan(
        section="projects", entities=proj_entities,
    )

    # ---- EDUCATION section ----
    # Suppress EDUCATION anchors that got merged into a non-education
    # cluster (e.g. an entry that's also a ROLE and scored as
    # experience).
    edu_facts = [
        f for f in all_valid
        if f.type == FactType.EDUCATION
        and suppressed_anchors.get(f.id) != "experience"
        and suppressed_anchors.get(f.id) != "certifications"
    ]
    ranked_edu = _ranked(
        edu_facts,
        must_have_terms=must_have_terms,
        nice_to_have_terms=nice_to_have_terms,
    )
    edu_alloc = [
        FactAllocation(
            fact_id=f.id,
            rationale=_rationale_for(f, "education"),
            hedged=f.hedged,
        )
        for f in ranked_edu[: caps["education"]]
    ]
    sections["education"] = SectionPlan(section="education", facts=edu_alloc)

    # ---- CERTIFICATIONS section ----
    # Suppress CREDENTIAL anchors that got merged into a non-cert
    # cluster (the typical case: a credential whose substance scored
    # high enough to land in experience). Platform-verified
    # credentials outrank others for the same slot by virtue of the
    # reliability rank in _final_rank.
    cred_facts = [
        f for f in all_valid
        if f.type == FactType.CREDENTIAL
        and suppressed_anchors.get(f.id) != "experience"
        and suppressed_anchors.get(f.id) != "education"
    ]
    ranked_creds = _ranked(
        cred_facts,
        must_have_terms=must_have_terms,
        nice_to_have_terms=nice_to_have_terms,
    )
    cred_alloc = [
        FactAllocation(
            fact_id=f.id,
            rationale=_rationale_for(f, "certifications"),
            hedged=f.hedged,
        )
        for f in ranked_creds[: caps["certifications"]]
    ]
    sections["certifications"] = SectionPlan(
        section="certifications", facts=cred_alloc,
    )

    return PlanResult(
        sections=sections,
        validation=validation,
        anti_overrep_stats=mention_counter.stats(),
        ranking_method="lexical_jd_overlap_v1",
        notes=notes,
    )
