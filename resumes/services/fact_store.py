"""Atomic-fact store for the v2 evidence-first resume pipeline.

Pipeline shape:

    ingest → extract atomic facts → FACT STORE (this module)
        → global plan → section generation → review/regen → assemble

The fact store is the contract every later stage reads/writes.
**Phrasing free, facts locked**: the generator may only emit claims
backed by a fact record. Provenance is enforced in the data model.

Three structural safety guarantees are encoded HERE rather than in
the generator:

1. ``evidence_quote`` is required on every fact (no quote → reject).
   The phantom-role bug ("Banque Misr" on a resume whose master
   profile has no Banque Misr role) was a downstream symptom of the
   v1 pipeline letting LLM output flow without evidence binding.

2. A ``type == METRIC`` fact MUST carry an ``entity_id`` (otherwise
   the metric is "floating" and can land on any item in a later
   stage). Cross-attachment is structurally impossible if metric
   lookups go through ``FactStore.metrics_for(entity_id)``.

3. Unknown / unparseable ``source_reliability`` coerces to
   ``TUTORIAL_DERIVED`` (the safer label), NEVER ``USER_ORIGINAL``.
   Fail toward honesty — a tutorial-derived 0.89 ROC-AUC must not
   silently promote to a user-authored claim.

This module is **isolated**: nothing in the existing resume_generator,
inclusion_planner, normalizer, or supervised loop depends on it. The
v1 pipeline keeps running; v2 builds on top of this when ready.
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from enum import Enum
from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FactType(str, Enum):
    SKILL = "skill"
    ACHIEVEMENT = "achievement"
    METRIC = "metric"
    ROLE = "role"
    EDUCATION = "education"
    PROJECT = "project"
    CREDENTIAL = "credential"


class SourceReliability(str, Enum):
    """Ranked confidence in the source's authority over the fact.

    Ordered (high → low) for dedup tiebreak purposes:

      ``PLATFORM_VERIFIED`` — Kaggle competition rank, Scholar citation
        count, public GitHub star counts — externally confirmed.
      ``USER_ORIGINAL``    — user's own work / own README / typed CV.
      ``TUTORIAL_DERIVED`` — a course / tutorial / following-along
        repo. Metrics are SUSPECT (they came from the tutorial, not
        the user's experiment). This is the safer default for
        unknown sources.
      ``INFERRED``         — extractor inference, not explicit in the
        source text. Lowest confidence.
    """
    PLATFORM_VERIFIED = "platform_verified"
    USER_ORIGINAL = "user_original"
    TUTORIAL_DERIVED = "tutorial_derived"
    INFERRED = "inferred"

    @classmethod
    def coerce(cls, value) -> "SourceReliability":
        """Unknown / unparseable values default to ``TUTORIAL_DERIVED``
        — the safer label. Never promote to ``USER_ORIGINAL`` on
        ambiguity (fail toward honesty)."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().lower())
            except ValueError:
                pass
        return cls.TUTORIAL_DERIVED


# Dedup tiebreak ranking. Higher wins.
_RELIABILITY_RANK = {
    SourceReliability.PLATFORM_VERIFIED: 4,
    SourceReliability.USER_ORIGINAL: 3,
    SourceReliability.TUTORIAL_DERIVED: 2,
    SourceReliability.INFERRED: 1,
}


# ---------------------------------------------------------------------------
# FactRecord
# ---------------------------------------------------------------------------


def _normalize_claim_for_dedup(s: str) -> str:
    """Dedup key normalization — case-insensitive, whitespace-collapsed.
    Two records with the same (type, normalized_claim, entity_id)
    collapse to one in the store."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


class FactRecord(BaseModel):
    """One atomic fact in the evidence graph.

    Every generator claim must trace to a FactRecord. The model
    enforces the safety rules so the contract isn't a comment, it's
    structural: a metric without an entity_id WILL raise at
    construction time; an empty evidence_quote WILL raise.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    id: str = Field(..., min_length=1, description="Stable unique id.")
    type: FactType
    claim: str = Field(
        ..., min_length=1,
        description='Human-readable assertion. "Reduced nightly data load by 6 hours."',
    )

    # Structured metric payload — non-null when type == METRIC.
    value: Optional[float] = Field(
        default=None,
        description="Numeric value when this fact carries a measurement.",
    )
    unit: Optional[str] = Field(
        default=None,
        description='Unit for `value`, e.g. "%", "ROC-AUC", "hours", "users".',
    )

    # Entity binding — the join key that prevents cross-attachment.
    entity_id: str = Field(
        default="",
        description=(
            "Stable key of the role/project/credential this fact attaches "
            "to. Convention: project URL, normalized (company, title) "
            "for roles. REQUIRED for METRIC facts; optional for others. "
            "This is the key that prevents a metric from one project "
            "landing on another."
        ),
    )
    entity_display: str = Field(
        default="",
        description=(
            "Human display name (e.g. 'Healthcare Prediction (DEPI)'), "
            "kept SEPARATE from entity_id so the join key is never "
            "coupled to display formatting drift."
        ),
    )

    # Provenance.
    source: str = Field(
        ..., min_length=1,
        description="Origin tag: old_cv | github_readme:<repo> | kaggle | scholar | linkedin.",
    )
    source_reliability: SourceReliability = SourceReliability.TUTORIAL_DERIVED
    evidence_quote: str = Field(
        ...,
        description=(
            "Raw source text the fact was extracted from. Required "
            "non-empty. Traceability for grounding and defense."
        ),
    )

    # Extractor confidence (0.0 - 1.0). Distinct from source_reliability:
    # confidence is the extractor's certainty that this fact was correctly
    # parsed from the source; source_reliability is how trustworthy the
    # source itself is. Both can be low.
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    hedged: bool = Field(
        default=False,
        description=(
            "Source hedged the claim ('~90%', 'aims to', restated-from-paper). "
            "A hedged metric must NEVER launder into a confident bullet."
        ),
    )

    # ---- validators ----

    @field_validator("source_reliability", mode="before")
    @classmethod
    def _coerce_reliability(cls, v):
        return SourceReliability.coerce(v)

    @field_validator("evidence_quote")
    @classmethod
    def _require_evidence(cls, v):
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                "evidence_quote must be a non-empty string — a fact "
                "without source text is rejected."
            )
        return v.strip()

    @model_validator(mode="after")
    def _metric_must_have_entity(self):
        if self.type == FactType.METRIC:
            if not (self.entity_id or "").strip():
                raise ValueError(
                    "metric fact must have a non-empty entity_id — a "
                    "floating metric is rejected (this is the data-model "
                    "rule that prevents cross-attachment of metrics)."
                )
        return self

    # ---- derived ----

    @property
    def reliability_rank(self) -> int:
        return _RELIABILITY_RANK.get(self.source_reliability, 0)


