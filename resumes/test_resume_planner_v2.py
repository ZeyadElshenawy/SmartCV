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
# Fix B — _jd_relevance_score variant-aware skill bonus
# ---------------------------------------------------------------------------


class JdRelevanceVariantMatchTests(SimpleTestCase):
    """A candidate skill under a variant name must still earn its JD-relevance
    bonus (so it survives the skills cap), via the shared skills_match. Pre-fix
    the exact word-boundary test scored these 0."""

    def _rel(self, claim, *, must=(), nice=()):
        from resumes.services.resume_planner_v2 import _jd_relevance_score
        return _jd_relevance_score(
            _fact(id="s", type_=FactType.SKILL, claim=claim, evidence=claim),
            must_have_terms=list(must), nice_to_have_terms=list(nice),
        )

    def test_variant_skill_earns_must_have_bonus(self):
        # JD token "REST API integration"; candidate skill "REST APIs".
        # Exact word-boundary scored 0 -> cut by the skills cap. Now grounded.
        self.assertGreaterEqual(
            self._rel("REST APIs", must=["REST API integration"]), 5.0)

    def test_restful_apis_variant_also_scores(self):
        self.assertGreaterEqual(
            self._rel("RESTful APIs", must=["REST API integration"]), 5.0)

    def test_nice_tier_variant_scores_two(self):
        self.assertGreaterEqual(
            self._rel("REST APIs", nice=["REST API integration"]), 2.0)

    def test_phantom_skill_scores_zero(self):
        # "GoRouter" is not the JD's "REST API integration" — no bonus, no base.
        self.assertEqual(
            self._rel("GoRouter", must=["REST API integration"]), 0.0)

    def test_exact_skill_still_scores(self):
        self.assertGreaterEqual(self._rel("Flutter", must=["Flutter"]), 5.0)


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
        """The cap protects NARRATIVE sections (summary / experience /
        projects) from keyword-spraying — that's its whole purpose. The
        structured SKILLS enumeration is the legitimate place for a
        keyword to appear once each and is exempt from the counter
        (fix (a)). So the total plan-wide count of a tracked keyword is
        bounded by ``cap + <skill-section facts naming the keyword>``,
        not by ``cap`` alone. For this fixture there's 1 Python skill
        fact, so the bound is cap+1=4. The internal counter (which
        excludes the skill contribution) still respects the cap."""
        store = self._python_heavy_store()
        plan = build_plan(
            store,
            job_must_have_skills=["Python"],
        )
        python_count = self._count_python_mentions(plan, store)
        # cap + 1 allowance for the single structured skill row that
        # legitimately enumerates "Python". The cap still binds the
        # narrative sections.
        self.assertLessEqual(
            python_count, DEFAULT_PER_SKILL_MENTION_CAP + 1,
            f"Python mentioned {python_count} times in plan; "
            f"bound is cap+1={DEFAULT_PER_SKILL_MENTION_CAP + 1} "
            f"(cap on narrative + 1 for the skills enumeration)",
        )
        # The counter — which excludes the skills section — should still
        # be capped at the narrative limit.
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

    # ------------------------------------------------------------------
    # Fix (a) — skills section does NOT increment the mention counter
    # ------------------------------------------------------------------

    def test_skills_section_does_not_increment_counter(self):
        """Fix (a): the structured skills enumeration is exempt from
        the mention counter. Allocating N skill rows that name a tracked
        keyword leaves the counter at 0 for that keyword (until a
        NARRATIVE section bumps it). Without this, the skills section
        exhausts the cap before experience even runs (the DEPI scenario).
        """
        store = FactStore()
        # Multiple skill rows each naming the tracked keyword.
        for i, claim in enumerate([
            "Python", "Python (Data Science)", "Pythonic patterns",
            "Python tooling (pip, poetry)",
        ]):
            store.add(_fact(
                id=f"sk_{i}", type_=FactType.SKILL,
                claim=claim, evidence=claim,
            ))
        plan = build_plan(store, job_must_have_skills=["Python"])
        # All skills allocated.
        self.assertGreaterEqual(
            len(plan.sections["skills"].facts), 4,
            "expected all 4 skill rows to allocate",
        )
        # And yet the counter sees zero — they didn't fund the cap.
        self.assertEqual(
            plan.anti_overrep_stats.get("python", 0), 0,
            "skills must not increment the mention counter",
        )

    # ------------------------------------------------------------------
    # Fix (c) — per-entity floor in EXPERIENCE
    # ------------------------------------------------------------------

    def _cap_exhausted_python_store_with_one_role(
        self, *, role_achievement_claim: str,
    ) -> "FactStore":
        """Build a store that drives the SUMMARY section to cap-exhaust
        Python (3 projects each with a Python achievement become marquee
        summary candidates) and then adds ONE experience role whose
        single achievement also mentions Python. Generic — caller
        supplies the role achievement's claim to control whether the
        strength gate vetoes it."""
        store = FactStore()
        # Three projects → seed the marquee pool with Python-mentioning
        # achievements until summary's cap is spent.
        for i, name in enumerate(["projX", "projY", "projZ"], start=1):
            eid = f"https://github.com/z/{name}"
            store.add(_fact(
                id=f"proj_{i}", type_=FactType.PROJECT,
                claim=f"Project {name}",
                evidence=f"{name} built with Python",
                entity_id=eid, entity_display=name,
            ))
            store.add(_fact(
                id=f"ach_{i}", type_=FactType.ACHIEVEMENT,
                claim=f"Shipped {name} with Python and SQL",
                evidence=f"Built {name} using Python over six months",
                entity_id=eid,
            ))
        # The experience role — one role, one achievement.
        role_eid = "cv:role|acme corp|software engineer"
        store.add(_fact(
            id="role_anchor", type_=FactType.ROLE,
            claim="Software Engineer at Acme Corp",
            evidence="Software Engineer at Acme Corp — Jan 2022 to Dec 2024",
            entity_id=role_eid,
            entity_display="Software Engineer @ Acme Corp",
        ))
        store.add(_fact(
            id="role_ach", type_=FactType.ACHIEVEMENT,
            claim=role_achievement_claim,
            evidence=role_achievement_claim,
            entity_id=role_eid,
        ))
        return store, role_eid

    def test_experience_entity_gets_floor_fact_when_cap_exhausted(self):
        """Fix (c): an experience entity whose sole strong-enough fact
        mentions a cap-exhausted skill must still get that one fact
        allocated. Without the floor the entity renders empty (DEPI
        scenario, generalised — no profile-specific data)."""
        store, role_eid = self._cap_exhausted_python_store_with_one_role(
            # Long, substantive achievement — passes the strength gate.
            role_achievement_claim=(
                "Built a Python data pipeline that reduced nightly load "
                "by six hours and replaced three legacy ETL jobs."
            ),
        )
        plan = build_plan(store, job_must_have_skills=["Python"])

        # Pre-condition: the counter is at the cap (so the role's fact
        # WOULD be skipped without the floor).
        self.assertEqual(
            plan.anti_overrep_stats.get("python", 0),
            DEFAULT_PER_SKILL_MENTION_CAP + 1,
            "expected the role's floor fact to push the counter exactly "
            "one mention past the cap (cap reached by summary + 1 floor)",
        )

        exp_sec = plan.sections.get("experience")
        self.assertIsNotNone(exp_sec)
        matches = [e for e in (exp_sec.entities or []) if e.entity_id == role_eid]
        self.assertEqual(len(matches), 1, "experience entity should exist")
        ent = matches[0]
        # The floor admitted one fact.
        self.assertEqual(
            len(ent.facts), 1,
            f"floor must admit exactly 1 fact; got {len(ent.facts)}",
        )
        self.assertEqual(ent.facts[0].fact_id, "role_ach")
        # And a note recorded WHY.
        floor_notes = [n for n in plan.notes
                       if "floor" in n and role_eid in n]
        self.assertEqual(
            len(floor_notes), 1,
            f"expected one floor note; got {floor_notes!r}",
        )

    def test_floor_does_not_bypass_strength_gate(self):
        """Floor admits ONE fact past the mention cap — but NOT past
        the strength gate. A URL-only claim is structurally weak and
        must NOT become a floor bullet just because the role would
        otherwise render empty. The role renders with zero bullets;
        the strength gate's protection holds."""
        store, role_eid = self._cap_exhausted_python_store_with_one_role(
            # Bare URL — strength gate's URL rule drops it.
            role_achievement_claim="https://github.com/acme/python-stuff",
        )
        plan = build_plan(store, job_must_have_skills=["Python"])

        exp_sec = plan.sections.get("experience")
        matches = [e for e in (exp_sec.entities or []) if e.entity_id == role_eid]
        self.assertEqual(len(matches), 1)
        ent = matches[0]
        # Floor would have admitted past the cap — but the strength gate
        # vetoes the URL. End result: empty entity, by design.
        self.assertEqual(
            len(ent.facts), 0,
            "URL-only fact must NOT pass the floor; strength gate wins",
        )
        # The plan notes should show BOTH events: floor admitted the
        # fact past the cap, AND the strength gate then dropped it.
        notes_for_role = [n for n in plan.notes if role_eid in n]
        self.assertTrue(
            any("floor" in n for n in notes_for_role),
            f"expected a floor admission note; got {notes_for_role!r}",
        )
        self.assertTrue(
            any("dropped weak fact" in n and "URL-only" in n
                for n in notes_for_role),
            f"expected the strength gate's URL-only drop note; "
            f"got {notes_for_role!r}",
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


# ===========================================================================
# Same-entity cross-section conflict resolver. General feature for all
# users — no profile / entity-name hardcoding.
# ===========================================================================


class SameEntityCrossSectionResolverTests(SimpleTestCase):
    """The resolver finds same-entity duplicates across anchor types
    (ROLE / CREDENTIAL / EDUCATION), merges them into ONE entity, and
    chooses its section by EXPERIENCE SUBSTANCE rather than the source
    label."""

    def _store_with(self, *facts) -> FactStore:
        s = FactStore()
        for f in facts:
            s.add(f)
        return s

    def test_substantive_program_lands_in_experience_not_certs(self):
        """A training program that arrives as both a ROLE (LinkedIn)
        AND a CREDENTIAL (CV course list) with strong name match +
        duration + deliverable → ONE entity in EXPERIENCE, NOT in
        certifications. Rationale names the substance signals."""
        role = _fact(
            id="r1", type_=FactType.ROLE,
            claim="AI & Data Science Trainee at AcmeTrack",
            evidence="AI & Data Science Trainee @ AcmeTrack — Jun 2025 - Dec 2025",
            entity_id="cv:role|acmetrack|ai & data science trainee",
            entity_display="AI & Data Science Trainee @ AcmeTrack",
            source="structured_profile",
        )
        role_ach = _fact(
            id="r1-a1", type_=FactType.ACHIEVEMENT,
            claim="Built and deployed a capstone pipeline end-to-end.",
            evidence="Built and deployed a capstone pipeline end-to-end.",
            entity_id=role.entity_id,
            entity_display=role.entity_display,
            source="structured_profile",
        )
        cred = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="AcmeTrack AI & Data Science Program — AcmeTrack",
            evidence="AcmeTrack AI & Data Science Program — AcmeTrack (2025)",
            entity_id="cv:cred|acmetrack ai & data science program|acmetrack",
            entity_display="AcmeTrack AI & Data Science Program",
            source="structured_profile",
        )
        store = self._store_with(role, role_ach, cred)
        plan = build_plan(store)
        # ---- EXPERIENCE: merged entity present ----
        exp = plan.sections["experience"]
        self.assertEqual(len(exp.entities), 1)
        exp_entity = exp.entities[0]
        self.assertEqual(exp_entity.entity_id, role.entity_id)
        self.assertIn(
            role_ach.id, {a.fact_id for a in exp_entity.facts},
        )
        # Resolver rationale surfaces on the entity allocation.
        self.assertIn("resolver", exp_entity.rationale)
        self.assertIn("duration", exp_entity.rationale)
        self.assertIn("deliverables", exp_entity.rationale)
        # ---- CERTIFICATIONS: CREDENTIAL suppressed ----
        cert_fact_ids = {
            a.fact_id for a in plan.sections["certifications"].facts
        }
        self.assertNotIn(
            cred.id, cert_fact_ids,
            "merged CREDENTIAL must NOT also appear in certifications "
            "— this is the load-bearing 'one entity, one section' rule",
        )

    def test_thin_certificate_stays_in_certifications(self):
        """A bare CREDENTIAL with no duration / deliverable /
        applied verbs / real-org affiliation → 0 signals → certs.
        Conservative default."""
        cred = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="Python Fundamentals — Udemy",
            evidence="Python Fundamentals — Udemy",
            entity_id="cv:cred|python fundamentals|udemy",
            entity_display="Python Fundamentals",
            source="structured_profile",
        )
        store = self._store_with(cred)
        plan = build_plan(store)
        cert_ids = {a.fact_id for a in plan.sections["certifications"].facts}
        self.assertIn(cred.id, cert_ids)
        self.assertEqual(plan.sections["experience"].entities, [])

    def test_truly_ambiguous_middle_defaults_to_certifications(self):
        """ONE signal fires (cert-platform org, no duration, no
        deliverable, one borderline applied verb) → conservative
        default to certifications. Don't promote a borderline cert
        to a job."""
        role = _fact(
            id="r1", type_=FactType.ROLE,
            claim="Learner",
            evidence="Learner @ Coursera",
            entity_id="cv:role|coursera|learner course",
            entity_display="Learner Course @ Coursera",
            source="structured_profile",
        )
        cred = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="Learner Course — Coursera",
            evidence="Designed exercises completed.",
            entity_id="cv:cred|learner course|coursera",
            entity_display="Learner Course",
            source="structured_profile",
        )
        store = self._store_with(role, cred)
        plan = build_plan(store)
        # Not promoted to experience — at most one anchor survives,
        # and it does so in certifications.
        self.assertEqual(plan.sections["experience"].entities, [])

    def test_substantial_program_called_certificate_lands_in_experience(self):
        """ANTI-TIMIDITY: an entity with real substance (duration +
        real-org affiliation + capstone deliverable + applied verbs)
        must land in EXPERIENCE even when one source labelled it a
        'certificate'. Burying a substantial multi-month program in
        the cert line is a real failure the resolver protects
        against."""
        cred = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="National AI Initiative — Ministry of Innovation",
            evidence=(
                "National AI Initiative — Ministry of Innovation "
                "(Jun 2024 - Dec 2024). Built and deployed a capstone "
                "pipeline; engineered the data flow end-to-end."
            ),
            entity_id="cv:cred|national ai initiative|ministry of innovation",
            entity_display="National AI Initiative",
            source="structured_profile",
        )
        role = _fact(
            id="r1", type_=FactType.ROLE,
            claim="Trainee at Ministry of Innovation",
            evidence=(
                "Trainee @ Ministry of Innovation — Jun 2024 - Dec 2024"
            ),
            entity_id="cv:role|ministry of innovation|trainee",
            entity_display="Trainee @ Ministry of Innovation",
            source="structured_profile",
        )
        store = self._store_with(role, cred)
        plan = build_plan(store)
        exp_entities = plan.sections["experience"].entities
        self.assertEqual(
            len(exp_entities), 1,
            "substantial program must land in experience, "
            "even if one source labelled it a certificate",
        )
        cert_ids = {a.fact_id for a in plan.sections["certifications"].facts}
        self.assertNotIn(
            cred.id, cert_ids,
            "anti-timidity: don't bury substance in the cert line",
        )

    def test_weak_name_overlap_does_not_merge(self):
        """Two cross-type entities with WEAK name overlap (e.g.
        different programs from the same cert platform) must STAY
        SEPARATE. Conservative match avoids collapsing unrelated
        artifacts even when they share the same issuer."""
        c_python = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="Python Programming — DataCamp",
            evidence="Python Programming — DataCamp",
            entity_id="cv:cred|python programming|datacamp",
            entity_display="Python Programming",
            source="structured_profile",
        )
        c_sql = _fact(
            id="c2", type_=FactType.CREDENTIAL,
            claim="SQL Analysis — DataCamp",
            evidence="SQL Analysis — DataCamp",
            entity_id="cv:cred|sql analysis|datacamp",
            entity_display="SQL Analysis",
            source="structured_profile",
        )
        store = self._store_with(c_python, c_sql)
        plan = build_plan(store)
        cert_ids = {a.fact_id for a in plan.sections["certifications"].facts}
        # Both survive — no wrong collapse. (Same-type pair is never
        # subject to resolver merging anyway — resolver only acts on
        # cross-type clusters — but this asserts the end-state.)
        self.assertIn(c_python.id, cert_ids)
        self.assertIn(c_sql.id, cert_ids)
        self.assertEqual(plan.sections["experience"].entities, [])

    def test_entity_appears_in_exactly_one_section(self):
        """End-to-end: after resolution, no merged anchor fact
        appears in two sections (the load-bearing 'one entity, one
        section' invariant)."""
        role = _fact(
            id="r1", type_=FactType.ROLE,
            claim="AI Engineer at OrgCo",
            evidence="AI Engineer @ OrgCo — Jan 2024 - Dec 2024",
            entity_id="cv:role|orgco|ai engineer",
            entity_display="AI Engineer @ OrgCo",
            source="structured_profile",
        )
        role_ach = _fact(
            id="r1-a1", type_=FactType.ACHIEVEMENT,
            claim="Built and shipped the production ML pipeline.",
            evidence="Built and shipped the production ML pipeline.",
            entity_id=role.entity_id,
            entity_display=role.entity_display,
            source="structured_profile",
        )
        cred = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="AI Engineer Programme — OrgCo",
            evidence="AI Engineer Programme — OrgCo (Jan 2024 - Dec 2024)",
            entity_id="cv:cred|ai engineer programme|orgco",
            entity_display="AI Engineer Programme",
            source="structured_profile",
        )
        store = self._store_with(role, role_ach, cred)
        plan = build_plan(store)
        seen_by_section: dict[str, set[str]] = {}
        for name, sec in plan.sections.items():
            seen_by_section[name] = (
                {a.fact_id for a in sec.facts}
                | {e.anchor_fact_id for e in sec.entities}
            )
        for fact_id in (role.id, cred.id):
            in_sections = [
                n for n, ids in seen_by_section.items() if fact_id in ids
            ]
            self.assertLessEqual(
                len(in_sections), 1,
                f"fact {fact_id!r} appeared in {in_sections!r} — "
                f"must be in at most one section",
            )

    def test_no_cross_type_conflict_is_unaffected(self):
        """Regression: a plan with no cross-type duplicates produces
        the same allocations as before — the resolver is inert when
        no cluster has multiple types."""
        r = _fact(
            id="r1", type_=FactType.ROLE,
            claim="Engineer at OrgX",
            evidence="Engineer @ OrgX — 2023 - 2024",
            entity_id="cv:role|orgx|engineer",
            entity_display="Engineer @ OrgX",
            source="structured_profile",
        )
        c = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="AWS Solutions Architect — Amazon",
            evidence="AWS Solutions Architect — Amazon (2023)",
            entity_id="cv:cred|aws solutions architect|amazon",
            entity_display="AWS Solutions Architect",
            source="structured_profile",
        )
        e = _fact(
            id="e1", type_=FactType.EDUCATION,
            claim="BSc CS at Tech University",
            evidence="BSc CS @ Tech University (2022)",
            entity_id="cv:edu|tech university|bsc cs",
            entity_display="BSc CS @ Tech University",
            source="structured_profile",
        )
        store = self._store_with(r, c, e)
        plan = build_plan(store)
        self.assertEqual(
            [ent.anchor_fact_id for ent in plan.sections["experience"].entities],
            [r.id],
        )
        self.assertIn(
            c.id, {a.fact_id for a in plan.sections["certifications"].facts},
        )
        self.assertIn(
            e.id, {a.fact_id for a in plan.sections["education"].facts},
        )
        self.assertFalse(
            any(n.startswith("resolver:") for n in plan.notes),
            f"resolver should be inert; got notes={plan.notes!r}",
        )

    def test_resolver_logs_substance_rationale(self):
        """plan.notes carries the resolver's per-cluster rationale
        (which signals fired, why this section). Required for
        explainability / future debug."""
        role = _fact(
            id="r1", type_=FactType.ROLE,
            claim="AI Engineer at OrgCo",
            evidence="AI Engineer @ OrgCo — Jan 2024 - Dec 2024",
            entity_id="cv:role|orgco|ai engineer",
            entity_display="AI Engineer @ OrgCo",
            source="structured_profile",
        )
        role_ach = _fact(
            id="r1-a1", type_=FactType.ACHIEVEMENT,
            claim="Built the production pipeline end-to-end.",
            evidence="Built the production pipeline end-to-end.",
            entity_id=role.entity_id,
            entity_display=role.entity_display,
            source="structured_profile",
        )
        cred = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="AI Engineer Programme — OrgCo",
            evidence="AI Engineer Programme — OrgCo (Jan 2024 - Dec 2024)",
            entity_id="cv:cred|ai engineer programme|orgco",
            entity_display="AI Engineer Programme",
            source="structured_profile",
        )
        store = self._store_with(role, role_ach, cred)
        plan = build_plan(store)
        resolver_notes = [n for n in plan.notes if n.startswith("resolver:")]
        self.assertEqual(len(resolver_notes), 1)
        note = resolver_notes[0]
        self.assertIn("substance signals fired", note)
        self.assertIn("section='experience'", note)


