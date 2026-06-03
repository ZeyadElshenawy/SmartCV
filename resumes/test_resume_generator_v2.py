"""Isolation tests for resumes.services.resume_generator_v2.

LLM calls are mocked. Tests target the load-bearing properties:

  1. NUMBER GUARD: a generated bullet whose numbers don't trace to
     allocated facts is caught, regenerated once, then DROPPED on
     persistent failure. The final output contains no ungrounded
     number. (This is the test that matters most.)
  2. ALLOWED NUMBERS: a bullet whose numbers DO trace to allocated
     facts passes through.
  3. NORMALIZATION: "541K" → 541000 ≈ allocated value 541000.
  4. CROSS-ENTITY isolation: entity B's bullet prompt does not
     receive entity A's facts; A's metric cannot leak into B.
  5. HEDGE flag forwarding: hedged allocated fact → bullet
     ``hedged=True``.
  6. End-to-end shape: a populated PlanResult yields prose for
     every section, with traceable fact_ids.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from resumes.services.fact_store import (
    FactRecord,
    FactStore,
    FactType,
    SourceReliability,
)
from resumes.services.resume_generator_v2 import (
    FabricationEvent,
    GeneratedResumeV2,
    _allowed_numbers_from_facts,
    _normalize_number,
    _numbers_in,
    _ungrounded_numbers,
    generate_resume_v2,
)
from resumes.services.resume_planner_v2 import build_plan


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


def _populated_store():
    """A small but realistic store: one role, one project, with
    achievements + metrics on each, plus a skill and an education
    entry. Enough material to exercise every section path."""
    store = FactStore()

    # Role + role children.
    store.add(_fact(
        id="r1", type_=FactType.ROLE,
        claim="AI Trainee at DEPI",
        evidence="AI Trainee, DEPI — Jun 2025 - Dec 2025",
        entity_id="cv:role|depi|ai trainee",
        entity_display="AI Trainee @ DEPI",
    ))
    store.add(_fact(
        id="r1_ach1", type_=FactType.ACHIEVEMENT,
        claim="Shipped healthcare-prediction pipeline.",
        evidence="Shipped to the DEPI cohort with end-to-end pipeline.",
        entity_id="cv:role|depi|ai trainee",
    ))
    store.add(_fact(
        id="r1_metric", type_=FactType.METRIC,
        claim="Reduced nightly data load by 6 hours.",
        evidence="Reduced nightly data load by 6 hours",
        value=6.0, unit="hours",
        entity_id="cv:role|depi|ai trainee",
    ))

    # Project + project children.
    store.add(_fact(
        id="p1", type_=FactType.PROJECT,
        claim="Healthcare prediction app.",
        evidence="A healthcare prediction app built with Flask and MLflow.",
        entity_id="https://github.com/z/healthcare",
        entity_display="Healthcare Prediction",
    ))
    store.add(_fact(
        id="p1_ach1", type_=FactType.ACHIEVEMENT,
        claim="End-to-end pipeline ingestion through serving.",
        evidence="ingestion → preprocessing → training → serving via Flask",
        entity_id="https://github.com/z/healthcare",
    ))
    store.add(_fact(
        id="p1_metric", type_=FactType.METRIC,
        claim="0.6027 decision threshold via PR-curve.",
        evidence="Decision threshold tuned to 0.6027 via PR curve",
        value=0.6027, unit=None,
        entity_id="https://github.com/z/healthcare",
    ))

    # Skill + education.
    store.add(_fact(
        id="sk_py", type_=FactType.SKILL, claim="Python",
        evidence="Python is a skill",
    ))
    store.add(_fact(
        id="edu1", type_=FactType.EDUCATION,
        claim="BSc Computer Science, KSIU",
        evidence="BSc Computer Science, KSIU — 2027 (expected)",
        entity_id="cv:edu|ksiu|bsc cs",
    ))
    store.add(_fact(
        id="cred1", type_=FactType.CREDENTIAL,
        claim="AI Specialization",
        evidence="Completed AI Specialization",
        entity_id="cred:ai_spec",
        reliability=SourceReliability.PLATFORM_VERIFIED,
    ))
    return store


# ===========================================================================
# Pure-helper tests (no LLM)
# ===========================================================================


class NumberNormalizationTests(SimpleTestCase):

    def test_plain_integer(self):
        self.assertEqual(_normalize_number("337"), 337.0)

    def test_decimal(self):
        self.assertAlmostEqual(_normalize_number("0.6027"), 0.6027)

    def test_percentage(self):
        self.assertEqual(_normalize_number("40%"), 40.0)
        self.assertAlmostEqual(_normalize_number("4.9%"), 4.9)

    def test_comma_thousands(self):
        self.assertEqual(_normalize_number("1,470"), 1470.0)
        self.assertEqual(_normalize_number("541,000"), 541000.0)

    def test_k_suffix(self):
        self.assertEqual(_normalize_number("541K"), 541_000.0)
        self.assertEqual(_normalize_number("1.2k"), 1_200.0)

    def test_m_suffix(self):
        self.assertEqual(_normalize_number("1.2M"), 1_200_000.0)

    def test_b_suffix(self):
        self.assertEqual(_normalize_number("3B"), 3_000_000_000.0)

    def test_garbage_returns_none(self):
        self.assertIsNone(_normalize_number("abc"))
        self.assertIsNone(_normalize_number(""))
        self.assertIsNone(_normalize_number(None))


class NumbersInTests(SimpleTestCase):

    def test_extracts_multiple(self):
        out = _numbers_in("Achieved 0.89 ROC-AUC on 50,000 samples in 6 hours")
        self.assertEqual(out, {0.89, 50000.0, 6.0})

    def test_handles_punctuation(self):
        """Numbers followed by punctuation must still parse."""
        out = _numbers_in("Reached 0.89 ROC-AUC, 40% recall.")
        self.assertEqual(out, {0.89, 40.0})

    def test_ignores_numbers_embedded_in_identifiers(self):
        """'Python3' should not contribute '3' to the number pool."""
        out = _numbers_in("Used Python3 and ABC123 for tests")
        # '3' from 'Python3' must NOT appear; '123' embedded in ABC123 also not.
        self.assertNotIn(3.0, out)
        self.assertNotIn(123.0, out)


class AllowedNumbersFromFactsTests(SimpleTestCase):

    def test_collects_value_and_text_numbers(self):
        facts = [
            _fact(id="a", type_=FactType.METRIC, claim="0.89 ROC-AUC",
                  evidence="0.89 ROC-AUC on the test set",
                  value=0.89, unit="ROC-AUC",
                  entity_id="ent"),
            _fact(id="b", type_=FactType.ACHIEVEMENT,
                  claim="Reduced load by 6 hours",
                  evidence="Reduced nightly data load by 6 hours across 1,200 jobs",
                  entity_id="ent"),
        ]
        allowed = _allowed_numbers_from_facts(facts)
        self.assertIn(0.89, allowed)
        self.assertIn(6.0, allowed)
        self.assertIn(1200.0, allowed)

    def test_facts_without_numbers_contribute_nothing(self):
        facts = [
            _fact(id="x", type_=FactType.SKILL, claim="Python",
                  evidence="Python is a skill"),
        ]
        self.assertEqual(_allowed_numbers_from_facts(facts), set())


class UngroundedNumbersTests(SimpleTestCase):

    def test_grounded_number_passes(self):
        allowed = {0.89, 6.0}
        bad = _ungrounded_numbers(
            "Achieved 0.89 ROC-AUC, saved 6 hours.", allowed,
        )
        self.assertEqual(bad, [])

    def test_ungrounded_number_caught(self):
        allowed = {0.6027, 4.9}
        bad = _ungrounded_numbers(
            "Achieved 84% recall on the test set.", allowed,
        )
        self.assertIn(84.0, bad)

    def test_comma_suffix_normalization_grounds(self):
        """Allocated value 541000; LLM writes '541K' — same number."""
        allowed = {541_000.0}
        bad = _ungrounded_numbers(
            "Processed 541K transactions in production.", allowed,
        )
        self.assertEqual(bad, [],
                         f"541K should ground to 541000; got {bad!r}")

    def test_decimal_vs_percent_compatibility(self):
        """Allocated 0.89 ROC-AUC; LLM writes '89%' — accepted via the
        ×100 compatibility window."""
        allowed = {0.89}
        bad = _ungrounded_numbers(
            "Achieved 89% on the validation set.", allowed,
        )
        self.assertEqual(bad, [])


# ===========================================================================
# Generation flow tests — LLM mocked
# ===========================================================================


class GenerateBulletHappyPathTests(SimpleTestCase):
    """When the LLM returns prose grounded in the allocated facts,
    the bullet passes through and carries the fact_ids."""

    def test_grounded_bullet_passes_with_fact_ids(self):
        store = _populated_store()
        plan = build_plan(store, job_must_have_skills=["Python"])
        # Mock the LLM to echo back grounded text using a real number
        # from the allocated facts.
        def _stub(prompt, **kw):
            if "professional summary" in prompt.lower():
                return "Junior data scientist focused on ML production."
            if "AI Trainee" in prompt:
                return "Engineered a healthcare-prediction pipeline that cut nightly data load by 6 hours."
            if "Healthcare Prediction" in prompt:
                return "Built end-to-end pipeline; tuned decision threshold to 0.6027 via PR-curve analysis."
            return "Wrote a thing."
        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            resume = generate_resume_v2(store, plan, job_title="Data Scientist")
        # The fabrication log is empty (all numbers grounded).
        self.assertEqual(resume.fabrication_events, [])
        # Experience block has a bullet referencing 6 hours (allowed).
        exp = resume.sections["experience"].entities
        self.assertTrue(exp)
        depi = next(e for e in exp if "depi" in e.entity_id.lower())
        self.assertTrue(depi.bullets)
        self.assertIn("6 hours", depi.bullets[0].text)
        # Bullet carries fact_ids for traceability.
        self.assertTrue(depi.bullets[0].fact_ids)


class FabricationGuardTests(SimpleTestCase):
    """THE critical class. The LLM is mocked to inject numbers that
    DON'T appear in allocated facts. The guard must catch them,
    regenerate once, then drop on persistent failure. Final output
    must contain no ungrounded number."""

    def test_persistent_fabrication_is_dropped(self):
        store = _populated_store()
        plan = build_plan(store, job_must_have_skills=["Python"])
        # The DEPI role's allocated metric value is 6.0 (hours). The
        # LLM keeps trying to emit "84% recall" instead.
        depi_call_count = {"n": 0}
        def _stub(prompt, **kw):
            # The DEPI role's prompt mentions the role hint string;
            # match on the entity_display we set.
            if "DEPI" in prompt:
                depi_call_count["n"] += 1
                # Both first attempt AND regen attempt return an
                # ungrounded number — the bullet must DROP.
                return f"Achieved 84% recall on stroke prediction (attempt {depi_call_count['n']})."
            # Everything else returns grounded text.
            if "professional summary" in prompt.lower():
                return "Junior data scientist."
            if "Healthcare Prediction" in prompt:
                return "Built end-to-end pipeline; threshold tuned to 0.6027."
            return "Some grounded text."
        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            resume = generate_resume_v2(store, plan, job_title="Data Scientist")
        # Regen-then-drop fired: TWO calls on the DEPI bullet (first +
        # one regeneration).
        self.assertEqual(depi_call_count["n"], 2,
                         "expected one initial + one regen call on the DEPI bullet")
        # Two events logged: one regenerated + one dropped.
        depi_events = [
            e for e in resume.fabrication_events
            if "depi" in (e.entity_id or "").lower()
        ]
        self.assertEqual(len(depi_events), 2)
        self.assertEqual(depi_events[0].action, "regenerated")
        self.assertEqual(depi_events[1].action, "dropped")
        # Final DEPI block has NO bullet containing the ungrounded 84.
        depi_entity = next(
            e for e in resume.sections["experience"].entities
            if "depi" in e.entity_id.lower()
        )
        for b in depi_entity.bullets:
            self.assertNotIn("84%", b.text,
                             f"dropped bullet leaked into output: {b.text!r}")
            self.assertNotIn(" 84 ", " " + b.text + " ")

    def test_regenerate_succeeds_keeps_bullet(self):
        """First attempt fabricates; regen produces grounded prose →
        bullet is KEPT, one event logged as 'regenerated'."""
        store = _populated_store()
        plan = build_plan(store, job_must_have_skills=["Python"])
        depi_calls = {"n": 0}
        def _stub(prompt, **kw):
            if "DEPI" in prompt:
                depi_calls["n"] += 1
                if depi_calls["n"] == 1:
                    return "Hit 84% recall on the test set."  # fabricated
                return "Cut nightly data load by 6 hours via pipeline overhaul."
            if "professional summary" in prompt.lower():
                return "Junior data scientist."
            if "Healthcare Prediction" in prompt:
                return "Built pipeline; threshold 0.6027."
            return "Some text."
        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            resume = generate_resume_v2(store, plan, job_title="Data Scientist")
        # One regenerated event for DEPI (not dropped).
        depi_events = [
            e for e in resume.fabrication_events
            if "depi" in (e.entity_id or "").lower()
        ]
        self.assertEqual(len(depi_events), 1)
        self.assertEqual(depi_events[0].action, "regenerated")
        # Final bullet contains the GROUNDED 6 hours.
        depi_entity = next(
            e for e in resume.sections["experience"].entities
            if "depi" in e.entity_id.lower()
        )
        self.assertTrue(depi_entity.bullets)
        self.assertIn("6 hours", depi_entity.bullets[0].text)
        self.assertNotIn("84", depi_entity.bullets[0].text)


class CrossEntityIsolationTests(SimpleTestCase):
    """Entity B's bullet prompt must not contain entity A's facts.
    Numbers from A's metrics cannot leak into B even if the LLM
    tried — they aren't in the allowed pool for B."""

    def test_other_entity_metric_not_in_prompt(self):
        store = _populated_store()
        plan = build_plan(store, job_must_have_skills=["Python"])
        captured_prompts = []
        def _stub(prompt, **kw):
            captured_prompts.append(prompt)
            return "Some grounded text."
        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            generate_resume_v2(store, plan, job_title="Data Scientist")
        # Find the project (Healthcare) prompt — it must NOT contain
        # the role's metric (6 hours / value=6.0) or the role's
        # achievement claim.
        for p in captured_prompts:
            if "Healthcare Prediction" in p:
                # Role's specific metric ("6 hours") must not appear
                # in the project's facts block.
                self.assertNotIn(
                    "Reduced nightly data load", p,
                    "DEPI role's achievement leaked into Healthcare project prompt",
                )

    def test_other_entity_number_caught_if_llm_tries(self):
        """The DEPI role's allocated numbers are {6.0}. If a
        Healthcare-project bullet's prompt somehow emits '6 hours',
        the number guard for THAT project will catch it (Healthcare's
        allowed pool has 0.6027 only)."""
        store = _populated_store()
        plan = build_plan(store, job_must_have_skills=["Python"])
        def _stub(prompt, **kw):
            if "Healthcare Prediction" in prompt:
                # The LLM hallucinates the DEPI metric onto Healthcare.
                # Allowed pool for Healthcare = {0.6027}. 6 is ungrounded.
                return "Saved 6 hours of training time."
            if "DEPI" in prompt:
                return "Cut nightly load by 6 hours."
            if "professional summary" in prompt.lower():
                return "Junior data scientist."
            return "OK."
        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            resume = generate_resume_v2(store, plan, job_title="Data Scientist")
        # Healthcare events captured the fabrication.
        hc_events = [
            e for e in resume.fabrication_events
            if "healthcare" in (e.entity_id or "").lower()
        ]
        self.assertTrue(hc_events,
                        "expected fabrication catch on Healthcare project")
        self.assertIn(6.0, hc_events[0].ungrounded_numbers)


