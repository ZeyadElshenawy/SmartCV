"""Tests for resumes.services.resume_reviewer_v2.

Mocked LLM throughout — no Groq. The load-bearing tests are:

  - regen routes through ``_generate_one_bullet`` (the GUARDED v2
    generator), NEVER through ``regenerate_section`` (the v1 path
    without a number-lock).
  - the INTEGRITY test: a regen whose mocked LLM tries to introduce
    a number outside the bullet's allocated facts → still dropped by
    the unchanged number-lock.
  - findings_classifier runs unchanged (unknown-source fail-safe →
    NEEDS_USER_INPUT).
  - cap-exhaust demotes unresolved AUTO_FIX to ADVISORY; no infinite
    loop.
"""
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase

from resumes.services.fact_store import (
    FactRecord,
    FactStore,
    FactType,
    SourceReliability,
)
from resumes.services.resume_generator_v2 import (
    GeneratedResumeV2,
    GeneratedSection,
    EntityBlock,
    GeneratedBullet,
    FabricationEvent,
)
from resumes.services.resume_planner_v2 import build_plan
from resumes.services import resume_reviewer_v2 as rv
from resumes.services.findings_classifier import (
    BUCKET_ADVISORY,
    BUCKET_AUTO_FIX,
    BUCKET_USER_INPUT,
    classify_finding,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fact(
    *, id, type_, claim, evidence, entity_id="", entity_display="",
    source="structured_profile",
    reliability=SourceReliability.USER_ORIGINAL,
    value=None, unit=None, hedged=False,
):
    return FactRecord(
        id=id, type=type_, claim=claim, evidence_quote=evidence,
        entity_id=entity_id, entity_display=entity_display,
        source=source, source_reliability=reliability,
        value=value, unit=unit, hedged=hedged,
    )


def _store_with(*facts) -> FactStore:
    s = FactStore()
    for f in facts:
        s.add(f)
    return s


def _make_resume_v2_with_one_bullet(text: str, *, fact_ids: list[str]):
    """Build a minimal GeneratedResumeV2 with one experience entity
    containing ONE bullet."""
    role = _fact(
        id="role1", type_=FactType.ROLE,
        claim="Engineer at OrgCo",
        evidence="Engineer @ OrgCo — 2024",
        entity_id="cv:role|orgco|engineer",
        entity_display="Engineer @ OrgCo",
    )
    bullet_fact = _fact(
        id="b1", type_=FactType.ACHIEVEMENT,
        claim="Shipped X.",
        evidence="Shipped X.",
        entity_id=role.entity_id, entity_display=role.entity_display,
    )
    store = _store_with(role, bullet_fact)
    bullet = GeneratedBullet(text=text, fact_ids=fact_ids, hedged=False)
    entity = EntityBlock(
        entity_id=role.entity_id,
        entity_display=role.entity_display,
        anchor_fact_id=role.id,
        bullets=[bullet],
    )
    sections = {
        "experience": GeneratedSection(
            section="experience", entities=[entity],
        ),
    }
    resume = GeneratedResumeV2(
        sections=sections, fabrication_events=[],
    )
    plan = build_plan(store)
    return resume, store, plan


# ===========================================================================
# 1. Banned-opening triggers AUTO_FIX → regen through _generate_one_bullet.
# ===========================================================================


class BannedOpeningRegenTests(SimpleTestCase):

    def test_banned_opening_flagged_and_regenerated(self):
        """A bullet starting with 'Utilized' is flagged as AUTO_FIX
        and regenerated. The regen call goes through
        _generate_one_bullet; the resulting bullet no longer starts
        with the banned opening."""
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Utilized machine learning to do the thing.",
            fact_ids=["b1"],
        )

        # Mock the LLM that _generate_one_bullet calls. Return a
        # clean rewrite without any number.
        def _stub_llm(prompt, **_kw):
            return "Built and deployed the ML service that improved outcomes."

        with patch.object(rv, "_generate_one_bullet",
                          wraps=rv._generate_one_bullet) as spy_one_bullet, \
             patch(
                 "resumes.services.resume_generator_v2._llm_call",
                 side_effect=_stub_llm,
             ):
            new_resume, report = rv.review_and_regenerate(
                resume, store=store, plan=plan, max_rounds=1,
            )

        # The regen path went through _generate_one_bullet AT LEAST ONCE.
        self.assertGreaterEqual(spy_one_bullet.call_count, 1)
        # The new bullet doesn't start with the banned opening.
        new_text = new_resume.sections["experience"].entities[0].bullets[0].text
        self.assertFalse(
            new_text.lower().lstrip().startswith("utilized"),
            f"regen still starts with banned opening: {new_text!r}",
        )
        # Report records the resolution.
        self.assertEqual(len(report["resolved"]), 1)
        self.assertIn("banned opening", report["resolved"][0]["detail"].lower())


