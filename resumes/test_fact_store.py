"""Isolation tests for resumes.services.fact_store.

Hand-written fact records only. No LLM, no pipeline, no consumers.
The fact store is the v2 evidence graph's foundation; these tests
prove its structural safety guarantees before anything is wired up.
"""

from django.test import SimpleTestCase
from pydantic import ValidationError

from resumes.services.fact_store import (
    FactRecord,
    FactStore,
    FactType,
    SourceReliability,
)


# ---------------------------------------------------------------------------
# Helpers (test fixtures only)
# ---------------------------------------------------------------------------


def _project_fact(
    *, id="p1", claim="Healthcare-prediction app on Flask + MLflow.",
    entity_id="https://github.com/zeyad/healthcare-prediction-depi",
    entity_display="Healthcare Prediction (DEPI)",
    source="github_readme:healthcare-prediction-depi",
    source_reliability=SourceReliability.USER_ORIGINAL,
    evidence_quote="A healthcare prediction app built with Flask and tracked in MLflow.",
):
    return FactRecord(
        id=id, type=FactType.PROJECT, claim=claim,
        entity_id=entity_id, entity_display=entity_display,
        source=source, source_reliability=source_reliability,
        evidence_quote=evidence_quote,
    )


def _metric_fact(
    *, id="m1", entity_id="https://github.com/zeyad/healthcare-prediction-depi",
    claim="0.89 ROC-AUC on held-out validation set.",
    value=0.89, unit="ROC-AUC",
    source="github_readme:healthcare-prediction-depi",
    source_reliability=SourceReliability.USER_ORIGINAL,
    evidence_quote="Achieved 0.89 ROC-AUC on the held-out validation set.",
):
    return FactRecord(
        id=id, type=FactType.METRIC, claim=claim,
        value=value, unit=unit,
        entity_id=entity_id, entity_display="Healthcare Prediction (DEPI)",
        source=source, source_reliability=source_reliability,
        evidence_quote=evidence_quote,
    )


# ---------------------------------------------------------------------------
# Validation rules (hard rejection paths)
# ---------------------------------------------------------------------------


class FactRecordValidationTests(SimpleTestCase):
    """Hard structural validation. These are not advisory — Pydantic
    raises at construction so an invalid fact CANNOT enter the store."""

    def test_metric_without_entity_id_is_rejected(self):
        """The Banque Misr / cross-attachment lesson encoded in the
        data model: a metric must point at one specific entity."""
        with self.assertRaises(ValidationError) as cm:
            FactRecord(
                id="m_floating", type=FactType.METRIC,
                claim="0.89 ROC-AUC.", value=0.89, unit="ROC-AUC",
                entity_id="",   # ← floating metric
                source="github_readme:foo",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote="Achieved 0.89 ROC-AUC.",
            )
        msg = str(cm.exception)
        self.assertIn("metric fact must have a non-empty entity_id", msg)

    def test_metric_with_whitespace_only_entity_id_is_rejected(self):
        with self.assertRaises(ValidationError):
            FactRecord(
                id="m_floating", type=FactType.METRIC,
                claim="x", value=1, unit="%",
                entity_id="    ",
                source="cv", source_reliability=SourceReliability.INFERRED,
                evidence_quote="…",
            )

    def test_empty_evidence_quote_is_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            FactRecord(
                id="x", type=FactType.SKILL, claim="Python",
                source="old_cv", source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote="",   # ← no evidence
            )
        self.assertIn("evidence_quote", str(cm.exception))

    def test_whitespace_only_evidence_quote_is_rejected(self):
        with self.assertRaises(ValidationError):
            FactRecord(
                id="x", type=FactType.SKILL, claim="Python",
                source="old_cv", source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote="   \t\n  ",
            )

    def test_non_metric_facts_do_not_require_entity_id(self):
        """A skill ('Python') is not bound to a single entity — it can
        come from the CV as a whole. The entity_id requirement applies
        ONLY to metrics."""
        f = FactRecord(
            id="s1", type=FactType.SKILL, claim="Python",
            source="old_cv", source_reliability=SourceReliability.USER_ORIGINAL,
            evidence_quote="Skills: Python, SQL, Pandas.",
        )
        self.assertEqual(f.entity_id, "")

    def test_unknown_source_reliability_coerces_to_tutorial_derived(self):
        """The safety rule: an unparseable reliability label must NEVER
        promote to user_original. Coerce DOWN to tutorial_derived so
        a 0.89 ROC-AUC from a sketchy source can't be laundered."""
        f = FactRecord(
            id="x", type=FactType.SKILL, claim="Python",
            source="old_cv",
            source_reliability="invented-tier",   # ← unknown
            evidence_quote="ok",
        )
        self.assertEqual(f.source_reliability, SourceReliability.TUTORIAL_DERIVED)
        self.assertNotEqual(f.source_reliability, SourceReliability.USER_ORIGINAL)

    def test_missing_source_reliability_coerces_to_tutorial_derived(self):
        f = FactRecord(
            id="x", type=FactType.SKILL, claim="Python",
            source="old_cv",
            source_reliability=None,   # ← absent
            evidence_quote="ok",
        )
        self.assertEqual(f.source_reliability, SourceReliability.TUTORIAL_DERIVED)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class FactStoreSerializationTests(SimpleTestCase):

    def test_round_trip_json(self):
        store = FactStore()
        store.add(_project_fact())
        store.add(_metric_fact())
        store.add(FactRecord(
            id="s1", type=FactType.SKILL, claim="Python",
            source="old_cv", source_reliability=SourceReliability.USER_ORIGINAL,
            evidence_quote="Skills section listed Python.",
        ))
        raw = store.to_json()
        rebuilt = FactStore.from_json(raw)
        self.assertEqual(len(rebuilt), len(store))
        # Verify the metric survived intact with its entity binding.
        metrics = rebuilt.metrics_for("https://github.com/zeyad/healthcare-prediction-depi")
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].value, 0.89)
        self.assertEqual(metrics[0].unit, "ROC-AUC")

    def test_to_json_payload_has_version_and_facts(self):
        import json
        store = FactStore()
        store.add(_project_fact())
        payload = json.loads(store.to_json())
        self.assertIn("version", payload)
        self.assertIn("facts", payload)
        self.assertEqual(len(payload["facts"]), 1)

    def test_from_json_rejects_payload_without_facts_key(self):
        with self.assertRaises(ValueError):
            FactStore.from_json('{"version": 1}')


