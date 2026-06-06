"""Unit tests for tier-aware proximity-enriched gap analysis.

Covers:
  - Score formula edge cases (all matched, all missing, mixed, all-0 vs all-0.8
    proximity, tier-empty)
  - match_band thresholds
  - avg_proximity edge cases (None on empty, mean math on populated)
  - Pydantic rejection of proximity == 1.0
  - Phase 2 reconciliation defaults to proximity=0.0 with the correct reason
  - Reconciliation cross-tier dedupe (matched wins)
  - LLM is mocked — no real Groq calls
"""
from __future__ import annotations

import types
from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic import ValidationError

from analysis.services.gap_analyzer import _reconcile_tier, compute_gap_analysis
from analysis.services.skill_score import (
    avg_proximity,
    compute_match_score,
    match_band,
)
from profiles.services.schemas import (
    MatchedSkill,
    MissingSkill,
    TieredGapAnalysisResult,
)


# ---------- Pydantic constraints ----------

class TestStringCoercion(SimpleTestCase):
    """The LLM sometimes returns null for evidence_source / evidence_quote
    (when no specific source applies) and sometimes returns over-length
    quotes. Both used to crash the Groq tool-call validator. Schema now
    coerces null → "" and truncates to 140 chars in Pydantic."""

    def test_matched_skill_accepts_null_evidence_fields(self):
        m = MatchedSkill(name="R", evidence_source=None, evidence_quote=None)
        self.assertEqual(m.evidence_source, "")
        self.assertEqual(m.evidence_quote, "")

    def test_matched_skill_truncates_long_quote(self):
        long_quote = "x" * 200
        m = MatchedSkill(name="Y", evidence_quote=long_quote)
        self.assertEqual(len(m.evidence_quote), 140)

    def test_missing_skill_accepts_null_source_quote(self):
        m = MissingSkill(name="Z", source_quote=None, proximity_reason=None, proximity=0.2)
        self.assertEqual(m.source_quote, "")
        self.assertEqual(m.proximity_reason, "")

    def test_missing_skill_truncates_long_reason(self):
        long = "y" * 200
        m = MissingSkill(name="W", proximity_reason=long, proximity=0.5)
        self.assertEqual(len(m.proximity_reason), 140)

    def test_bridge_hint_null_stays_null(self):
        m = MissingSkill(name="A", bridge_hint=None, proximity=0.3)
        self.assertIsNone(m.bridge_hint)

    def test_bridge_hint_empty_string_becomes_null(self):
        m = MissingSkill(name="A", bridge_hint="", proximity=0.3)
        self.assertIsNone(m.bridge_hint)


class TestProximityValidator(SimpleTestCase):
    def test_proximity_exactly_one_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            MissingSkill(name="Kubernetes", proximity=1.0)
        self.assertIn("less than 1", str(ctx.exception).lower())

    def test_proximity_above_one_rejected(self):
        with self.assertRaises(ValidationError):
            MissingSkill(name="X", proximity=1.5)

    def test_proximity_negative_rejected(self):
        with self.assertRaises(ValidationError):
            MissingSkill(name="X", proximity=-0.1)

    def test_proximity_zero_accepted(self):
        m = MissingSkill(name="X", proximity=0.0, proximity_reason="No evidence")
        self.assertEqual(m.proximity, 0.0)

    def test_proximity_anchor_values_accepted(self):
        for v in (0.0, 0.2, 0.4, 0.6, 0.8, 0.99):
            MissingSkill(name="X", proximity=v)


# ---------- Score formula ----------