# ===========================================================================
# 2. THE INTEGRITY TEST — regen can't introduce a number outside facts.
# ===========================================================================


class RegenIntegrityNumberLockTests(SimpleTestCase):
    """Load-bearing. A flagged bullet's regen, if the mocked LLM tries
    to cite a number outside the bullet's allocated facts, must STILL
    be dropped by the unchanged number-lock — proves the review path
    didn't bypass v2's grounding guard."""

    def test_regen_with_ungrounded_number_is_dropped(self):
        # Original bullet has fact_ids=['b1']; b1's value=None, no
        # numbers in its claim. So allowed_numbers for regen = {}.
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Utilized something.",  # banned opening → triggers regen
            fact_ids=["b1"],
        )

        # The mocked LLM, regardless of prompt content (review or
        # number-guard regen feedback), keeps citing "47%". 47 is not
        # in any fact → both attempts fail the guard → bullet DROPPED.
        def _bad_llm(prompt, **_kw):
            return "Reduced query latency 47% by sharding the service."

        with patch(
            "resumes.services.resume_generator_v2._llm_call",
            side_effect=_bad_llm,
        ):
            new_resume, report = rv.review_and_regenerate(
                resume, store=store, plan=plan, max_rounds=1,
            )

        # Bullet was dropped — entity now has zero bullets.
        entity = new_resume.sections["experience"].entities[0]
        for b in entity.bullets:
            self.assertNotIn(
                "47", b.text,
                f"ungrounded number leaked into final bullet: {b.text!r}",
            )
        # Resolution log shows the drop.
        dropped = [r for r in report["resolved"]
                   if r.get("resolved_to") == "(dropped)"]
        self.assertGreater(
            len(dropped), 0,
            "expected at least one (dropped) resolution — number-lock "
            "rejected the regen's ungrounded number on BOTH attempts",
        )


# ===========================================================================
# 3. regenerate_section is NEVER called from the v2 review path.
# ===========================================================================


class RegenerateSectionForbiddenTests(SimpleTestCase):
    """Hard guard. v1's regenerate_section has NO number-lock; if any
    v2 review path called it, fabricated numbers would re-enter the
    resume. Mock it to raise — the test FAILS if anything touched it."""

    def test_regenerate_section_never_invoked(self):
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Utilized ML to ship the service.",
            fact_ids=["b1"],
        )

        def _stub_llm(prompt, **_kw):
            return "Built the ML service end-to-end."

        # Patch regenerate_section to raise if anyone calls it.
        with patch(
            "resumes.services.resume_generator.regenerate_section",
            side_effect=AssertionError(
                "regenerate_section MUST NOT be called from v2 review",
            ),
        ) as mock_regen_section, patch(
            "resumes.services.resume_generator_v2._llm_call",
            side_effect=_stub_llm,
        ):
            rv.review_and_regenerate(
                resume, store=store, plan=plan, max_rounds=1,
            )
        # Belt-and-braces: the mock was never invoked.
        mock_regen_section.assert_not_called()


# ===========================================================================
# 4. findings_classifier runs unchanged — unknown kind → NEEDS_USER_INPUT.
# ===========================================================================