# ---------------------------------------------------------------------------
# Dedup + reliability tiebreak
# ---------------------------------------------------------------------------


class FactStoreDedupTests(SimpleTestCase):
    """``(type, normalized_claim, entity_id)`` is the dedup key. On
    collapse, the higher-reliability source wins. Insertion order
    of survivors is preserved across upgrade-replacements so callers
    iterating the store see a stable order."""

    def test_same_claim_same_entity_collapses_one_record(self):
        store = FactStore()
        first = _metric_fact(
            id="m_low", source_reliability=SourceReliability.TUTORIAL_DERIVED,
        )
        second = _metric_fact(
            id="m_high", source_reliability=SourceReliability.USER_ORIGINAL,
        )
        store.add(first)
        store.add(second)
        self.assertEqual(len(store), 1)
        survivor = store.all()[0]
        self.assertEqual(survivor.id, "m_high")
        self.assertEqual(survivor.source_reliability, SourceReliability.USER_ORIGINAL)

    def test_higher_reliability_wins_regardless_of_insertion_order(self):
        store = FactStore()
        store.add(_metric_fact(
            id="m_high", source_reliability=SourceReliability.PLATFORM_VERIFIED,
        ))
        store.add(_metric_fact(
            id="m_low", source_reliability=SourceReliability.INFERRED,
        ))
        self.assertEqual(len(store), 1)
        self.assertEqual(store.all()[0].id, "m_high")

    def test_normalized_claim_dedup_is_case_and_whitespace_insensitive(self):
        store = FactStore()
        store.add(FactRecord(
            id="s_a", type=FactType.SKILL, claim="Python",
            source="old_cv", source_reliability=SourceReliability.USER_ORIGINAL,
            evidence_quote="ok",
        ))
        store.add(FactRecord(
            id="s_b", type=FactType.SKILL, claim="  python  ",
            source="github_readme:repo", source_reliability=SourceReliability.INFERRED,
            evidence_quote="ok2",
        ))
        self.assertEqual(len(store), 1)

    def test_different_entity_ids_do_not_collapse(self):
        """Same claim, different entity → different facts (each metric
        belongs to its own entity)."""
        store = FactStore()
        store.add(_metric_fact(id="m_a", entity_id="entity_A"))
        store.add(_metric_fact(id="m_b", entity_id="entity_B"))
        self.assertEqual(len(store), 2)

    def test_different_types_do_not_collapse(self):
        """A 'Python' skill and a 'Python' achievement (if both existed)
        live separately — they're different fact types."""
        store = FactStore()
        store.add(FactRecord(
            id="a", type=FactType.SKILL, claim="Built X",
            source="cv", source_reliability=SourceReliability.USER_ORIGINAL,
            evidence_quote="ok",
        ))
        store.add(FactRecord(
            id="b", type=FactType.ACHIEVEMENT, claim="Built X",
            source="cv", source_reliability=SourceReliability.USER_ORIGINAL,
            evidence_quote="ok",
        ))
        self.assertEqual(len(store), 2)