class TestComputeMatchScore(SimpleTestCase):
    def test_all_matched_max(self):
        mm = [MatchedSkill(name=f"S{i}") for i in range(8)]
        mn = [MatchedSkill(name=f"N{i}") for i in range(4)]
        score = compute_match_score(mm, [], mn, [])
        # Base 0.05 + 0.75 * 1.0 + 0.20 * 1.0 = 1.0
        self.assertEqual(score, 1.0)

    def test_all_missing_zero_proximity_floor(self):
        miss_m = [MissingSkill(name=f"S{i}", proximity=0.0) for i in range(5)]
        miss_n = [MissingSkill(name=f"N{i}", proximity=0.0) for i in range(3)]
        score = compute_match_score([], miss_m, [], miss_n)
        # Base 0.05 + 0.75 * 0 + 0.20 * 0 = 0.05
        self.assertEqual(score, 0.05)

    def test_all_missing_high_proximity_partial_credit(self):
        miss_m = [MissingSkill(name=f"S{i}", proximity=0.8) for i in range(5)]
        miss_n = [MissingSkill(name=f"N{i}", proximity=0.8) for i in range(3)]
        score = compute_match_score([], miss_m, [], miss_n)
        # must_ratio = (0 + 5 * 0.4) / 5 = 0.4
        # nice_ratio = (0 + 3 * 0.4) / 3 = 0.4
        # score      = 0.05 + 0.75*0.4 + 0.20*0.4 = 0.05 + 0.30 + 0.08 = 0.43
        self.assertEqual(score, 0.43)

    def test_proximity_cap_at_half(self):
        # A proximity-0.8 missing skill contributes 0.4 of a match, never 0.8.
        miss = [MissingSkill(name="X", proximity=0.8)]
        matched = [MatchedSkill(name="A")]
        score = compute_match_score(matched, miss, [], [])
        # must_ratio = (1 + 0.4) / 2 = 0.70
        # No nice tier -> renormalized must weight = 0.95 (no free nice credit).
        # score = 0.05 + 0.95*0.70 = 0.715
        self.assertEqual(score, 0.715)

    def test_lseg_screenshot_shape(self):
        # Mirror the spec's LSEG expectation: 8 matched_must / 0 missing_must
        # OR 1 with proximity 0.4, plus ~4 matched_nice and ~5 missing_nice.
        mm = [MatchedSkill(name=f"S{i}") for i in range(8)]
        mn = [MatchedSkill(name=f"N{i}") for i in range(4)]
        miss_n = [
            MissingSkill(name="NoSQL", proximity=0.5),
            MissingSkill(name="NLP", proximity=0.3),
            MissingSkill(name="LLMs", proximity=0.3),
            MissingSkill(name="Agentic", proximity=0.2),
            MissingSkill(name="Vector DBs", proximity=0.3),
        ]
        score = compute_match_score(mm, [], mn, miss_n)
        # must_ratio = 1.0; nice_ratio = (4 + 0.5 * sum_prox) / 9
        # sum_prox = 0.5+0.3+0.3+0.2+0.3 = 1.6; +0.8 partial = 0.8 added
        # nice_ratio = (4 + 0.8) / 9 ≈ 0.5333
        # score ≈ 0.05 + 0.75*1.0 + 0.20*0.5333 ≈ 0.05 + 0.75 + 0.1067 ≈ 0.9067
        self.assertGreater(score, 0.85)
        self.assertLess(score, 0.95)
        self.assertEqual(match_band(score), "strong")

    def test_empty_tier_treated_as_satisfied(self):
        # Job with only must-haves declared: nice tier ratio = 1.0
        mm = [MatchedSkill(name="A"), MatchedSkill(name="B")]
        score = compute_match_score(mm, [], [], [])
        self.assertEqual(score, 1.0)

    def test_all_buckets_empty_returns_baseline(self):
        # Edge: no JD skills at all -> empty contract, no tier to satisfy -> BASE.
        self.assertEqual(compute_match_score([], [], [], []), 0.05)


class TestMatchBand(SimpleTestCase):
    def test_thresholds(self):
        self.assertEqual(match_band(0.92), "strong")
        self.assertEqual(match_band(0.85), "strong")
        self.assertEqual(match_band(0.84), "solid")
        self.assertEqual(match_band(0.70), "solid")
        self.assertEqual(match_band(0.69), "partial")
        self.assertEqual(match_band(0.55), "partial")
        self.assertEqual(match_band(0.54), "weak")
        self.assertEqual(match_band(0.0), "weak")


class TestAvgProximity(SimpleTestCase):
    def test_none_when_empty(self):
        self.assertIsNone(avg_proximity([], []))

    def test_mean_across_tiers(self):
        miss_m = [MissingSkill(name="A", proximity=0.4), MissingSkill(name="B", proximity=0.6)]
        miss_n = [MissingSkill(name="C", proximity=0.8)]
        # mean of [0.4, 0.6, 0.8] = 0.6
        self.assertEqual(avg_proximity(miss_m, miss_n), 0.6)

    def test_handles_legacy_strings(self):
        # A legacy entry that's just a string (no proximity attr) counts as 0.
        self.assertEqual(avg_proximity(["Python", "React"], []), 0.0)


# ---------- Phase 2 reconciliation ----------