class ResolverHelperUnitTests(SimpleTestCase):
    """Direct unit coverage of the resolver's matching + substance
    primitives — keeps threshold tuning honest as the codebase
    evolves."""

    def test_same_entity_matches_strong_overlap(self):
        from resumes.services.resume_planner_v2 import _same_entity
        a = _fact(
            id="r1", type_=FactType.ROLE,
            claim="AI Trainee at AcmeTrack",
            evidence="AI Trainee @ AcmeTrack — 2024",
            entity_id="cv:role|acmetrack|ai trainee",
            entity_display="AI Trainee @ AcmeTrack",
        )
        b = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="AcmeTrack AI Track — AcmeTrack",
            evidence="AcmeTrack AI Track — AcmeTrack",
            entity_id="cv:cred|acmetrack ai track|acmetrack",
            entity_display="AcmeTrack AI Track",
        )
        self.assertTrue(_same_entity(a, b))

    def test_same_entity_rejects_same_type(self):
        """Same-type dedup is the signal_merger's job; the resolver
        never collapses ROLE×ROLE or CRED×CRED."""
        from resumes.services.resume_planner_v2 import _same_entity
        a = _fact(
            id="r1", type_=FactType.ROLE,
            claim="X at Y",
            entity_id="cv:role|y|x",
            entity_display="X @ Y",
            evidence="X @ Y",
        )
        b = _fact(
            id="r2", type_=FactType.ROLE,
            claim="X at Y",
            entity_id="cv:role|y|x",
            entity_display="X @ Y",
            evidence="X @ Y",
        )
        self.assertFalse(_same_entity(a, b))

    def test_same_entity_rejects_unrelated_orgs(self):
        from resumes.services.resume_planner_v2 import _same_entity
        a = _fact(
            id="r1", type_=FactType.ROLE,
            claim="Engineer at AcmeCo",
            entity_id="cv:role|acmeco|engineer",
            entity_display="Engineer @ AcmeCo",
            evidence="Engineer @ AcmeCo",
        )
        b = _fact(
            id="c1", type_=FactType.CREDENTIAL,
            claim="AI Cert — BetaCorp",
            entity_id="cv:cred|ai cert|betacorp",
            entity_display="AI Cert",
            evidence="AI Cert — BetaCorp",
        )
        self.assertFalse(_same_entity(a, b))

    def test_decide_section_threshold(self):
        from resumes.services.resume_planner_v2 import _decide_section
        # 2+ signals → experience.
        self.assertEqual(
            _decide_section({
                "duration": True, "org_relationship": True,
                "deliverables": False, "applied_language": False,
            }),
            "experience",
        )
        self.assertEqual(
            _decide_section({
                "duration": True, "org_relationship": True,
                "deliverables": True, "applied_language": True,
            }),
            "experience",
        )
        # 1 signal (ambiguous middle) → certifications conservative.
        self.assertEqual(
            _decide_section({
                "duration": False, "org_relationship": True,
                "deliverables": False, "applied_language": False,
            }),
            "certifications",
        )
        # 0 signals → certifications.
        self.assertEqual(
            _decide_section({
                "duration": False, "org_relationship": False,
                "deliverables": False, "applied_language": False,
            }),
            "certifications",
        )

    def test_cert_platform_org_does_not_count_as_relationship(self):
        """A 'DataCamp' / 'Coursera' issuer is administrative
        metadata, not an org relationship. Real employers / training
        initiatives do count."""
        from resumes.services.resume_planner_v2 import (
            _is_cert_platform, _canonical_alnum,
        )
        self.assertTrue(_is_cert_platform(_canonical_alnum("DataCamp")))
        self.assertTrue(_is_cert_platform(_canonical_alnum("Coursera")))
        self.assertTrue(_is_cert_platform(_canonical_alnum("Udemy")))
        self.assertFalse(_is_cert_platform(_canonical_alnum("AcmeCo")))
        self.assertFalse(
            _is_cert_platform(_canonical_alnum("Ministry of Innovation")),
        )


