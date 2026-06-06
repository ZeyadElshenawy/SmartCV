"""Gap-score separation-collapse fix tests.

Two fixes:
  A. compute_match_score renormalizes weights over PRESENT tiers (no more
     0.25 floor from an absent nice-tier defaulting to ratio 1.0).
  B. _skill_is_grounded grounds variant-phrased skills via skills_match
     (closes the literal _phrase_in_prose hole) while still demoting phantoms.

The mechanism-level separation test drives the REAL code path
(_demote_evidenceless_matches -> compute_match_score) with hand-built
LLM-output stand-ins. The true benchmark off-diagonal separation can only be
recomputed by re-running the LLM gap analysis (Groq) -- the benchmark stores
counts, not raw skill names + profiles -- so this validates the fix mechanism,
not the fixture numbers. NO live Groq.
"""
from __future__ import annotations

import math
import statistics as st
from types import SimpleNamespace

from django.test import SimpleTestCase

from analysis.services.skill_score import compute_match_score, BASE
from analysis.services import gap_analyzer as ga
from profiles.services.schemas import MissingSkill, MatchedSkill, TieredGapAnalysisResult


def _miss(name, proximity=0.0):
    return MissingSkill(name=name, source_quote="", proximity=proximity,
                        proximity_reason="", bridge_hint=None)


def _matched(name):
    # Non-empty evidence so the LLM-evidence gate (gate 1) passes and the
    # PROFILE-GROUNDING gate (gate 2 — the one Fix B touches) decides demotion.
    return MatchedSkill(name=name, evidence_source="experience",
                        evidence_quote=f"worked with {name}")


# ---------------------------------------------------------------------------
# Fix A — formula renormalization (floor gone, perfect -> ~1.0)
# ---------------------------------------------------------------------------
class FormulaRenormTests(SimpleTestCase):
    def test_no_nice_perfect_must_is_one(self):
        # 4 must matched, 0 missing, NO nice tier -> ~1.0 (was already 1.0,
        # must stay 1.0 after renorm).
        s = compute_match_score([_matched("a")] * 4, [], [], [])
        self.assertAlmostEqual(s, 1.0, places=4)

    def test_no_nice_zero_must_is_base_not_quarter(self):
        # 0 matched, 4 missing (proximity 0), NO nice tier.
        # OLD: 0.05 + 0.75*0 + 0.20*1.0 = 0.25 (the floor).
        # NEW: 0.05 + 0.95*0 = 0.05.
        s = compute_match_score([], [_miss("a"), _miss("b"), _miss("c"), _miss("d")], [], [])
        self.assertAlmostEqual(s, BASE, places=4)
        self.assertAlmostEqual(s, 0.05, places=4)
        self.assertLess(s, 0.25)                      # floor is gone

    def test_no_nice_half_must_is_point_525(self):
        # 2 matched / 4 total must, no nice -> 0.05 + 0.95*0.5 = 0.525.
        s = compute_match_score([_matched("a"), _matched("b")],
                                [_miss("c"), _miss("d")], [], [])
        self.assertAlmostEqual(s, 0.525, places=4)

    def test_both_tiers_present_unchanged_weights(self):
        # Perfect both tiers -> base + 0.75 + 0.20 = 1.0 (renorm is identity).
        s = compute_match_score([_matched("a")], [], [_matched("b")], [])
        self.assertAlmostEqual(s, 1.0, places=4)

    def test_no_must_tier_nice_carries_full_weight(self):
        # Only a nice tier, fully matched -> base + 0.95*1.0 = 1.0 (symmetric).
        s = compute_match_score([], [], [_matched("a")] * 3, [])
        self.assertAlmostEqual(s, 1.0, places=4)

    def test_empty_contract_is_base(self):
        # No skills in either tier -> just BASE.
        s = compute_match_score([], [], [], [])
        self.assertAlmostEqual(s, BASE, places=4)

    def test_proximity_partial_credit_still_applies(self):
        # 0 matched, 1 missing @ proximity 0.8, no nice ->
        # must_credit = 0.5*0.8 = 0.4 over total 1 -> must_ratio 0.4
        # 0.05 + 0.95*0.4 = 0.43.
        s = compute_match_score([], [_miss("a", proximity=0.8)], [], [])
        self.assertAlmostEqual(s, 0.43, places=4)