class TestEvidencelessDemotion(SimpleTestCase):
    """An LLM-produced matched_* entry with empty evidence_quote should be
    demoted to missing_* with proximity 0.0 — see Pharco regression."""

    def _run_demote(self, result):
        from analysis.services.gap_analyzer import _demote_evidenceless_matches
        return _demote_evidenceless_matches(result)

    def test_empty_evidence_quote_demotes(self):
        result = TieredGapAnalysisResult(
            matched_must_have=[
                MatchedSkill(name="Python", evidence_quote="Python in 3 projects"),
                MatchedSkill(name="Hadoop", evidence_quote=""),
            ],
        )
        out = self._run_demote(result)
        self.assertEqual([m.name for m in out.matched_must_have], ["Python"])
        self.assertEqual([m.name for m in out.missing_must_have], ["Hadoop"])
        self.assertEqual(out.missing_must_have[0].proximity, 0.0)
        self.assertIn("without specific evidence", out.missing_must_have[0].proximity_reason)

    def test_whitespace_only_evidence_demotes(self):
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="R", evidence_quote="   ")],
        )
        out = self._run_demote(result)
        self.assertEqual(out.matched_must_have, [])
        self.assertEqual([m.name for m in out.missing_must_have], ["R"])

    def test_evidence_present_stays_matched(self):
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="Python", evidence_quote="In skills list")],
        )
        out = self._run_demote(result)
        self.assertEqual([m.name for m in out.matched_must_have], ["Python"])
        self.assertEqual(out.missing_must_have, [])

    def test_demote_skips_when_skill_already_in_missing(self):
        # Pharco regression: LLM put Hadoop in BOTH matched (no evidence) and
        # missing (proximity 0.2). We should drop the matched entry without
        # adding a duplicate 0.0 to missing.
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="Hadoop", evidence_quote="")],
            missing_must_have=[MissingSkill(name="Hadoop", proximity=0.2,
                                            proximity_reason="Some PySpark exposure")],
        )
        out = self._run_demote(result)
        self.assertEqual(out.matched_must_have, [])
        self.assertEqual(len(out.missing_must_have), 1)
        self.assertEqual(out.missing_must_have[0].proximity, 0.2)
        self.assertIn("PySpark", out.missing_must_have[0].proximity_reason)

    def test_demote_works_for_nice_tier_too(self):
        result = TieredGapAnalysisResult(
            matched_nice_to_have=[MatchedSkill(name="MLflow", evidence_quote="")],
        )
        out = self._run_demote(result)
        self.assertEqual(out.matched_nice_to_have, [])
        self.assertEqual([m.name for m in out.missing_nice_to_have], ["MLflow"])