class HedgePropagationTests(SimpleTestCase):
    """A hedged allocated fact yields a bullet with hedged=True. The
    prompt also instructs the LLM to phrase the number with a
    qualifier (~ / approximately) — soft constraint."""

    def test_hedged_fact_flags_bullet(self):
        store = FactStore()
        store.add(_fact(
            id="p1", type_=FactType.PROJECT,
            claim="Customer-segmentation analysis",
            evidence="RFM customer segmentation on transactional data",
            entity_id="https://github.com/z/segmentation",
            entity_display="Customer Segmentation",
        ))
        store.add(_fact(
            id="m_hedged", type_=FactType.METRIC,
            claim="~541K transactions",
            evidence="~541K transactions in the dataset",
            value=541_000.0, unit="transactions",
            entity_id="https://github.com/z/segmentation",
            hedged=True,
        ))
        plan = build_plan(store, job_must_have_skills=["Python"])
        def _stub(prompt, **kw):
            return "Analyzed ~541K transactions to segment customers."
        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            resume = generate_resume_v2(store, plan, job_title="Data Scientist")
        proj_entity = resume.sections["projects"].entities[0]
        self.assertTrue(proj_entity.bullets)
        # The hedged flag propagates.
        self.assertTrue(
            proj_entity.bullets[0].hedged,
            "hedged metric should set GeneratedBullet.hedged=True",
        )
        # The bullet text uses the hedge qualifier (not asserted hard,
        # but the stubbed LLM did include it).
        self.assertIn("541K", proj_entity.bullets[0].text)