class ClassifierUnchangedTests(SimpleTestCase):

    def test_unknown_source_falls_through_to_user_input(self):
        """Source-agnostic fail-safe still works. Reused as-is."""
        bucket = classify_finding("not_a_real_source", {"foo": "bar"})
        self.assertEqual(bucket, BUCKET_USER_INPUT)

    def test_unknown_supervisor_category_falls_through_to_user_input(self):
        bucket = classify_finding("supervisor", {
            "category": "future_category_we_dont_know_yet",
            "severity": "blocking",
            "layer": "content",
        })
        self.assertEqual(bucket, BUCKET_USER_INPUT)

    def test_unknown_bullet_rule_id_falls_through_to_user_input(self):
        bucket = classify_finding("bullet", {"rule_id": "Z99_invented_rule"})
        self.assertEqual(bucket, BUCKET_USER_INPUT)


# ===========================================================================
# 5. NEEDS_USER_INPUT findings BYPASS the regen loop.
# ===========================================================================


class UserInputBypassTests(SimpleTestCase):

    def test_unsupported_metric_finding_bypasses_loop(self):
        """A fabrication_event of action='dropped' produces an
        ``unsupported_metric`` grounding finding, which the classifier
        routes to USER_INPUT — must NOT enter the regen loop, must
        surface in the report's 'user_input' list."""
        # Build a resume with a CLEAN bullet (no banned opening) so
        # the ONLY finding is the grounding one from a fabrication
        # event we inject.
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Built and deployed the service.",
            fact_ids=["b1"],
        )
        # Inject a fabrication_event into the resume.
        resume = resume.model_copy(update={
            "fabrication_events": [
                FabricationEvent(
                    section="experience",
                    entity_id="cv:role|orgco|engineer",
                    bullet_text="Reduced latency 99%.",
                    ungrounded_numbers=[99.0],
                    action="dropped",
                ),
            ],
        })

        # If the loop tried to regen, it would call _generate_one_bullet.
        # Patch with a stub that records calls — assert 0 calls.
        def _llm_should_not_fire(*_a, **_kw):
            raise AssertionError(
                "regen path fired on a USER_INPUT finding — must bypass"
            )

        with patch(
            "resumes.services.resume_generator_v2._llm_call",
            side_effect=_llm_should_not_fire,
        ):
            new_resume, report = rv.review_and_regenerate(
                resume, store=store, plan=plan, max_rounds=1,
            )

        # The grounding finding surfaces to the user.
        user_input = report["user_input"]
        self.assertEqual(len(user_input), 1)
        self.assertEqual(user_input[0]["kind"], "unsupported_metric")
        # Nothing was regenerated.
        self.assertEqual(report["resolved"], [])
        # Bullet is untouched.
        self.assertEqual(
            new_resume.sections["experience"].entities[0].bullets[0].text,
            "Built and deployed the service.",
        )


# ===========================================================================
# 6. Cap exhaustion → demote to ADVISORY; loop terminates.
# ===========================================================================


class CapExhaustDemotionTests(SimpleTestCase):

    def test_max_rounds_zero_demotes_to_advisory(self):
        """max_rounds=0 means "review only, no regen rounds". A
        banned-opening AUTO_FIX finding still surfaces but gets
        demoted to advisory severity. Loop terminates without an
        LLM call."""
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Utilized the service to ship.",
            fact_ids=["b1"],
        )

        def _llm_should_not_fire(*_a, **_kw):
            raise AssertionError(
                "max_rounds=0 means no regen — LLM must not be called"
            )

        with patch(
            "resumes.services.resume_generator_v2._llm_call",
            side_effect=_llm_should_not_fire,
        ):
            new_resume, report = rv.review_and_regenerate(
                resume, store=store, plan=plan, max_rounds=0,
            )
        # Bullet text untouched.
        self.assertEqual(
            new_resume.sections["experience"].entities[0].bullets[0].text,
            "Utilized the service to ship.",
        )
        # Demoted set non-empty; severity dropped to 'warning'.
        self.assertEqual(len(report["demoted"]), 1)
        self.assertEqual(report["demoted"][0]["severity"], "warning")
        self.assertEqual(
            report["demoted"][0]["demoted_reason"], "review_cap_exhausted",
        )

    def test_loop_terminates_with_persistent_banned_opening(self):
        """An LLM that keeps re-introducing the banned opening across
        retries should still terminate at max_rounds and demote."""
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Utilized the service.",
            fact_ids=["b1"],
        )

        # Mocked LLM keeps emitting a banned opening — would loop
        # forever without the cap.
        def _stuck_llm(prompt, **_kw):
            return "Utilized the same service still."

        with patch(
            "resumes.services.resume_generator_v2._llm_call",
            side_effect=_stuck_llm,
        ):
            new_resume, report = rv.review_and_regenerate(
                resume, store=store, plan=plan, max_rounds=2,
            )
        # Terminated within ≤ 3 passes.
        self.assertLessEqual(report["rounds_run"], 3)
        # Either resolved (replaced) or demoted — never infinite.