class TestProfileGroundingValidator(SimpleTestCase):
    """Fix (b-grounding): a non-empty evidence_quote is necessary but NOT
    sufficient — a matched skill must also be GROUNDED in the profile, or it
    is demoted. Targets the LLM's holistic/adjacency over-claims. Must NOT
    re-break PR 3e (tech-array / declared / prose-evidenced skills stay
    matched)."""

    # taher-shaped profile: Firebase + RESTful APIs + Flutter + SQLite are
    # real (tech array / skills / bullet); the 6 phantoms appear NOWHERE.
    PROFILE = {
        "professional_summary": "Junior Flutter developer skilled in clean "
                                "architecture and state management with BLoC.",
        "projects": [
            {
                "name": "Brain Tumor Classifier",
                "technologies": ["Flutter", "Firebase", "RESTful APIs", "SQLite"],
                "description": [
                    "Implemented authentication using Firebase Auth with persistent sessions.",
                    "Streamed downloadable audio files via Firestore.",
                ],
            },
        ],
        "experiences": [
            {"title": "Mobile Dev",
             "description": ["Built apps with Flutter and Dart, optimizing performance."]},
        ],
        "certifications": [{"name": "Flutter Essential Training"}],
        "skills": [{"name": "Flutter"}, {"name": "Firebase"},
                   {"name": "RESTful APIs"}, {"name": "SQLite"}],
    }

    def _demote(self, result, profile=None):
        from analysis.services.gap_analyzer import _demote_evidenceless_matches
        return _demote_evidenceless_matches(result, profile_data=profile)

    def test_six_phantom_skills_with_quotes_are_demoted(self):
        # Each has a plausible NON-EMPTY quote (legacy check would KEEP them)
        # but no profile grounding anywhere.
        phantom_must = ["Firebase Messaging", "GoRouter", "Dio",
                        "multi-role mobile applications"]
        phantom_nice = ["analytics", "Arabic/English apps"]
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name=n, evidence_quote=f"LLM claim re {n}")
                               for n in phantom_must],
            matched_nice_to_have=[MatchedSkill(name=n, evidence_quote=f"LLM claim re {n}")
                                  for n in phantom_nice],
        )
        out = self._demote(result, self.PROFILE)
        self.assertEqual(out.matched_must_have, [])
        self.assertEqual(out.matched_nice_to_have, [])
        self.assertEqual({m.name for m in out.missing_must_have}, set(phantom_must))
        self.assertEqual({m.name for m in out.missing_nice_to_have}, set(phantom_nice))
        for m in out.missing_must_have + out.missing_nice_to_have:
            self.assertEqual(m.proximity, 0.0)
            self.assertIn("grounding", m.proximity_reason)

    def test_pr3e_tech_array_skill_stays_matched(self):
        # PR-3e SAFETY: skills present in a project's technologies array MUST
        # survive the grounding validator — this is the regression to prevent.
        result = TieredGapAnalysisResult(
            matched_must_have=[
                MatchedSkill(name="RESTful APIs", evidence_quote="x"),
                MatchedSkill(name="Flutter", evidence_quote="x"),
                MatchedSkill(name="SQLite", evidence_quote="x"),
            ],
        )
        out = self._demote(result, self.PROFILE)
        self.assertEqual({m.name for m in out.matched_must_have},
                         {"RESTful APIs", "Flutter", "SQLite"})
        self.assertEqual(out.missing_must_have, [])

    def test_prose_only_skill_stays_matched(self):
        # "clean architecture" only in the summary; "authentication" only in a
        # project bullet. Whole-phrase prose grounding must keep both matched.
        result = TieredGapAnalysisResult(
            matched_must_have=[
                MatchedSkill(name="clean architecture", evidence_quote="x"),
                MatchedSkill(name="authentication", evidence_quote="x"),
            ],
        )
        out = self._demote(result, self.PROFILE)
        self.assertEqual({m.name for m in out.matched_must_have},
                         {"clean architecture", "authentication"})
        self.assertEqual(out.missing_must_have, [])

    def test_shared_token_does_not_ground_phantom(self):
        # "Firebase" (real) stays; "Firebase Messaging" (whole phrase absent)
        # demotes — a shared "Firebase" token must NOT rescue the phantom.
        result = TieredGapAnalysisResult(
            matched_must_have=[
                MatchedSkill(name="Firebase", evidence_quote="x"),
                MatchedSkill(name="Firebase Messaging", evidence_quote="x"),
            ],
        )
        out = self._demote(result, self.PROFILE)
        self.assertEqual([m.name for m in out.matched_must_have], ["Firebase"])
        self.assertEqual([m.name for m in out.missing_must_have], ["Firebase Messaging"])

    def test_dio_does_not_ground_off_audio_substring(self):
        # "Dio" must not ground off "audio files" in a bullet (substring trap).
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="Dio", evidence_quote="x")],
        )
        out = self._demote(result, self.PROFILE)
        self.assertEqual(out.matched_must_have, [])
        self.assertEqual([m.name for m in out.missing_must_have], ["Dio"])

    def test_grounding_is_opt_in_skipped_without_profile_data(self):
        # profile_data omitted (None) → grounding does NOT run; an ungrounded
        # but quoted skill stays matched (legacy behaviour preserved).
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="GoRouter", evidence_quote="LLM claim")],
        )
        out = self._demote(result, None)
        self.assertEqual([m.name for m in out.matched_must_have], ["GoRouter"])
        self.assertEqual(out.missing_must_have, [])

    def test_empty_quote_still_demotes_even_when_grounded(self):
        # Gate 1 (empty quote) still fires independently: a grounded skill with
        # an EMPTY quote is demoted on the legacy rule.
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="Flutter", evidence_quote="")],
        )
        out = self._demote(result, self.PROFILE)
        self.assertEqual(out.matched_must_have, [])
        self.assertEqual([m.name for m in out.missing_must_have], ["Flutter"])

    def test_rest_variant_is_re_grounded(self):
        # Fix B: the JD token "REST API integration" is grounded by the
        # candidate's variant-named REST skill ("RESTful APIs" in skills[] AND
        # the project tech array) via the shared canonical matcher. Exact-match
        # previously demoted it (restapiintegration != restfulapis) — the
        # category-II false demotion this fix repairs.
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="REST API integration", evidence_quote="x")],
        )
        out = self._demote(result, self.PROFILE)
        self.assertEqual([m.name for m in out.matched_must_have], ["REST API integration"])
        self.assertEqual(out.missing_must_have, [])