class SectionShapeTests(SimpleTestCase):
    """End-to-end shape: every section yields prose with traceable
    fact_ids, no surprises."""

    def test_full_resume_has_every_non_summary_section(self):
        """``generate_resume_v2`` populates skills + experience + projects +
        education + certifications. Summary is intentionally NOT populated
        here — Layer 5 Full synthesises it AFTER the reviewer settles, via
        ``_synthesize_summary_from_sections`` called from the dispatcher.
        ``_generate_summary`` is still defined and exercised by other tests
        for the direct-call path."""
        store = _populated_store()
        plan = build_plan(store, job_must_have_skills=["Python"])
        def _stub(prompt, **kw):
            # Trivial grounded text by section keyword.
            if "DEPI" in prompt:
                return "Cut nightly load by 6 hours."
            if "Healthcare Prediction" in prompt:
                return "Built pipeline; threshold 0.6027."
            return "Did stuff."
        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            resume = generate_resume_v2(store, plan, job_title="Data Scientist")
        # All non-summary sections present.
        self.assertEqual(
            set(resume.sections.keys()),
            {"skills", "experience",
             "projects", "education", "certifications"},
        )
        # Summary intentionally absent here — the dispatcher populates it
        # via ``_synthesize_summary_from_sections`` after the reviewer runs.
        self.assertNotIn("summary", resume.sections)
        # Skills is a comma-separated line.
        self.assertIn("Python", resume.sections["skills"].skills_line)
        # Education + certs render as lines.
        self.assertTrue(resume.sections["education"].lines)
        self.assertTrue(resume.sections["certifications"].lines)
        # Experience + projects have entity blocks.
        self.assertTrue(resume.sections["experience"].entities)
        self.assertTrue(resume.sections["projects"].entities)

    def test_skills_line_lists_planner_skills_in_order(self):
        """No LLM for skills — the line is the planner's allocation
        joined with ', '. Deterministic by build_plan's ranking."""
        store = FactStore()
        store.add(_fact(id="s_py", type_=FactType.SKILL, claim="Python",
                        evidence="Python skill"))
        store.add(_fact(id="s_sql", type_=FactType.SKILL, claim="SQL",
                        evidence="SQL skill"))
        plan = build_plan(store, job_must_have_skills=["Python"])
        # No LLM should be called for the skills section. Stub returns
        # something obvious so a leak would show up.
        def _stub(prompt, **kw):
            return "LLM_LEAK"
        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            resume = generate_resume_v2(store, plan, job_title="Engineer")
        self.assertIn("Python", resume.sections["skills"].skills_line)
        self.assertNotIn("LLM_LEAK", resume.sections["skills"].skills_line)