# ---------------------------------------------------------------------------
# SAFETY accessor: metrics_for never returns another entity's metrics
# ---------------------------------------------------------------------------


class MetricsForSafetyTests(SimpleTestCase):
    """The structural guarantee that prevents the v1 Banque Misr-style
    cross-attachment in v2 by construction."""

    def test_metrics_for_returns_only_entity_bound_metrics(self):
        store = FactStore()
        store.add(_metric_fact(id="m_A1", entity_id="entity_A",
                               claim="0.89 ROC-AUC.", value=0.89))
        store.add(_metric_fact(id="m_A2", entity_id="entity_A",
                               claim="20k rows processed.", value=20000, unit="rows"))
        store.add(_metric_fact(id="m_B", entity_id="entity_B",
                               claim="0.351 silhouette.", value=0.351, unit="silhouette"))

        out_a = store.metrics_for("entity_A")
        self.assertEqual(len(out_a), 2)
        for f in out_a:
            self.assertEqual(f.entity_id, "entity_A")

        out_b = store.metrics_for("entity_B")
        self.assertEqual(len(out_b), 1)
        self.assertEqual(out_b[0].entity_id, "entity_B")
        self.assertNotIn(0.89, [f.value for f in out_b])
        self.assertNotIn(20000, [f.value for f in out_b])

    def test_metrics_for_unknown_entity_returns_empty(self):
        store = FactStore()
        store.add(_metric_fact(id="m_A", entity_id="entity_A"))
        self.assertEqual(store.metrics_for("entity_Z"), [])
        self.assertEqual(store.metrics_for(""), [])
        self.assertEqual(store.metrics_for(None), [])

    def test_metrics_for_excludes_non_metric_facts_on_same_entity(self):
        """A SKILL or ACHIEVEMENT on the same entity must NOT show up
        in metrics_for. The accessor is type-discriminating."""
        store = FactStore()
        store.add(_project_fact())   # entity_id matches
        store.add(_metric_fact())    # entity_id matches; type=METRIC
        store.add(FactRecord(
            id="ach1", type=FactType.ACHIEVEMENT,
            claim="Shipped to production for the DEPI cohort.",
            entity_id="https://github.com/zeyad/healthcare-prediction-depi",
            source="github_readme:healthcare-prediction-depi",
            source_reliability=SourceReliability.USER_ORIGINAL,
            evidence_quote="Production-deployed for the DEPI cohort.",
        ))
        out = store.metrics_for("https://github.com/zeyad/healthcare-prediction-depi")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].type, FactType.METRIC)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


class FactStoreQueryTests(SimpleTestCase):

    def setUp(self):
        self.store = FactStore()
        self.store.add(_project_fact(id="p1"))
        self.store.add(_metric_fact(id="m1"))
        self.store.add(FactRecord(
            id="s1", type=FactType.SKILL, claim="Flask",
            source="github_readme:healthcare-prediction-depi",
            source_reliability=SourceReliability.USER_ORIGINAL,
            evidence_quote="Built with Flask.",
        ))
        self.store.add(FactRecord(
            id="r1", type=FactType.ROLE, claim="AI Trainee at DEPI",
            entity_id="depi|ai_trainee",
            source="old_cv", source_reliability=SourceReliability.USER_ORIGINAL,
            evidence_quote="AI Trainee at DEPI, 2025.",
        ))

    def test_by_entity(self):
        out = self.store.by_entity(
            "https://github.com/zeyad/healthcare-prediction-depi"
        )
        # Project + metric are bound to this entity. Skill 's1' has
        # empty entity_id so it's not bound to anything queryable here.
        self.assertEqual({f.id for f in out}, {"p1", "m1"})

    def test_by_type(self):
        self.assertEqual({f.id for f in self.store.by_type(FactType.METRIC)}, {"m1"})
        self.assertEqual({f.id for f in self.store.by_type(FactType.ROLE)}, {"r1"})
        self.assertEqual(
            {f.id for f in self.store.by_type(FactType.PROJECT)}, {"p1"},
        )

    def test_by_reliability(self):
        rs = self.store.by_reliability(SourceReliability.USER_ORIGINAL)
        self.assertEqual(len(rs), 4)
        self.assertEqual(self.store.by_reliability(SourceReliability.INFERRED), [])

    def test_entities_returns_distinct_ids_in_first_seen_order(self):
        ents = self.store.entities()
        self.assertIn("https://github.com/zeyad/healthcare-prediction-depi", ents)
        self.assertIn("depi|ai_trainee", ents)
        # Skill 's1' had empty entity_id; not included.
        self.assertEqual(len(ents), 2)


