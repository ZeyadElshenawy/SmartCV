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
# Fix A — user-asserted (self-reported) skills: skills-line only, never bullets
# ---------------------------------------------------------------------------


class FixAUserAssertedSkillsTests(SimpleTestCase):
    """A user_asserted (chip-moved, unevidenced) skill appears in the rendered
    skills LINE as a plain keyword — never a fact, never a bullet, never the
    summary's grounded fact pool. The safety property is structural: no fact
    => the entity-bullet path (which consumes facts) cannot reach it."""

    def _skills_store(self):
        store = FactStore()
        store.add(_fact(id="s_py", type_=FactType.SKILL, claim="Python",
                        evidence="Python skill"))
        store.add(_fact(id="s_rest", type_=FactType.SKILL, claim="RESTful APIs",
                        evidence="REST skill"))
        return store

    def test_surfacing_and_safety_in_skills_line(self):
        from resumes.services.resume_generator_v2 import _generate_skills_line
        store = self._skills_store()
        section = build_plan(store, job_must_have_skills=["Python"]).sections["skills"]
        out = _generate_skills_line(store, section, extra_list_only=["GoRouter", "Dio"])
        # (surfacing) asserted keywords are in the rendered line
        self.assertIn("GoRouter", out.skills_line)
        self.assertIn("Dio", out.skills_line)
        # (SAFETY) no skills "bullet" carries them, no fact_id resolves to them,
        # and crucially no fact was ever created for them.
        self.assertNotIn("GoRouter", [b.text for b in out.bullets])
        self.assertNotIn("Dio", [b.text for b in out.bullets])
        for b in out.bullets:
            for fid in (b.fact_ids or []):
                self.assertNotIn(store.get(fid).claim, ("GoRouter", "Dio"))
        self.assertFalse(
            any(f.claim in ("GoRouter", "Dio") for f in store.all()),
            "asserted skills must NOT become facts",
        )

    def test_dedupe_against_evidenced(self):
        from resumes.services.resume_generator_v2 import _generate_skills_line
        store = self._skills_store()
        section = build_plan(store, job_must_have_skills=["Python"]).sections["skills"]
        # "python" asserted (different case) is already evidenced -> not doubled.
        out = _generate_skills_line(store, section, extra_list_only=["python", "GoRouter"])
        self.assertEqual(out.skills_line.lower().count("python"), 1)
        self.assertIn("GoRouter", out.skills_line)

    def test_back_compat_none_and_empty(self):
        from resumes.services.resume_generator_v2 import _generate_skills_line
        store = self._skills_store()
        section = build_plan(store, job_must_have_skills=["Python"]).sections["skills"]
        base = _generate_skills_line(store, section)
        none_ = _generate_skills_line(store, section, extra_list_only=None)
        empty = _generate_skills_line(store, section, extra_list_only=[])
        self.assertEqual(base.skills_line, none_.skills_line)
        self.assertEqual(base.skills_line, empty.skills_line)
        self.assertEqual([b.text for b in base.bullets], [b.text for b in none_.bullets])

    def test_edge_no_skills_section_still_surfaces(self):
        from resumes.services.resume_generator_v2 import _generate_skills_line
        store = FactStore()
        out = _generate_skills_line(store, None, extra_list_only=["GoRouter"])
        self.assertIn("GoRouter", out.skills_line)
        self.assertEqual(out.bullets, [])

    def test_generate_resume_v2_surfaces_excludes_bullets_and_pool(self):
        store = _populated_store()
        plan = build_plan(store, job_must_have_skills=["Python"])

        def _stub(prompt, **kw):
            return "Built scalable services improving throughput for users."

        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            resume = generate_resume_v2(
                store, plan, job_title="Engineer", user_asserted_skills=["GoRouter"],
            )
        # (surfacing)
        self.assertIn("GoRouter", resume.sections["skills"].skills_line)
        # (SAFETY) no experience/project bullet text or fact references it
        for name in ("experience", "projects"):
            sec = resume.sections.get(name)
            for ent in (getattr(sec, "entities", None) or []):
                for b in ent.bullets:
                    self.assertNotIn("GoRouter", b.text)
                    for fid in (b.fact_ids or []):
                        self.assertNotEqual(getattr(store.get(fid), "claim", None), "GoRouter")
        # (no summary leak) not a skills-section bullet -> no fact_id -> the
        # summary harvest (which collects skills_sec.bullets fact_ids) can't
        # pull it into the grounded pool. And no fact exists for it at all.
        self.assertNotIn("GoRouter", [b.text for b in resume.sections["skills"].bullets])
        self.assertFalse(any(f.claim == "GoRouter" for f in store.all()))

    def test_generate_resume_v2_back_compat_without_param(self):
        store = _populated_store()
        plan = build_plan(store, job_must_have_skills=["Python"])

        def _stub(prompt, **kw):
            return "Built scalable services."

        with patch("resumes.services.resume_generator_v2._llm_call", side_effect=_stub):
            base = generate_resume_v2(store, plan, job_title="Engineer")
            withp = generate_resume_v2(
                store, plan, job_title="Engineer", user_asserted_skills=[],
            )
        self.assertEqual(
            base.sections["skills"].skills_line, withp.sections["skills"].skills_line,
        )


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