# ---------------------------------------------------------------------------
# Fix B — JD emphasis prompt block + JD-skill grounding guard
# ---------------------------------------------------------------------------


class JDEmphasisPromptBlockTests(SimpleTestCase):
    """The emphasis block renders only when JD lists are non-empty, drops
    cleanly otherwise, and labels itself as guidance (not facts)."""

    def _facts(self):
        return [
            _fact(id="role", type_=FactType.ROLE,
                  claim="Engineer at Acme",
                  evidence="Engineer at Acme",
                  entity_id="cv:role|acme|eng",
                  entity_display="Engineer @ Acme"),
            _fact(id="ach", type_=FactType.ACHIEVEMENT,
                  claim="Built a Python data pipeline",
                  evidence="Built a Python data pipeline",
                  entity_id="cv:role|acme|eng"),
        ]

    def test_block_absent_when_both_lists_empty(self):
        from resumes.services.resume_generator_v2 import _bullet_prompt
        p = _bullet_prompt(
            role_hint="a experience entry: 'Engineer @ Acme' (targeting Engineer)",
            facts=self._facts(), section="experience",
            writing_rules_block="", digest_text="",
            regen_feedback="",
            jd_must=[], jd_nice=[],
        )
        self.assertNotIn("=== JD EMPHASIS", p)
        self.assertNotIn("must-have skills", p)

    def test_block_present_when_must_have_lists_non_empty(self):
        from resumes.services.resume_generator_v2 import _bullet_prompt
        p = _bullet_prompt(
            role_hint="a experience entry: 'Engineer @ Acme'",
            facts=self._facts(), section="experience",
            writing_rules_block="", digest_text="",
            regen_feedback="",
            jd_must=["Python", "Flutter"],
            jd_nice=["Docker"],
        )
        self.assertIn("=== JD EMPHASIS", p)
        self.assertIn("NOT facts about the candidate", p)
        self.assertIn("JD must-have skills: Python, Flutter", p)
        self.assertIn("JD nice-to-have skills: Docker", p)
        self.assertIn("EMPHASIS ONLY", p)
        self.assertIn("=== END JD EMPHASIS", p)

    def test_block_absent_in_summary_when_lists_empty(self):
        from resumes.services.resume_generator_v2 import _bullet_prompt
        p = _bullet_prompt(
            role_hint="the professional summary for a Engineer role",
            facts=self._facts(), section="summary",
            writing_rules_block="", digest_text="",
            regen_feedback="",
            jd_must=[], jd_nice=[],
        )
        self.assertNotIn("=== JD EMPHASIS", p)