# ===========================================================================
# 7. Validation-report shim shape — what findings_presenter consumes.
# ===========================================================================


class ValidationReportShimTests(SimpleTestCase):

    def test_v1_shaped_keys_present(self):
        """build_v2_validation_report emits the v1 keys
        findings_presenter expects. Lets the presenter consume v2
        output unchanged."""
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Built the service end-to-end.",
            fact_ids=["b1"],
        )
        vr = rv.build_v2_validation_report(resume)
        self.assertIn("findings", vr)
        self.assertIn("grounding_findings", vr)
        self.assertIn("regression_findings", vr)
        self.assertIn("supervisor_findings", vr)

    def test_bullet_findings_carry_v1_keys(self):
        """Each bullet finding has rule_id / severity / where / detail /
        fix — matches the v1 contract findings_classifier reads."""
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Utilized the service successfully.",
            fact_ids=["b1"],
        )
        vr = rv.build_v2_validation_report(resume)
        for f in vr["findings"]:
            for key in ("rule_id", "severity", "where", "detail", "fix"):
                self.assertIn(
                    key, f, f"finding missing v1-shape key {key!r}: {f!r}",
                )

    def test_grounding_findings_carry_v1_keys(self):
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Built the service.", fact_ids=["b1"],
        )
        resume = resume.model_copy(update={
            "fabrication_events": [
                FabricationEvent(
                    section="experience",
                    entity_id="cv:role|orgco|engineer",
                    bullet_text="A bullet with 99%.",
                    ungrounded_numbers=[99.0],
                    action="dropped",
                ),
            ],
        })
        vr = rv.build_v2_validation_report(resume)
        self.assertEqual(len(vr["grounding_findings"]), 1)
        for key in ("kind", "severity", "where", "detail"):
            self.assertIn(key, vr["grounding_findings"][0])


# ===========================================================================
# 8. Locator — bullet-finding ``where`` strings parse / fail-safe.
# ===========================================================================


class WhereLocatorTests(SimpleTestCase):

    def test_summary_where_parses(self):
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Built X.", fact_ids=["b1"],
        )
        # Manually add a summary section with one bullet.
        from resumes.services.resume_generator_v2 import GeneratedSection
        summary_bullet = GeneratedBullet(
            text="Built X.", fact_ids=["b1"], hedged=False,
        )
        resume = resume.model_copy(update={
            "sections": {
                **resume.sections,
                "summary": GeneratedSection(
                    section="summary",
                    summary_text="Built X.",
                    bullets=[summary_bullet],
                ),
            },
        })
        loc = rv._locate_bullet("summary[0]", resume)
        self.assertIsNotNone(loc)
        section_name, entity_idx, bullet_idx, entity = loc
        self.assertEqual(section_name, "summary")
        self.assertIsNone(entity_idx)
        self.assertEqual(bullet_idx, 0)

    def test_experience_where_parses(self):
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Built X.", fact_ids=["b1"],
        )
        loc = rv._locate_bullet(
            "experience/cv:role|orgco|engineer[0]", resume,
        )
        self.assertIsNotNone(loc)
        section_name, entity_idx, bullet_idx, entity = loc
        self.assertEqual(section_name, "experience")
        self.assertEqual(entity_idx, 0)
        self.assertEqual(bullet_idx, 0)

    def test_unknown_where_returns_none(self):
        resume, store, plan = _make_resume_v2_with_one_bullet(
            "Built X.", fact_ids=["b1"],
        )
        self.assertIsNone(rv._locate_bullet("", resume))
        self.assertIsNone(rv._locate_bullet("garbage", resume))
        self.assertIsNone(rv._locate_bullet("experience/missing[0]", resume))
        self.assertIsNone(
            rv._locate_bullet("experience/cv:role|orgco|engineer[99]", resume),
        )