# ---------------------------------------------------------------------------
# Fix C — sibling context + opener-collision post-pass
# ---------------------------------------------------------------------------


class SiblingsPromptBlockTests(SimpleTestCase):
    """The SIBLINGS prompt block renders only when prior bullets exist
    and labels itself as guidance (not facts)."""

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

    def test_block_absent_when_siblings_none(self):
        from resumes.services.resume_generator_v2 import _bullet_prompt
        p = _bullet_prompt(
            role_hint="a experience entry: 'Engineer @ Acme'",
            facts=self._facts(), section="experience",
            writing_rules_block="", digest_text="",
            regen_feedback="",
            jd_must=[], jd_nice=[],
            siblings=None,
        )
        self.assertNotIn("=== SIBLINGS", p)

    def test_block_absent_when_siblings_empty_list(self):
        from resumes.services.resume_generator_v2 import _bullet_prompt
        p = _bullet_prompt(
            role_hint="a experience entry: 'Engineer @ Acme'",
            facts=self._facts(), section="experience",
            writing_rules_block="", digest_text="",
            regen_feedback="",
            jd_must=[], jd_nice=[],
            siblings=[],
        )
        self.assertNotIn("=== SIBLINGS", p)

    def test_block_present_when_siblings_populated(self):
        from resumes.services.resume_generator_v2 import _bullet_prompt
        p = _bullet_prompt(
            role_hint="a experience entry: 'Engineer @ Acme'",
            facts=self._facts(), section="experience",
            writing_rules_block="", digest_text="",
            regen_feedback="",
            jd_must=[], jd_nice=[],
            siblings=["Designed the auth flow.", "Built a like system."],
        )
        self.assertIn("=== SIBLINGS", p)
        self.assertIn("NEVER as content", p)
        self.assertIn("Designed the auth flow.", p)
        self.assertIn("Built a like system.", p)
        self.assertIn("=== END SIBLINGS", p)
        # SIBLINGS block sits BEFORE the FACTS block so the LLM reads the
        # diversity hints first, then sees the closed content set.
        self.assertLess(p.index("=== SIBLINGS"), p.index("FACTS (the ONLY"))