class TestSkillsMatch(SimpleTestCase):
    """Fix B shared matcher: variant spellings of a real skill match; phantoms
    and shared-token near-misses do not. Used identically by the grounding
    validator and the planner's JD-relevance."""

    def _m(self, a, b):
        from jobs.services.skill_extractor import skills_match
        return skills_match(a, b)

    def test_rest_variants_all_match_via_canonical(self):
        # The short variants ("REST APIs" 0.552, "RESTful APIs" 0.500) that
        # difflib alone misses now match via the alias table + trailing-noun
        # strip — both sides canonicalize to "REST API".
        for variant in ["RESTful API Integration", "REST APIs", "RESTful APIs"]:
            self.assertTrue(self._m("REST API integration", variant), variant)
        self.assertTrue(self._m("REST APIs", "RESTful APIs"))  # both -> REST API

    def test_phantoms_do_not_match_nearest_token(self):
        # (phantom, candidate's nearest real token) — all canonical-distinct
        # AND below the 0.85 difflib fallback.
        pairs = [
            ("GoRouter", "Flutter"), ("GoRouter", "GetX (State Management)"),
            ("Dio", "just_audio"),
            ("Firebase Messaging", "Firebase"),
            ("multi-role mobile applications", "GitHub Actions"),
            ("analytics", "Data Analysis"),
            ("Token handling", "Testing"),
            ("Arabic/English apps", "REST APIs"),
        ]
        for a, b in pairs:
            self.assertFalse(self._m(a, b), "%r should NOT match %r" % (a, b))

    def test_substring_is_not_a_match(self):
        # ratio() is substring-safe: 0.615 < 0.85, distinct canonical.
        self.assertFalse(self._m("Firebase Messaging", "Firebase"))

    def test_exact_and_typo_fallback(self):
        self.assertTrue(self._m("Flutter", "Flutter"))
        self.assertTrue(self._m("PostgreSQL", "postgres"))  # alias map

    def test_empty_inputs(self):
        self.assertFalse(self._m("", "Flutter"))
        self.assertFalse(self._m("Flutter", ""))


class TestReconciliation(SimpleTestCase):

    def _empty_result(self):
        return TieredGapAnalysisResult()

    def test_reconciliation_adds_missing_must_with_default_proximity(self):
        result = self._empty_result()
        out = _reconcile_tier(result, must_skills=["Python", "Docker"], nice_skills=[])
        self.assertEqual(len(out.missing_must_have), 2)
        for m in out.missing_must_have:
            self.assertEqual(m.proximity, 0.0)
            self.assertEqual(m.proximity_reason, "No related evidence found in profile")
            self.assertIsNone(m.bridge_hint)

    def test_reconciliation_skips_matched(self):
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="Python", evidence_quote="Python in 3 projects")],
        )
        out = _reconcile_tier(result, must_skills=["Python", "Docker"], nice_skills=[])
        names = [m.name for m in out.missing_must_have]
        self.assertNotIn("Python", names)
        self.assertIn("Docker", names)

    def test_reconciliation_fuzzy_match_against_matched(self):
        # LLM matched "Postgres" but JD has "PostgreSQL" — fuzzy at 0.85 catches it.
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="PostgreSQL")],
        )
        out = _reconcile_tier(result, must_skills=["postgresql"], nice_skills=[])
        # Should not add "postgresql" as missing.
        self.assertEqual(out.missing_must_have, [])

    def test_cross_tier_dedupe_matched_wins(self):
        result = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="Python")],
            missing_must_have=[MissingSkill(name="Python", proximity=0.5)],
        )
        out = _reconcile_tier(result, must_skills=["Python"], nice_skills=[])
        self.assertEqual([m.name for m in out.matched_must_have], ["Python"])
        self.assertEqual(out.missing_must_have, [])

    def test_nice_tier_reconciled_separately(self):
        out = _reconcile_tier(
            self._empty_result(),
            must_skills=[],
            nice_skills=["MLOps", "Kubernetes"],
        )
        self.assertEqual(len(out.missing_nice_to_have), 2)
        self.assertEqual(out.missing_must_have, [])


# ---------- Mocked end-to-end gap analyzer ----------