# ---------------------------------------------------------------------------
# Fix B — variant-aware grounding (variant stays matched, phantom demoted)
# ---------------------------------------------------------------------------
class VariantGroundingTests(SimpleTestCase):
    def test_variant_in_declared_skills_grounds(self):
        prof = {"skills": [{"name": "RESTful APIs"}, {"name": "Vue"}]}
        prose = ga._grounding_prose_corpus(prof)
        self.assertTrue(ga._skill_is_grounded("REST API", prof, prose))      # via skills_match
        self.assertTrue(ga._skill_is_grounded("Vue.js", prof, prose))

    def test_variant_in_prose_grounds_now(self):
        # Skill present ONLY in experience prose, variant-phrased, NOT in skills[].
        prof = {"skills": [], "experiences": [
            {"description": ["Developed RESTful API services for the platform"]}]}
        prose = ga._grounding_prose_corpus(prof)
        self.assertFalse(ga._phrase_in_prose("REST API", prose))             # literal misses
        self.assertTrue(ga._phrase_variant_in_prose("REST API", prose))      # variant catches
        self.assertTrue(ga._skill_is_grounded("REST API", prof, prose))

    def test_phantom_still_demoted(self):
        prof = {"skills": [{"name": "Flutter"}], "experiences": [
            {"description": ["Built mobile apps with Flutter and Firebase"]}]}
        prose = ga._grounding_prose_corpus(prof)
        for phantom in ("Kubernetes", "GoRouter", "Terraform"):
            self.assertFalse(ga._skill_is_grounded(phantom, prof, prose),
                             f"{phantom} should NOT ground (no evidence anywhere)")

    def test_ambiguous_english_word_does_not_false_ground(self):
        # The word "rest" (English) in prose must NOT ground "REST API"
        # (single bare alias is never prose-expanded).
        prof = {"skills": [], "experiences": [
            {"description": ["Took the rest of the sprint to refactor the team's code"]}]}
        prose = ga._grounding_prose_corpus(prof)
        self.assertFalse(ga._skill_is_grounded("REST API", prof, prose))


# ---------------------------------------------------------------------------
# Separation (mechanism) — drive the REAL demote+score path with stand-ins
# ---------------------------------------------------------------------------
def _result(matched_must, missing_must):
    """A real TieredGapAnalysisResult standing in for the LLM's output."""
    return TieredGapAnalysisResult(
        matched_must_have=[_matched(n) for n in matched_must],
        missing_must_have=[_miss(n) for n in missing_must],
        matched_nice_to_have=[],
        missing_nice_to_have=[],
        soft_skill_gaps=[],
    )


def _score_after_grounding(matched_must, missing_must, profile):
    """Run the production Phase-2a grounding demotion, then the score."""
    res = _result(matched_must, missing_must)
    demoted = ga._demote_evidenceless_matches(res, profile_data=profile)
    return compute_match_score(
        demoted.matched_must_have, demoted.missing_must_have,
        demoted.matched_nice_to_have, demoted.missing_nice_to_have,
    )


def cohens_d(a, b):
    va, vb = st.pvariance(a), st.pvariance(b)
    na, nb = len(a), len(b)
    pooled = math.sqrt(((na * va) + (nb * vb)) / (na + nb))
    return (st.fmean(a) - st.fmean(b)) / pooled if pooled else float("inf")


class SeparationMechanismTests(SimpleTestCase):
    """Strong-fit pairs (candidate genuinely has the skills, some variant-
    phrased) must score clearly above weak-fit pairs (phantom 'matches' that
    grounding demotes). Both fixes active."""

    MUST = ["REST API", "Vue.js", "PostgreSQL", "Docker"]

    def _strong_profile(self):
        # Has all 4: PostgreSQL/Docker declared; REST API only as the VARIANT
        # phrase "RESTful API services" (exercises Fix B's prose-variant path);
        # Vue.js present literally.
        return {
            "skills": [{"name": "PostgreSQL"}, {"name": "Docker"}],
            "experiences": [{"description": [
                "Developed RESTful API services and built Vue.js single-page applications"]}],
        }

    def _weak_profile(self):
        # Has none of the must-haves; unrelated skills only.
        return {
            "skills": [{"name": "Photoshop"}, {"name": "Excel"}],
            "experiences": [{"description": ["Designed marketing collateral and managed spreadsheets"]}],
        }

    def test_strong_pair_scores_high(self):
        # LLM "matched" all 4; grounding must KEEP all 4 (2 declared, 2 variant
        # prose) -> must_ratio 1.0 -> ~1.0.
        s = _score_after_grounding(self.MUST, [], self._strong_profile())
        self.assertGreater(s, 0.85, f"strong pair scored {s}, expected high")

    def test_weak_pair_scores_low(self):
        # LLM hallucinated all 4 as matched; grounding demotes all (phantoms)
        # -> must_ratio 0 -> BASE (0.05).
        s = _score_after_grounding(self.MUST, [], self._weak_profile())
        self.assertLess(s, 0.25, f"weak pair scored {s}, expected low (floor gone)")

    def test_diagonal_control_perfect_match_scores_high(self):
        # Tautological pair: candidate genuinely has every must-have (declared).
        # Was 0.258 under the bug; must now be ~1.0.
        perfect = {"skills": [{"name": m} for m in self.MUST]}
        s = _score_after_grounding(self.MUST, [], perfect)
        self.assertGreater(s, 0.9, f"diagonal/perfect pair scored {s}, expected ~1.0")

    def test_strong_weak_separate(self):
        # A small bucket of each through the real path; strong mean must clear
        # weak mean with real separation (not the collapsed 0.26-flat).
        strong = [_score_after_grounding(self.MUST, [], self._strong_profile()) for _ in range(5)]
        weak = [_score_after_grounding(self.MUST, [], self._weak_profile()) for _ in range(5)]
        sm, wm = st.fmean(strong), st.fmean(weak)
        self.assertGreater(sm, wm + 0.5,
                           f"strong {sm:.3f} not clearly above weak {wm:.3f}")
        # Cohen's d is inf here (zero within-bucket variance); assert the gap.
        self.assertGreater(sm - wm, 0.7)