class OpenerCollisionDetectionTests(SimpleTestCase):
    """The deterministic _find_opener_collisions helper — its job is to
    catch repeated opening verbs that the per-bullet banned-openings
    check (a hard list of forbidden words) structurally cannot."""

    def test_no_collisions_returns_empty(self):
        from resumes.services.resume_generator_v2 import _find_opener_collisions
        self.assertEqual(
            _find_opener_collisions([
                "Designed the auth flow.",
                "Built a like system with Firestore.",
                "Implemented role-based access control.",
            ]),
            [],
        )

    def test_first_occurrence_wins_later_flagged(self):
        from resumes.services.resume_generator_v2 import _find_opener_collisions
        result = _find_opener_collisions([
            "Designed the auth flow.",
            "Designed flowing patterns.",
            "Built the data layer.",
        ])
        # Index 1 is the "loser" — same opener as index 0.
        self.assertEqual(result, [(1, "designed")])

    def test_leading_noise_stripped_before_compare(self):
        from resumes.services.resume_generator_v2 import _find_opener_collisions
        # LEADING bullet glyphs, quotes, parens, dashes, whitespace are
        # stripped via the shared _LEADING_NOISE_RE so the detector and
        # the banned-openings module agree on what counts as "first word".
        # Trailing punctuation attached to the first token is NOT
        # stripped — the regex is leading-only by design.
        result = _find_opener_collisions([
            "- Designed the auth flow.",
            "* designed flowing patterns",
            "  Designed something else",
        ])
        # Indices 1 and 2 both collide with index 0 after leading-noise strip.
        self.assertEqual(result, [(1, "designed"), (2, "designed")])

    def test_empty_text_skipped(self):
        from resumes.services.resume_generator_v2 import _find_opener_collisions
        # Empty bullets are skipped (no token to compare), not flagged.
        self.assertEqual(
            _find_opener_collisions(["", "Built X", "", "Built Y"]),
            [(3, "built")],
        )

    def test_n_equals_one_no_collision_possible(self):
        from resumes.services.resume_generator_v2 import _find_opener_collisions
        self.assertEqual(_find_opener_collisions(["only one"]), [])