# ---------------------------------------------------------------------------
# The worked example from the design discussion.
# Healthcare-prediction-depi: project + skill + achievement + metric.
# ---------------------------------------------------------------------------


class HealthcarePredictionWorkedExampleTests(SimpleTestCase):
    """End-to-end on hand-written facts: store the 4 records, prove
    the metric is bound to the project's entity_id, prove the
    safety queries return the right things, prove JSON round-trips."""

    PROJECT_ENTITY = "https://github.com/zeyad/healthcare-prediction-depi"
    PROJECT_DISPLAY = "Healthcare Prediction (DEPI)"

    def _build_facts(self):
        return [
            FactRecord(
                id="hp_project",
                type=FactType.PROJECT,
                claim="Healthcare-prediction app built with Flask and tracked in MLflow.",
                entity_id=self.PROJECT_ENTITY,
                entity_display=self.PROJECT_DISPLAY,
                source="github_readme:healthcare-prediction-depi",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote=(
                    "A healthcare prediction app built with Flask, tracked "
                    "experiments in MLflow."
                ),
            ),
            FactRecord(
                id="hp_skill_flask",
                type=FactType.SKILL,
                claim="Flask",
                entity_id=self.PROJECT_ENTITY,
                entity_display=self.PROJECT_DISPLAY,
                source="github_readme:healthcare-prediction-depi",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote="Built with Flask.",
            ),
            FactRecord(
                id="hp_achievement",
                type=FactType.ACHIEVEMENT,
                claim="Shipped end-to-end pipeline from data ingestion through serving.",
                entity_id=self.PROJECT_ENTITY,
                entity_display=self.PROJECT_DISPLAY,
                source="github_readme:healthcare-prediction-depi",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote=(
                    "End-to-end pipeline: ingestion → preprocessing → "
                    "training → serving via Flask."
                ),
            ),
            FactRecord(
                id="hp_metric_rocauc",
                type=FactType.METRIC,
                claim="0.89 ROC-AUC on held-out validation set.",
                value=0.89,
                unit="ROC-AUC",
                entity_id=self.PROJECT_ENTITY,
                entity_display=self.PROJECT_DISPLAY,
                source="github_readme:healthcare-prediction-depi",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote="Achieved 0.89 ROC-AUC on the held-out validation set.",
            ),
        ]

    def test_all_four_facts_stored(self):
        store = FactStore()
        store.add_many(self._build_facts())
        self.assertEqual(len(store), 4)
        ids = {f.id for f in store.all()}
        self.assertEqual(
            ids,
            {"hp_project", "hp_skill_flask", "hp_achievement", "hp_metric_rocauc"},
        )

    def test_metric_is_bound_to_the_project_entity(self):
        store = FactStore()
        store.add_many(self._build_facts())
        metrics = store.metrics_for(self.PROJECT_ENTITY)
        self.assertEqual(len(metrics), 1)
        m = metrics[0]
        self.assertEqual(m.value, 0.89)
        self.assertEqual(m.unit, "ROC-AUC")
        self.assertEqual(m.entity_id, self.PROJECT_ENTITY)

    def test_metric_does_not_leak_to_an_unrelated_entity(self):
        store = FactStore()
        store.add_many(self._build_facts())
        # A different project's entity_id should yield zero metrics.
        self.assertEqual(
            store.metrics_for("https://github.com/zeyad/different-project"),
            [],
        )
        self.assertEqual(store.metrics_for("entity_B"), [])

    def test_by_entity_returns_all_four_facts(self):
        store = FactStore()
        store.add_many(self._build_facts())
        ents = store.by_entity(self.PROJECT_ENTITY)
        self.assertEqual(len(ents), 4)
        kinds = {f.type for f in ents}
        self.assertEqual(
            kinds,
            {FactType.PROJECT, FactType.SKILL, FactType.ACHIEVEMENT, FactType.METRIC},
        )

    def test_worked_example_round_trips_through_json(self):
        store = FactStore()
        store.add_many(self._build_facts())
        rebuilt = FactStore.from_json(store.to_json())
        self.assertEqual(len(rebuilt), 4)
        metrics = rebuilt.metrics_for(self.PROJECT_ENTITY)
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].value, 0.89)
        self.assertEqual(metrics[0].unit, "ROC-AUC")