class JDSkillGroundingGuardUnitTests(SimpleTestCase):
    """The _ungrounded_jd_skills helper — match shape + known-limit
    documentation."""

    def _facts_with_python(self):
        return [_fact(
            id="ach", type_=FactType.ACHIEVEMENT,
            claim="Built a data pipeline in Python",
            evidence="Built a data pipeline in Python",
            entity_id="cv:role|acme|eng",
        )]

    def test_unsupported_skill_in_bullet_flagged(self):
        from resumes.services.resume_generator_v2 import _ungrounded_jd_skills
        # Bullet claims Flutter; fact has only Python.
        bad = _ungrounded_jd_skills(
            text="Built a Flutter app on top of the pipeline",
            facts=self._facts_with_python(),
            jd_must=["Flutter", "Python"],
        )
        self.assertEqual(bad, ["Flutter"])

    def test_supported_skill_not_flagged_exact(self):
        from resumes.services.resume_generator_v2 import _ungrounded_jd_skills
        bad = _ungrounded_jd_skills(
            text="Built a Python data pipeline reducing nightly load",
            facts=self._facts_with_python(),
            jd_must=["Python"],
        )
        self.assertEqual(bad, [])

    def test_supported_skill_not_flagged_case_insensitive(self):
        """Word-boundary regex is case-insensitive; PYTHON in the bullet
        still matches Python in the facts."""
        from resumes.services.resume_generator_v2 import _ungrounded_jd_skills
        bad = _ungrounded_jd_skills(
            text="PYTHON pipeline shipped",
            facts=self._facts_with_python(),
            jd_must=["Python"],
        )
        self.assertEqual(bad, [])

    def test_no_jd_must_means_no_check(self):
        from resumes.services.resume_generator_v2 import _ungrounded_jd_skills
        bad = _ungrounded_jd_skills(
            text="Built a Flutter app",
            facts=self._facts_with_python(),
            jd_must=[],
        )
        self.assertEqual(bad, [])

    def test_skill_not_in_text_is_never_flagged(self):
        from resumes.services.resume_generator_v2 import _ungrounded_jd_skills
        bad = _ungrounded_jd_skills(
            text="Built a Python data pipeline",
            facts=self._facts_with_python(),
            jd_must=["Flutter"],  # Flutter not in text → safe
        )
        self.assertEqual(bad, [])

    def test_known_limitation_acronym_false_positive_documented(self):
        """Documents the known false-positive direction: a bullet using an
        ACRONYM for a skill the facts spell out fully gets flagged. This
        is why the caller degrades to KEEP+FLAG instead of dropping."""
        from resumes.services.resume_generator_v2 import _ungrounded_jd_skills
        facts = [_fact(
            id="ach", type_=FactType.ACHIEVEMENT,
            claim="Trained a machine learning model on tabular data",
            evidence="Trained a machine learning model on tabular data",
            entity_id="cv:role|acme|eng",
        )]
        bad = _ungrounded_jd_skills(
            text="Trained an ML model with strong accuracy",
            facts=facts,
            jd_must=["ML"],  # bullet says ML, facts say "machine learning"
        )
        # This IS a false positive — guard cannot equate "ML" ≡ "Machine Learning".
        # Documented behavior so future fixes can target it specifically.
        self.assertEqual(bad, ["ML"])


