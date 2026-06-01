"""Isolation tests for resumes.services.resume_planner_v2.

Hand-built FactStores, no LLM. The planner consumes a populated
store + a JD signal and produces a structured PlanResult. Tests target:

  - validate_fact_store anomaly detection + drop-not-crash policy
  - ranking respects source_reliability + JD relevance
  - hedge flag propagates forward
  - anti-over-representation cap holds across sections
  - metric facts attach only via store.metrics_for (cross-attach
    impossible by construction)
  - experience entities ordered reverse-chronologically
  - empty / all-anomalous store raises
"""

from django.test import SimpleTestCase

from resumes.services.fact_store import (
    FactRecord,
    FactStore,
    FactType,
    SourceReliability,
)
from resumes.services.resume_planner_v2 import (
    DEFAULT_PER_SKILL_MENTION_CAP,
    DEFAULT_SECTION_CAPS,
    PlanResult,
    SectionPlan,
    ValidationReport,
    build_plan,
    validate_fact_store,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fact(*, id, type_, claim, evidence, entity_id="", entity_display="",
          source="github_readme:zeyad/repo",
          reliability=SourceReliability.USER_ORIGINAL,
          value=None, unit=None, hedged=False):
    return FactRecord(
        id=id, type=type_, claim=claim, evidence_quote=evidence,
        entity_id=entity_id, entity_display=entity_display,
        source=source, source_reliability=reliability,
        value=value, unit=unit, hedged=hedged,
    )


# ---------------------------------------------------------------------------
# validate_fact_store
# ---------------------------------------------------------------------------


class ValidateFactStoreTests(SimpleTestCase):
    """Pre-pass guard. Anomalous facts are dropped (with a logged
    reason); the rest go through to the planner."""

    def test_clean_store_no_anomalies(self):
        store = FactStore()
        store.add(_fact(id="p1", type_=FactType.PROJECT, claim="SmartCV",
                        evidence="SmartCV is a thing",
                        entity_id="https://github.com/z/smartcv"))
        store.add(_fact(id="m1", type_=FactType.METRIC, claim="0.89 ROC-AUC",
                        evidence="0.89 ROC-AUC", value=0.89, unit="ROC-AUC",
                        entity_id="https://github.com/z/smartcv"))
        report = validate_fact_store(store)
        self.assertEqual(report.anomalies, [])
        self.assertEqual(set(report.valid_fact_ids), {"p1", "m1"})

    def test_orphan_metric_is_flagged_and_dropped(self):
        """Metric bound to an entity_id that has NO project/role/
        education/credential anchor → flagged + dropped from valid."""
        store = FactStore()
        store.add(_fact(id="orphan", type_=FactType.METRIC,
                        claim="99% accuracy",
                        evidence="99% accuracy",
                        value=99.0, unit="%",
                        entity_id="https://github.com/z/nonexistent"))
        report = validate_fact_store(store)
        self.assertNotIn("orphan", report.valid_fact_ids)
        self.assertEqual(len(report.anomalies), 1)
        self.assertEqual(report.anomalies[0].fact_id, "orphan")
        self.assertIn("orphan metric", report.anomalies[0].reason)

    def test_metric_with_anchor_is_kept(self):
        store = FactStore()
        store.add(_fact(id="proj", type_=FactType.PROJECT, claim="SmartCV",
                        evidence="SmartCV thing",
                        entity_id="https://github.com/z/smartcv"))
        store.add(_fact(id="met", type_=FactType.METRIC,
                        claim="0.89 ROC-AUC", evidence="0.89 ROC-AUC",
                        value=0.89, unit="ROC-AUC",
                        entity_id="https://github.com/z/smartcv"))
        report = validate_fact_store(store)
        self.assertEqual(set(report.valid_fact_ids), {"proj", "met"})

    def test_anchor_must_be_role_project_education_or_credential(self):
        """An ACHIEVEMENT at the entity is not an anchor — metrics on
        such an entity are still orphans."""
        store = FactStore()
        store.add(_fact(id="ach", type_=FactType.ACHIEVEMENT,
                        claim="Did things",
                        evidence="Did things",
                        entity_id="ent_X"))
        store.add(_fact(id="met", type_=FactType.METRIC,
                        claim="50%", evidence="50%",
                        value=50.0, unit="%", entity_id="ent_X"))
        report = validate_fact_store(store)
        self.assertIn("ach", report.valid_fact_ids)   # achievement is fine
        self.assertNotIn("met", report.valid_fact_ids)
        self.assertEqual(len(report.anomalies), 1)


# ---------------------------------------------------------------------------
# build_plan — empty / all-anomalous → raises
# ---------------------------------------------------------------------------


class BuildPlanEmptyStoreTests(SimpleTestCase):

    def test_empty_store_raises(self):
        with self.assertRaises(ValueError) as cm:
            build_plan(FactStore(), job_must_have_skills=["Python"])
        self.assertIn("empty", str(cm.exception))

    def test_all_anomalous_store_raises(self):
        """A store with only orphan-metric facts has nothing valid to
        plan → raise."""
        store = FactStore()
        store.add(_fact(id="o", type_=FactType.METRIC,
                        claim="99%", evidence="99%",
                        value=99.0, unit="%",
                        entity_id="https://github.com/z/ghost"))
        with self.assertRaises(ValueError) as cm:
            build_plan(store, job_must_have_skills=["Python"])
        self.assertIn("anomalous", str(cm.exception))


# ---------------------------------------------------------------------------
# Anti-over-representation cap
# ---------------------------------------------------------------------------


class AntiOverRepresentationTests(SimpleTestCase):
    """The keyword-stuffing killer: a skill mentioned in many facts
    must not appear in every section. The cap holds in CODE."""

    def _python_heavy_store(self):
        """Build a store where 'Python' appears as a skill AND in 9
        achievement facts across 3 projects — a worst-case sprayer.
        With the default cap=3, Python should appear at most 3 times
        in the final plan (once in skills + at most 2 elsewhere)."""
        store = FactStore()
        # Three projects, each with their own anchor.
        for i, name in enumerate(["projA", "projB", "projC"], start=1):
            eid = f"https://github.com/z/{name}"
            store.add(_fact(
                id=f"proj_{i}", type_=FactType.PROJECT,
                claim=f"Project {name}",
                evidence=f"Project {name} built in Python.",
                entity_id=eid, entity_display=name,
            ))
            # Three achievements per project, all mentioning Python.
            for j in range(3):
                store.add(_fact(
                    id=f"ach_{i}_{j}", type_=FactType.ACHIEVEMENT,
                    claim=f"Did thing {j} in Python",
                    evidence=f"Built thing {j} using Python and SQL.",
                    entity_id=eid,
                ))
        # Plus the Python skill fact itself.
        store.add(_fact(
            id="sk_py", type_=FactType.SKILL, claim="Python",
            evidence="Python is a skill",
        ))
        return store

    def _count_python_mentions(self, plan: PlanResult, store: FactStore) -> int:
        """How many allocated facts mention 'python' anywhere in their
        claim or evidence."""
        seen_ids = set()
        for section in plan.sections.values():
            for fa in section.facts:
                seen_ids.add(fa.fact_id)
            for ent in section.entities:
                for fa in ent.facts:
                    seen_ids.add(fa.fact_id)
                # metric_fact_ids count too
                for mid in ent.metric_fact_ids:
                    seen_ids.add(mid)
        count = 0
        for fid in seen_ids:
            f = store.get(fid)
            if not f:
                continue
            text = (f.claim or "") + " " + (f.evidence_quote or "")
            if "python" in text.lower():
                count += 1
        return count

    def test_cap_holds_across_sections(self):
        store = self._python_heavy_store()
        plan = build_plan(
            store,
            job_must_have_skills=["Python"],
        )
        python_count = self._count_python_mentions(plan, store)
        self.assertLessEqual(
            python_count, DEFAULT_PER_SKILL_MENTION_CAP,
            f"Python mentioned {python_count} times in plan; "
            f"cap is {DEFAULT_PER_SKILL_MENTION_CAP}",
        )
        # The counter should ALSO reflect this.
        self.assertLessEqual(
            plan.anti_overrep_stats.get("python", 0),
            DEFAULT_PER_SKILL_MENTION_CAP,
        )

    def test_anti_overrep_stats_populated(self):
        store = self._python_heavy_store()
        plan = build_plan(store, job_must_have_skills=["Python"])
        self.assertIn("python", plan.anti_overrep_stats)
        self.assertGreater(plan.anti_overrep_stats["python"], 0)

    def test_skipped_facts_are_noted(self):
        """When a fact is refused for cap reasons, the plan's `notes`
        field gets an entry — diagnostic trail for explainability."""
        store = self._python_heavy_store()
        plan = build_plan(store, job_must_have_skills=["Python"])
        self.assertTrue(
            any("mention cap" in n for n in plan.notes),
            f"expected cap-skip notes; got {plan.notes!r}",
        )


# ---------------------------------------------------------------------------
# Cross-attachment safety — metrics only at their bound entity
# ---------------------------------------------------------------------------


class MetricCrossAttachmentSafetyTests(SimpleTestCase):

    def test_metrics_only_appear_under_their_bound_entity(self):
        store = FactStore()
        # Two projects, each with its own metric.
        store.add(_fact(id="pA", type_=FactType.PROJECT,
                        claim="Project A",
                        evidence="Project A",
                        entity_id="ent_A", entity_display="A"))
        store.add(_fact(id="mA", type_=FactType.METRIC,
                        claim="0.89 ROC-AUC",
                        evidence="0.89 ROC-AUC",
                        value=0.89, unit="ROC-AUC",
                        entity_id="ent_A"))
        store.add(_fact(id="pB", type_=FactType.PROJECT,
                        claim="Project B",
                        evidence="Project B",
                        entity_id="ent_B", entity_display="B"))
        store.add(_fact(id="mB", type_=FactType.METRIC,
                        claim="0.351 silhouette",
                        evidence="0.351 silhouette",
                        value=0.351, unit="silhouette",
                        entity_id="ent_B"))
        plan = build_plan(store, job_must_have_skills=["Python"])
        # Walk every project entity in the plan; the metric_fact_ids
        # at each entity must belong ONLY to that entity.
        for ent in plan.sections["projects"].entities:
            for mid in ent.metric_fact_ids:
                metric = store.get(mid)
                self.assertEqual(
                    metric.entity_id, ent.entity_id,
                    f"metric {mid!r} surfaced under wrong entity "
                    f"{ent.entity_id!r} (its real entity is {metric.entity_id!r})",
                )


# ---------------------------------------------------------------------------
# Ranking — reliability wins for same slot
# ---------------------------------------------------------------------------


class RankingReliabilityTests(SimpleTestCase):

    def test_platform_verified_outranks_tutorial_for_same_slot(self):
        """Reliability is the differentiator when JD relevance is
        equal. Both fixtures below have ZERO mentions of the must-have
        skill, so the scorer ties on relevance → reliability rank
        decides (PLATFORM_VERIFIED=4 vs TUTORIAL_DERIVED=2)."""
        store = FactStore()
        # Two credentials competing for the same slot — neither
        # mentions the JD's Python, so reliability is the sole signal.
        store.add(_fact(
            id="cred_kaggle", type_=FactType.CREDENTIAL,
            claim="Kaggle Silver Medal on Titanic",
            evidence="Silver Medal on Titanic competition",
            reliability=SourceReliability.PLATFORM_VERIFIED,
            entity_id="kaggle:competition|titanic",
        ))
        store.add(_fact(
            id="cred_course", type_=FactType.CREDENTIAL,
            claim="Completed Udemy Java Bootcamp",
            evidence="Completed Udemy Java Bootcamp course",
            reliability=SourceReliability.TUTORIAL_DERIVED,
            entity_id="cv:cred|udemy_java",
        ))
        plan = build_plan(store, job_must_have_skills=["Python"])
        cert_ids = [fa.fact_id for fa in plan.sections["certifications"].facts]
        self.assertIn("cred_kaggle", cert_ids)
        self.assertIn("cred_course", cert_ids)
        self.assertLess(
            cert_ids.index("cred_kaggle"), cert_ids.index("cred_course"),
            "PLATFORM_VERIFIED credential should rank above TUTORIAL_DERIVED "
            "when JD relevance is equal",
        )

    def test_jd_relevance_lifts_matching_skill(self):
        store = FactStore()
        store.add(_fact(
            id="sk_py", type_=FactType.SKILL, claim="Python",
            evidence="Python is a skill",
        ))
        store.add(_fact(
            id="sk_cobol", type_=FactType.SKILL, claim="COBOL",
            evidence="COBOL is a skill",
        ))
        plan = build_plan(store, job_must_have_skills=["Python"])
        skill_ids = [fa.fact_id for fa in plan.sections["skills"].facts]
        # Python ranks first (JD match bonus).
        self.assertEqual(skill_ids[0], "sk_py")


# ---------------------------------------------------------------------------
# Hedge flag propagation
# ---------------------------------------------------------------------------


class HedgeFlagPropagationTests(SimpleTestCase):

    def test_hedged_fact_carries_flag_into_plan(self):
        store = FactStore()
        store.add(_fact(
            id="p1", type_=FactType.PROJECT,
            claim="ResumeParser",
            evidence="A resume parser project",
            entity_id="ent_RP", entity_display="ResumeParser",
        ))
        store.add(_fact(
            id="m_hedged", type_=FactType.METRIC,
            claim="~92% accuracy", value=92.0, unit="%",
            evidence="achieves about 92% extraction accuracy",
            entity_id="ent_RP", hedged=True,
        ))
        plan = build_plan(store, job_must_have_skills=["accuracy"])
        # The metric_fact_id stays under its entity; the entity's
        # hedged_any flag MUST flip true because of this hedged metric.
        proj_entity = next(
            e for e in plan.sections["projects"].entities
            if e.entity_id == "ent_RP"
        )
        self.assertIn("m_hedged", proj_entity.metric_fact_ids)
        self.assertTrue(
            proj_entity.hedged_any,
            "entity with a hedged metric should be flagged hedged_any=True",
        )


# ---------------------------------------------------------------------------
# Defensive: orphan metric flagged + dropped, plan continues
# ---------------------------------------------------------------------------


class DefensiveOrphanMetricTests(SimpleTestCase):

    def test_orphan_metric_dropped_but_plan_continues(self):
        store = FactStore()
        # Healthy project + metric.
        store.add(_fact(id="proj", type_=FactType.PROJECT,
                        claim="SmartCV", evidence="SmartCV",
                        entity_id="ent_real", entity_display="SmartCV"))
        store.add(_fact(id="met_good", type_=FactType.METRIC,
                        claim="0.89 ROC-AUC",
                        evidence="0.89 ROC-AUC",
                        value=0.89, unit="ROC-AUC",
                        entity_id="ent_real"))
        # Orphan metric — entity_id has no anchor.
        store.add(_fact(id="met_orphan", type_=FactType.METRIC,
                        claim="99% accuracy",
                        evidence="99% accuracy",
                        value=99.0, unit="%",
                        entity_id="ent_ghost"))
        plan = build_plan(store, job_must_have_skills=["Python"])
        # Validation report flags the orphan.
        self.assertEqual(len(plan.validation.anomalies), 1)
        self.assertEqual(plan.validation.anomalies[0].fact_id, "met_orphan")
        # The orphan does NOT appear in any allocation.
        all_allocated_ids = set()
        for sect in plan.sections.values():
            for fa in sect.facts:
                all_allocated_ids.add(fa.fact_id)
            for ent in sect.entities:
                for fa in ent.facts:
                    all_allocated_ids.add(fa.fact_id)
                all_allocated_ids.update(ent.metric_fact_ids)
        self.assertNotIn("met_orphan", all_allocated_ids,
                         "orphan metric must not reach the plan")
        # The healthy metric did make it through.
        self.assertIn("met_good", all_allocated_ids)


# ---------------------------------------------------------------------------
# Experience entities ordered reverse-chronologically
# ---------------------------------------------------------------------------


class ExperienceReverseChronologicalTests(SimpleTestCase):

    def test_roles_ordered_by_parsed_end_date(self):
        store = FactStore()
        # Three roles with dates in the evidence_quote.
        store.add(_fact(
            id="r_old", type_=FactType.ROLE,
            claim="IT Intern at Almansour",
            evidence="IT Intern, Almansour Automotive — Jul 2023 - Jul 2024",
            entity_id="cv:role|almansour|it intern",
            entity_display="IT Intern @ Almansour Automotive",
        ))
        store.add(_fact(
            id="r_mid", type_=FactType.ROLE,
            claim="DevOps Trainee at NTI",
            evidence="DevOps Trainee, NTI — Aug 2025 - Sep 2025",
            entity_id="cv:role|nti|devops trainee",
            entity_display="DevOps Trainee @ NTI",
        ))
        store.add(_fact(
            id="r_new", type_=FactType.ROLE,
            claim="AI Trainee at DEPI",
            evidence="AI Trainee, DEPI — Jun 2025 - Dec 2025",
            entity_id="cv:role|depi|ai trainee",
            entity_display="AI Trainee @ DEPI",
        ))
        plan = build_plan(
            store, job_must_have_skills=["Python"],
            today_ym=(2026, 6),   # frozen now for determinism
        )
        order = [e.entity_id for e in plan.sections["experience"].entities]
        self.assertEqual(
            order,
            ["cv:role|depi|ai trainee",          # Dec 2025
             "cv:role|nti|devops trainee",       # Sep 2025
             "cv:role|almansour|it intern"],     # Jul 2024
            f"experience entities not in reverse-chrono order: {order!r}",
        )

    def test_present_role_sorts_to_top(self):
        store = FactStore()
        store.add(_fact(
            id="r_old", type_=FactType.ROLE,
            claim="Old role",
            evidence="Old role — Jan 2024 - Dec 2024",
            entity_id="ent_old", entity_display="Old",
        ))
        store.add(_fact(
            id="r_present", type_=FactType.ROLE,
            claim="Current role",
            evidence="Current role — Mar 2025 - Present",
            entity_id="ent_pres", entity_display="Current",
        ))
        plan = build_plan(
            store, job_must_have_skills=["Python"],
            today_ym=(2026, 6),
        )
        order = [e.entity_id for e in plan.sections["experience"].entities]
        self.assertEqual(order[0], "ent_pres",
                         "Present/ongoing role should be first")


# ---------------------------------------------------------------------------
# End-to-end shape / sanity
# ---------------------------------------------------------------------------


class PlanShapeIntegrationTests(SimpleTestCase):

    def test_every_allocated_fact_id_exists_in_store(self):
        """Defensive integration: every fact_id in the plan must
        resolve to a fact in the store."""
        store = FactStore()
        store.add(_fact(id="p1", type_=FactType.PROJECT, claim="X",
                        evidence="X built", entity_id="ent1",
                        entity_display="X"))
        store.add(_fact(id="ach1", type_=FactType.ACHIEVEMENT,
                        claim="Shipped X",
                        evidence="Shipped X in production",
                        entity_id="ent1"))
        store.add(_fact(id="sk1", type_=FactType.SKILL, claim="Python",
                        evidence="Python skill"))
        store.add(_fact(id="r1", type_=FactType.ROLE,
                        claim="Eng at Acme",
                        evidence="Eng, Acme — Jun 2025 - Dec 2025",
                        entity_id="role:acme:eng",
                        entity_display="Eng @ Acme"))
        store.add(_fact(id="ach_r", type_=FactType.ACHIEVEMENT,
                        claim="Built Acme dashboard",
                        evidence="Built Acme dashboard for SAP team",
                        entity_id="role:acme:eng"))
        store.add(_fact(id="edu1", type_=FactType.EDUCATION,
                        claim="BSc CS, KSIU",
                        evidence="BSc Computer Science, KSIU, 2027"))
        store.add(_fact(id="cred1", type_=FactType.CREDENTIAL,
                        claim="AI Specialization",
                        evidence="Completed AI Specialization",
                        entity_id="cred:ai_spec",
                        reliability=SourceReliability.PLATFORM_VERIFIED))
        plan = build_plan(
            store,
            job_must_have_skills=["Python"],
            job_nice_to_have_skills=["SAP"],
        )
        # Every allocated id resolves.
        all_ids = set()
        for sect in plan.sections.values():
            for fa in sect.facts:
                all_ids.add(fa.fact_id)
            for ent in sect.entities:
                if ent.anchor_fact_id:
                    all_ids.add(ent.anchor_fact_id)
                for fa in ent.facts:
                    all_ids.add(fa.fact_id)
                all_ids.update(ent.metric_fact_ids)
        for fid in all_ids:
            self.assertIsNotNone(
                store.get(fid),
                f"plan referenced fact_id {fid!r} that doesn't exist in the store",
            )

    def test_returns_plan_result_with_expected_section_keys(self):
        store = FactStore()
        store.add(_fact(id="sk", type_=FactType.SKILL, claim="Python",
                        evidence="Python"))
        plan = build_plan(store, job_must_have_skills=["Python"])
        self.assertEqual(
            set(plan.sections.keys()),
            {"summary", "skills", "experience", "projects",
             "education", "certifications"},
        )
        self.assertEqual(plan.ranking_method, "lexical_jd_overlap_v1")

    def test_ranking_method_is_documented_in_output(self):
        store = FactStore()
        store.add(_fact(id="sk", type_=FactType.SKILL, claim="Python",
                        evidence="Python"))
        plan = build_plan(store, job_must_have_skills=["Python"])
        # Lexical scorer is what v1 of the planner uses; the field
        # documents this so a consumer can tell.
        self.assertIn("lexical", plan.ranking_method)
