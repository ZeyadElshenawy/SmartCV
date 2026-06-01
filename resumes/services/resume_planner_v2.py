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
    role_anchors = [f for f in all_valid if f.type == FactType.ROLE]
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
        # else at the same entity_id, excluding the role anchor itself).
        children_all = store.by_entity(eid)
        children = [f for f in children_all
                    if f.id != role.id and f.id in valid_ids
                    and f.type != FactType.METRIC]
        ranked_children = _ranked(
            children,
            must_have_terms=must_have_terms,
            nice_to_have_terms=nice_to_have_terms,
        )
        slot_facts: list[FactAllocation] = []
        hedged_any = role.hedged
        for fact in ranked_children:
            if used >= exp_budget:
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
        # Metric facts at this entity — pulled via the SAFETY accessor.
        # No metric from another entity can appear here. Filter to valid.
        metric_ids = [
            m.id for m in store.metrics_for(eid) if m.id in valid_ids
        ]
        if any(store.get(mid).hedged for mid in metric_ids if store.get(mid)):
            hedged_any = True
        experience_entities.append(EntityAllocation(
            entity_id=eid,
            entity_display=role.entity_display or eid,
            anchor_fact_id=role.id,
            facts=slot_facts,
            metric_fact_ids=metric_ids,
            rationale=_rationale_for(role, "experience",
                                     extras=f"anchor entity={eid}"),
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
    edu_facts = [f for f in all_valid if f.type == FactType.EDUCATION]
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
    # Platform-verified credentials outrank others for the same slot
    # by virtue of the reliability rank in _final_rank.
    cred_facts = [f for f in all_valid if f.type == FactType.CREDENTIAL]
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