# ===========================================================================
# Per-entity experience cap — a verbose role can't monopolize the
# experience-section budget and starve other real roles.
# ===========================================================================


class PerEntityExperienceCapTests(SimpleTestCase):
    """The experience section's budget is the SECTION cap; without a
    per-entity sub-cap, role #1's many bullets eat the whole budget
    and roles #2 / #3 starve. The per-entity cap distributes the
    budget so every role with content gets a fair share."""

    def _store_with(self, *facts) -> FactStore:
        s = FactStore()
        for f in facts:
            s.add(f)
        return s

    def _role(self, *, idx: int, end_year: int, n_bullets: int):
        """Build a ROLE + n_bullets ACHIEVEMENT facts at a fresh
        entity_id. ``end_year`` controls reverse-chrono order
        (higher = more recent)."""
        company = f"OrgCo-{idx}"
        title = f"Role-{idx}"
        entity_id = f"cv:role|orgco-{idx}|role-{idx}"
        entity_display = f"Role-{idx} @ OrgCo-{idx}"
        role = _fact(
            id=f"role{idx}", type_=FactType.ROLE,
            claim=f"Role-{idx} at OrgCo-{idx}",
            evidence=f"{entity_display} — Jan {end_year} - Dec {end_year}",
            entity_id=entity_id, entity_display=entity_display,
            source="structured_profile",
        )
        bullets = [
            _fact(
                id=f"b{idx}-{j}", type_=FactType.ACHIEVEMENT,
                claim=f"Bullet {idx}.{j}: did the thing.",
                evidence=f"Bullet {idx}.{j}: did the thing.",
                entity_id=entity_id, entity_display=entity_display,
                source="structured_profile",
            )
            for j in range(n_bullets)
        ]
        return [role] + bullets

    def test_per_entity_cap_distributes_budget_fairly(self):
        """3 roles with bullet counts 8/4/2 and per-entity cap of 4:
        each role caps at 4 (or its own bullet count if smaller),
        ALL 3 roles present in allocation (none starved to zero),
        reverse-chronological order preserved."""
        # End years: 2025 > 2024 > 2023 so reverse-chron gives 1, 2, 3.
        store = self._store_with(*(
            self._role(idx=1, end_year=2025, n_bullets=8)
            + self._role(idx=2, end_year=2024, n_bullets=4)
            + self._role(idx=3, end_year=2023, n_bullets=2)
        ))
        plan = build_plan(
            store,
            per_entity_experience_cap=4,
            section_caps={"experience": 12},
        )
        ents = plan.sections["experience"].entities
        # All 3 roles present — none starved.
        self.assertEqual(
            len(ents), 3,
            f"all 3 roles must land in experience; got {[e.entity_display for e in ents]}",
        )
        # Reverse-chrono: role1 (2025) → role2 (2024) → role3 (2023).
        self.assertEqual(
            [e.anchor_fact_id for e in ents],
            ["role1", "role2", "role3"],
        )
        # Per-entity cap holds: role1 gets 4 (capped from 8), role2
        # gets 4 (its actual bullet count), role3 gets 2 (its actual
        # bullet count).
        counts = {e.anchor_fact_id: len(e.facts) for e in ents}
        self.assertEqual(counts["role1"], 4,
                         "role1's 8 bullets must be capped at 4")
        self.assertEqual(counts["role2"], 4)
        self.assertEqual(counts["role3"], 2)
        # Total facts allocated ≤ section budget.
        self.assertLessEqual(sum(counts.values()), 12)

    def test_single_role_with_many_bullets_capped_at_per_entity_cap(self):
        """Even a single role doesn't get to spend the whole section
        budget — the per-entity cap holds. (The budget remains
        available for other roles in future merges.)"""
        store = self._store_with(*self._role(idx=1, end_year=2025, n_bullets=10))
        plan = build_plan(
            store,
            per_entity_experience_cap=4,
            section_caps={"experience": 12},
        )
        ents = plan.sections["experience"].entities
        self.assertEqual(len(ents), 1)
        self.assertEqual(
            len(ents[0].facts), 4,
            "single role with 10 bullets must be capped at 4 — even "
            "when section budget could fit more",
        )

    def test_per_entity_cap_default_is_documented_constant(self):
        """The default cap is exposed for inspection / config; the
        module-level constant is the documented source of truth."""
        from resumes.services.resume_planner_v2 import (
            DEFAULT_PER_ENTITY_EXPERIENCE_CAP,
        )
        # Pin the value — 4 is the documented default; changing it
        # should be a deliberate decision visible in a diff.
        self.assertEqual(DEFAULT_PER_ENTITY_EXPERIENCE_CAP, 4)

    def test_per_entity_cap_records_a_note_when_truncating(self):
        """For diagnostic visibility, the planner should record a
        note when a role's children get truncated by the per-entity
        cap so a future debug session can see WHICH role hit it."""
        store = self._store_with(*self._role(idx=1, end_year=2025, n_bullets=10))
        plan = build_plan(
            store,
            per_entity_experience_cap=3,
            section_caps={"experience": 12},
        )
        # At least one note names the per-entity cap.
        self.assertTrue(
            any("per-entity cap" in n for n in plan.notes),
            f"expected a per-entity-cap note; got {plan.notes!r}",
        )