# ---------------------------------------------------------------------------
# FactStore
# ---------------------------------------------------------------------------


class FactStore:
    """Accumulator + dedup + safe queries for FactRecords.

    Insertion order preserved. Dedup key is
    ``(type, normalized_claim, entity_id)``; on collision the
    higher-reliability source wins (ties broken by insertion order).

    The store's purpose isn't just storage — it's the **safe-query API**
    the v2 generator will use. ``metrics_for(entity_id)`` returns metric
    facts bound to THAT entity only. By construction it cannot return
    a metric bound to a different entity, so a generator that sources
    numbers exclusively through this accessor cannot cross-attach.
    """

    def __init__(self) -> None:
        self._by_id: "OrderedDict[str, FactRecord]" = OrderedDict()
        self._by_dedup_key: dict[tuple, str] = {}

    # ---- mutations ----

    def add(self, fact: FactRecord) -> str:
        """Add one fact. Dedup-collapses against the existing store.

        Returns the surviving fact's id (either the incoming fact's
        id when it wins, or the existing fact's id when the existing
        record's reliability is higher-or-equal).
        """
        key = (fact.type, _normalize_claim_for_dedup(fact.claim), fact.entity_id or "")
        existing_id = self._by_dedup_key.get(key)
        if existing_id is None:
            self._by_id[fact.id] = fact
            self._by_dedup_key[key] = fact.id
            return fact.id

        existing = self._by_id[existing_id]
        if fact.reliability_rank > existing.reliability_rank:
            logger.info(
                "fact_store: dedup collapse — upgrading reliability "
                "%s -> %s for claim=%r entity=%r",
                existing.source_reliability.value,
                fact.source_reliability.value,
                fact.claim[:80], fact.entity_id,
            )
            # Replace the record. Preserve insertion position of the
            # original key in OrderedDict to keep iteration stable.
            new_dict: "OrderedDict[str, FactRecord]" = OrderedDict()
            for k, v in self._by_id.items():
                if k == existing_id:
                    new_dict[fact.id] = fact
                else:
                    new_dict[k] = v
            self._by_id = new_dict
            self._by_dedup_key[key] = fact.id
            return fact.id

        logger.info(
            "fact_store: dedup collapse — kept existing (%s) over new (%s) "
            "for claim=%r entity=%r",
            existing.source_reliability.value,
            fact.source_reliability.value,
            fact.claim[:80], fact.entity_id,
        )
        return existing_id

    def add_many(self, facts: Iterable[FactRecord]) -> list[str]:
        return [self.add(f) for f in facts]

    # ---- generic accessors ----

    def get(self, fact_id: str) -> Optional[FactRecord]:
        return self._by_id.get(fact_id)

    def all(self) -> list[FactRecord]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, fact_id: str) -> bool:
        return fact_id in self._by_id

    # ---- query helpers ----

    def by_entity(self, entity_id: str) -> list[FactRecord]:
        """All facts bound to a specific entity (project, role, …)."""
        eid = (entity_id or "").strip()
        if not eid:
            return []
        return [f for f in self._by_id.values() if f.entity_id == eid]

    def by_type(self, fact_type: FactType) -> list[FactRecord]:
        return [f for f in self._by_id.values() if f.type == fact_type]

    def by_reliability(self, reliability: SourceReliability) -> list[FactRecord]:
        return [
            f for f in self._by_id.values()
            if f.source_reliability == reliability
        ]

    def entities(self) -> list[str]:
        """Distinct non-empty entity_ids present in the store, in
        first-seen order."""
        seen: list[str] = []
        seen_set: set[str] = set()
        for f in self._by_id.values():
            if f.entity_id and f.entity_id not in seen_set:
                seen.append(f.entity_id)
                seen_set.add(f.entity_id)
        return seen

    # ---- SAFETY accessor ----

    def metrics_for(self, entity_id: str) -> list[FactRecord]:
        """Metric facts bound to THIS entity only — never another.

        This is THE API the v2 generator uses to look up "what numbers
        can I cite for this project/role?". By construction, the
        answer is correct: every metric in the returned list has
        ``entity_id == entity_id`` (verified at FactRecord construction
        time, since metric facts without an entity_id are rejected).

        Returns an empty list when ``entity_id`` is empty or no
        metric is bound to it. A generator that gates every numeric
        claim on a non-empty return from this method cannot
        cross-attach metrics between items.
        """
        eid = (entity_id or "").strip()
        if not eid:
            return []
        return [
            f for f in self._by_id.values()
            if f.type == FactType.METRIC and f.entity_id == eid
        ]

    # ---- serialization ----

    def to_json(self) -> str:
        payload = {
            "version": 1,
            "facts": [
                f.model_dump(mode="json") for f in self._by_id.values()
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "FactStore":
        data = json.loads(raw)
        if not isinstance(data, dict) or "facts" not in data:
            raise ValueError("FactStore.from_json: payload missing 'facts' key")
        store = cls()
        for item in data["facts"]:
            store.add(FactRecord(**item))
        return store