class JDSkillGuardIntegrationTests(SimpleTestCase):
    """The composed flow inside _generate_one_bullet: regen-with-feedback,
    keep-and-flag on persistent skill fabrication, and number-lock
    composition."""

    def _facts(self):
        return [
            _fact(id="role", type_=FactType.ROLE,
                  claim="Engineer at Acme",
                  evidence="Engineer at Acme",
                  entity_id="cv:role|acme|eng",
                  entity_display="Engineer @ Acme"),
            _fact(id="ach", type_=FactType.ACHIEVEMENT,
                  claim="Built a Python data pipeline reducing nightly load",
                  evidence="Built a Python data pipeline reducing nightly load",
                  entity_id="cv:role|acme|eng"),
        ]

    def _call(self, *, llm_outputs, jd_must=("Python", "Flutter"),
              allowed_numbers=None):
        """Run _generate_one_bullet with a stub LLM that returns the
        supplied outputs in order across calls."""
        from resumes.services.resume_generator_v2 import (
            _generate_one_bullet, FabricationEvent,
        )
        events: list = []
        calls = {"n": 0}
        def stub(prompt, **kw):
            i = calls["n"]
            calls["n"] += 1
            return llm_outputs[min(i, len(llm_outputs) - 1)]
        with patch("resumes.services.resume_generator_v2._llm_call",
                   side_effect=stub):
            b = _generate_one_bullet(
                section="experience", entity_id="cv:role|acme|eng",
                role_hint="a experience entry: 'Engineer @ Acme'",
                facts=self._facts(),
                allowed_numbers=set(allowed_numbers or []),
                events=events,
                writing_rules_block="",
                jd_must=list(jd_must),
                jd_nice=[],
            )
        return b, events, calls["n"]

    def test_skill_fabrication_triggers_regen_then_clean_keeps_bullet(self):
        """First LLM output references Flutter (not in facts) → regen with
        skill feedback → second output drops Flutter → returns clean."""
        bullet, events, n_calls = self._call(
            llm_outputs=[
                "Built a Flutter app on top of the Python pipeline.",
                "Built a Python data pipeline reducing nightly load by hours.",
            ],
            jd_must=["Python", "Flutter"],
        )
        self.assertIsNotNone(bullet)
        self.assertNotIn("Flutter", bullet.text)
        self.assertEqual(n_calls, 2)  # one regen
        # First-attempt regenerate event captured.
        regen_events = [e for e in events if e.action == "regenerated"]
        self.assertEqual(len(regen_events), 1)
        self.assertEqual(regen_events[0].ungrounded_skills, ["Flutter"])

    def test_skill_fabrication_persistent_keeps_bullet_and_records_event(self):
        """If regen STILL trips the guard, keep+record. Never drop on
        skill-only failure."""
        bullet, events, n_calls = self._call(
            llm_outputs=[
                "Built a Flutter app",
                "Designed a Flutter Frontend",
            ],
            jd_must=["Python", "Flutter"],
        )
        # KEPT — not None.
        self.assertIsNotNone(bullet)
        self.assertIn("Flutter", bullet.text)
        # Event recorded with jd_skill_ungrounded action.
        flagged = [e for e in events if e.action == "jd_skill_ungrounded"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].ungrounded_skills, ["Flutter"])
        # And the first-attempt regenerate event for the same skill.
        regen_events = [e for e in events if e.action == "regenerated"]
        self.assertEqual(len(regen_events), 1)

    def test_number_lock_and_jd_skill_compose_in_one_regen(self):
        """A bullet that trips BOTH number-lock AND skill guard gets one
        combined-feedback regen, not two separate cycles."""
        bullet, events, n_calls = self._call(
            llm_outputs=[
                # First attempt: fabricated number 99 AND fabricated skill Flutter.
                "Built a Flutter app saving 99 hours weekly with the pipeline",
                # Regen: clean on both — only allowed grounded content.
                "Built a Python data pipeline reducing nightly load",
            ],
            jd_must=["Python", "Flutter"],
            allowed_numbers=set(),  # no allowed numbers
        )
        self.assertIsNotNone(bullet)
        self.assertNotIn("Flutter", bullet.text)
        self.assertNotIn("99", bullet.text)
        # Exactly ONE regen (n_calls=2), not two separate cycles.
        self.assertEqual(n_calls, 2)
        # Both kinds of regenerate events from the first-attempt catch.
        regen_events = [e for e in events if e.action == "regenerated"]
        self.assertEqual(len(regen_events), 2)
        # One records ungrounded_numbers, the other ungrounded_skills.
        nums = [e for e in regen_events if e.ungrounded_numbers]
        sks = [e for e in regen_events if e.ungrounded_skills]
        self.assertEqual(len(nums), 1)
        self.assertEqual(len(sks), 1)
        self.assertEqual(nums[0].ungrounded_numbers, [99.0])
        self.assertEqual(sks[0].ungrounded_skills, ["Flutter"])

    def test_number_persistent_drops_even_if_skill_clean(self):
        """When numbers persist after regen, drop the bullet (existing
        rule, unchanged). Existing number-only behaviour stays correct."""
        bullet, events, n_calls = self._call(
            llm_outputs=[
                # Both attempts have the bad number, no skill issue.
                "Built a Python pipeline saving 99 hours weekly",
                "Built a Python pipeline saving 99 hours per week",
            ],
            jd_must=["Python"],
            allowed_numbers=set(),
        )
        self.assertIsNone(bullet)
        dropped = [e for e in events if e.action == "dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].ungrounded_numbers, [99.0])