class TestComputeGapAnalysisIntegration(SimpleTestCase):

    def _make_profile_and_job(self, must=None, nice=None):
        # Build duck-typed profile/job that compute_gap_analysis accepts.
        profile = types.SimpleNamespace(
            skills=[{'name': 'Python'}, {'name': 'Pandas'}, {'name': 'PyTorch'}],
            experiences=[],
            education=[],
            projects=[],
            certifications=[],
            # data_content must mirror the declared skills: in production
            # profile.skills is a property over data_content, and the
            # grounding validator reads data_content. An empty data_content
            # with populated .skills is an unrealistic stub.
            data_content={
                'skills': [{'name': 'Python'}, {'name': 'Pandas'}, {'name': 'PyTorch'}],
                'projects': [],
                'experiences': [],
                'certifications': [],
            },
        )
        job = types.SimpleNamespace(
            title="Data Scientist",
            company="Acme",
            description="JD body",
            domain="Financial Services",
            extracted_skills=list(must or []) + list(nice or []),
            extracted_skills_tiers={
                'must_have': list(must or []),
                'nice_to_have': list(nice or []),
            },
        )
        return profile, job

    def test_proximity_rubric_anchors_round_trip(self):
        """LLM mock returns specific anchor values; pipeline preserves them."""
        profile, job = self._make_profile_and_job(
            must=["TensorFlow", "Kubernetes", "Python"],
            nice=["MLflow"],
        )
        llm_out = TieredGapAnalysisResult(
            matched_must_have=[MatchedSkill(name="Python", evidence_source="skills", evidence_quote="Python in skills list")],
            matched_nice_to_have=[],
            missing_must_have=[
                MissingSkill(name="TensorFlow", source_quote="Exp w/ TF", proximity=0.4,
                             proximity_reason="PyTorch transfers", bridge_hint="1-2 weeks to port"),
                MissingSkill(name="Kubernetes", source_quote="K8s required", proximity=0.2,
                             proximity_reason="No container evidence", bridge_hint=None),
            ],
            missing_nice_to_have=[
                MissingSkill(name="MLflow", proximity=0.6, proximity_reason="Familiar with model tracking concepts"),
            ],
        )

        class _FakeLLM:
            def invoke(self, _): return llm_out

        with patch("analysis.services.gap_analyzer.get_structured_llm", return_value=_FakeLLM()):
            out = compute_gap_analysis(profile, job)

        self.assertEqual(out['analysis_method'], 'llm_v2')
        names_must = [m['name'] for m in out['missing_must_have']]
        self.assertIn("TensorFlow", names_must)
        self.assertIn("Kubernetes", names_must)
        # Proximity preserved
        tf = next(m for m in out['missing_must_have'] if m['name'] == "TensorFlow")
        self.assertEqual(tf['proximity'], 0.4)
        self.assertEqual(tf['bridge_hint'], "1-2 weeks to port")
        # avg_proximity = mean([0.4, 0.2, 0.6]) = 0.4
        self.assertEqual(out['avg_proximity'], 0.4)
        # Score = base + 0.75 * must_ratio + 0.20 * nice_ratio
        # must_ratio = (1 + 0.5*(0.4+0.2)) / 3 = 1.3/3 ≈ 0.4333
        # nice_ratio = (0 + 0.5*0.6) / 1 = 0.3
        # score ≈ 0.05 + 0.75*0.4333 + 0.20*0.3 ≈ 0.05 + 0.325 + 0.06 ≈ 0.435
        self.assertAlmostEqual(out['similarity_score'], 0.435, places=2)
        self.assertEqual(out['match_band'], 'weak')

    def test_no_job_skills_short_circuits(self):
        profile, job = self._make_profile_and_job(must=[], nice=[])
        out = compute_gap_analysis(profile, job)
        self.assertEqual(out['analysis_method'], 'no_job_skills')
        self.assertEqual(out['matched_must_have'], [])

    def test_empty_profile_returns_all_missing_with_zero_proximity(self):
        empty = types.SimpleNamespace(skills=[], experiences=[], education=[],
                                      projects=[], certifications=[], data_content={})
        job = types.SimpleNamespace(
            title="X", company="Y", description="Z", domain="",
            extracted_skills=["Python"],
            extracted_skills_tiers={'must_have': ["Python"], 'nice_to_have': []},
        )
        out = compute_gap_analysis(empty, job)
        self.assertEqual(out['analysis_method'], 'empty_profile')
        self.assertEqual(len(out['missing_must_have']), 1)
        self.assertEqual(out['missing_must_have'][0]['proximity'], 0.0)
        self.assertEqual(out['missing_must_have'][0]['proximity_reason'],
                         "No related evidence found in profile")