class OpenerCollisionRegenIntegrationTests(SimpleTestCase):
    """The end-to-end behaviour inside _generate_entity_bullets:
    (b) collision triggers regen via the same guarded path;
    (c) regen still passes number-lock + JD-skill;
    (d) one regen per bullet — persistent collision keeps the bullet;
    (e) N=1 entity and bullet #1 are no-ops."""

    def _build_entity_with_two_children(self):
        """Construct a planner-shaped EntityAllocation with one role
        anchor + two distinct achievement child facts. Generic data —
        no profile-specific values."""
        from resumes.services.resume_planner_v2 import (
            EntityAllocation, FactAllocation,
        )
        store = FactStore()
        store.add(_fact(
            id="role", type_=FactType.ROLE,
            claim="Engineer at Acme",
            evidence="Engineer at Acme",
            entity_id="cv:role|acme|eng",
            entity_display="Engineer @ Acme",
        ))
        store.add(_fact(
            id="ach1", type_=FactType.ACHIEVEMENT,
            claim="Built the user authentication flow",
            evidence="Built the user authentication flow",
            entity_id="cv:role|acme|eng",
        ))
        store.add(_fact(
            id="ach2", type_=FactType.ACHIEVEMENT,
            claim="Built the data sync layer",
            evidence="Built the data sync layer",
            entity_id="cv:role|acme|eng",
        ))
        entity = EntityAllocation(
            entity_id="cv:role|acme|eng",
            entity_display="Engineer @ Acme",
            anchor_fact_id="role",
            facts=[
                FactAllocation(fact_id="ach1"),
                FactAllocation(fact_id="ach2"),
            ],
        )
        return store, entity

    def test_collision_triggers_regen_via_guarded_path(self):
        """Two siblings both open with 'Designed' → the loser (index 1) is
        regenerated. Total LLM calls = 3 (bullet 0, bullet 1 (collides),
        bullet 1 regen). Final bullets[1].text starts with a different
        verb."""
        from resumes.services.resume_generator_v2 import (
            _generate_entity_bullets,
        )
        store, entity = self._build_entity_with_two_children()
        outputs = [
            "Designed the user authentication flow with Firebase Auth.",
            "Designed a data-sync layer with bidirectional updates.",
            # Regen for bullet[1] — opens with "Built", different verb.
            "Built the bidirectional data-sync layer with Firestore.",
        ]
        n_calls = {"i": 0}
        def stub(prompt, **kw):
            i = n_calls["i"]
            n_calls["i"] += 1
            return outputs[min(i, len(outputs) - 1)]
        with patch("resumes.services.resume_generator_v2._llm_call",
                   side_effect=stub):
            block = _generate_entity_bullets(
                store, entity, section="experience",
                job_title="Engineer", events=[],
                writing_rules_block="",
                jd_must=[], jd_nice=[],
            )
        self.assertEqual(len(block.bullets), 2)
        self.assertTrue(
            block.bullets[1].text.lower().startswith("built"),
            f"regen should have changed the opener; got {block.bullets[1].text!r}",
        )
        # 3 LLM calls = 2 initial + 1 regen.
        self.assertEqual(n_calls["i"], 3)

    def test_regen_still_runs_number_lock_and_jd_skill_guards(self):
        """The collision-regen path runs through _generate_one_bullet,
        so the inline guards (number-lock + JD-skill) still apply on
        the regen attempt. A regen that fabricates a number triggers
        the inline number-lock regen."""
        from resumes.services.resume_generator_v2 import (
            _generate_entity_bullets,
        )
        store, entity = self._build_entity_with_two_children()
        outputs = [
            "Designed the auth flow with Firebase Auth.",
            # bullet[1] first attempt: same opener — triggers collision regen.
            "Designed a data-sync layer with bidirectional updates.",
            # Collision regen, first inline attempt — fabricates "99 hours"
            # which isn't in the facts → triggers inline number-lock regen.
            "Built the sync layer saving 99 hours weekly.",
            # Inline number-lock regen — drops the bad number.
            "Built the sync layer with Firestore-backed updates.",
        ]
        n_calls = {"i": 0}
        def stub(prompt, **kw):
            i = n_calls["i"]
            n_calls["i"] += 1
            return outputs[min(i, len(outputs) - 1)]
        events: list = []
        with patch("resumes.services.resume_generator_v2._llm_call",
                   side_effect=stub):
            block = _generate_entity_bullets(
                store, entity, section="experience",
                job_title="Engineer", events=events,
                writing_rules_block="",
                jd_must=[], jd_nice=[],
            )
        # Two bullets emitted, both content-clean.
        self.assertEqual(len(block.bullets), 2)
        self.assertNotIn("99", block.bullets[1].text)
        # The inline number-lock regenerated event was recorded —
        # proving the guard ran on the regen path.
        from resumes.services.resume_generator_v2 import FabricationEvent
        regen_events = [e for e in events
                        if e.action == "regenerated" and e.ungrounded_numbers]
        self.assertEqual(len(regen_events), 1)
        self.assertEqual(regen_events[0].ungrounded_numbers, [99.0])

    def test_persistent_collision_keeps_bullet_never_drops(self):
        """If the collision regen ALSO collides, the (regenerated) bullet
        is kept rather than dropped. Bound = one regen per bullet."""
        from resumes.services.resume_generator_v2 import (
            _generate_entity_bullets,
        )
        store, entity = self._build_entity_with_two_children()
        outputs = [
            "Designed the auth flow with Firebase Auth.",
            "Designed a data-sync layer.",
            # Regen also starts with Designed (still collides — but we
            # are bounded to one regen; the bullet is KEPT regardless).
            "Designed the bidirectional data-sync layer.",
            "Designed unreachable fourth output",  # would fire on a 2nd regen
        ]
        n_calls = {"i": 0}
        def stub(prompt, **kw):
            i = n_calls["i"]
            n_calls["i"] += 1
            return outputs[min(i, len(outputs) - 1)]
        with patch("resumes.services.resume_generator_v2._llm_call",
                   side_effect=stub):
            block = _generate_entity_bullets(
                store, entity, section="experience",
                job_title="Engineer", events=[],
                writing_rules_block="",
                jd_must=[], jd_nice=[],
            )
        # Both bullets KEPT despite persistent collision.
        self.assertEqual(len(block.bullets), 2)
        self.assertTrue(block.bullets[1].text.lower().startswith("designed"))
        # Exactly 3 LLM calls — 2 initial + 1 regen attempt. Never 4.
        self.assertEqual(n_calls["i"], 3)

    def test_persistent_guard_failure_on_regen_keeps_original(self):
        """If the collision-regen returns None because a guard
        persistently failed on the regen attempt, the ORIGINAL colliding
        bullet remains. Never drop a grounded bullet over an opener."""
        from resumes.services.resume_generator_v2 import (
            _generate_entity_bullets,
        )
        store, entity = self._build_entity_with_two_children()
        # First two: succeed. Then regen for bullet[1] tries twice with
        # fabricated numbers (allowed_numbers is empty since no metric
        # facts), so the inline number-lock drops the regen → returns None.
        outputs = [
            "Designed the auth flow.",
            "Designed a sync layer.",
            "Built the sync layer saving 99 hours.",   # collision-regen attempt 1
            "Built the sync layer saving 99 hours per week.",  # inline regen
            "Built unreachable fourth output",
        ]
        n_calls = {"i": 0}
        def stub(prompt, **kw):
            i = n_calls["i"]
            n_calls["i"] += 1
            return outputs[min(i, len(outputs) - 1)]
        with patch("resumes.services.resume_generator_v2._llm_call",
                   side_effect=stub):
            block = _generate_entity_bullets(
                store, entity, section="experience",
                job_title="Engineer", events=[],
                writing_rules_block="",
                jd_must=[], jd_nice=[],
            )
        # Both bullets KEPT — bullet[1] is the ORIGINAL collider since
        # the regen returned None.
        self.assertEqual(len(block.bullets), 2)
        self.assertEqual(
            block.bullets[1].text,
            "Designed a sync layer.",
            "regen returned None → original colliding bullet must be kept",
        )

    def test_n_equals_one_entity_is_noop(self):
        """A single-bullet entity has no siblings and cannot collide —
        zero regens fire, exactly one LLM call."""
        from resumes.services.resume_generator_v2 import (
            _generate_entity_bullets,
        )
        from resumes.services.resume_planner_v2 import (
            EntityAllocation, FactAllocation,
        )
        store = FactStore()
        store.add(_fact(
            id="role", type_=FactType.ROLE,
            claim="Engineer at Acme",
            evidence="Engineer at Acme",
            entity_id="cv:role|acme|eng",
            entity_display="Engineer @ Acme",
        ))
        store.add(_fact(
            id="ach1", type_=FactType.ACHIEVEMENT,
            claim="Built the user authentication flow",
            evidence="Built the user authentication flow",
            entity_id="cv:role|acme|eng",
        ))
        entity = EntityAllocation(
            entity_id="cv:role|acme|eng",
            entity_display="Engineer @ Acme",
            anchor_fact_id="role",
            facts=[FactAllocation(fact_id="ach1")],
        )
        captured_prompts: list[str] = []
        def stub(prompt, **kw):
            captured_prompts.append(prompt)
            return "Built the user authentication flow."
        with patch("resumes.services.resume_generator_v2._llm_call",
                   side_effect=stub):
            block = _generate_entity_bullets(
                store, entity, section="experience",
                job_title="Engineer", events=[],
                writing_rules_block="",
                jd_must=[], jd_nice=[],
            )
        self.assertEqual(len(block.bullets), 1)
        # Exactly one LLM call — no regen path fired.
        self.assertEqual(len(captured_prompts), 1)
        # And the single call had NO siblings block (bullet #1 has no
        # prior siblings to diversify against).
        self.assertNotIn("=== SIBLINGS", captured_prompts[0])

    def test_bullet_1_has_no_siblings_block(self):
        """Verifies bullet #1's prompt has no SIBLINGS block; bullet #2's
        does. Direct prompt-content assertion."""
        from resumes.services.resume_generator_v2 import (
            _generate_entity_bullets,
        )
        store, entity = self._build_entity_with_two_children()
        captured: list[str] = []
        # Use non-colliding openers so we get exactly 2 LLM calls
        # (no collision regen).
        outputs = ["Built the auth flow.", "Designed the sync layer."]
        n_calls = {"i": 0}
        def stub(prompt, **kw):
            captured.append(prompt)
            i = n_calls["i"]
            n_calls["i"] += 1
            return outputs[min(i, len(outputs) - 1)]
        with patch("resumes.services.resume_generator_v2._llm_call",
                   side_effect=stub):
            _generate_entity_bullets(
                store, entity, section="experience",
                job_title="Engineer", events=[],
                writing_rules_block="",
                jd_must=[], jd_nice=[],
            )
        self.assertEqual(len(captured), 2)
        # Bullet #1 — no siblings yet.
        self.assertNotIn("=== SIBLINGS", captured[0])
        # Bullet #2 — sees bullet #1 as a sibling.
        self.assertIn("=== SIBLINGS", captured[1])
        self.assertIn("Built the auth flow.", captured[1])
