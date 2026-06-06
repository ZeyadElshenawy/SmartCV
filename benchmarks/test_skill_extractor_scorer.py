"""Skill-extractor eval scorer: canonical/variant matcher tests.

Verifies the verbose-phrasing double-penalty fix WITHOUT masking genuine
recall loss:
  * verbose/grouped phrasing of a real skill MATCHES (neither miss nor
    hallucination);
  * a gold skill the extractor never emitted in any form is still a MISS;
  * a real hallucination (not a variant of any gold skill) still counts.

No live Groq — pure scorer logic.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from benchmarks.skill_extractor_eval import _skill_equiv, _matches_any, _score


class SkillEquivTests(SimpleTestCase):
    # --- double-penalty cases: verbose/grouped variants MUST match ----------
    def test_trailing_qualifier_variants_match(self):
        for ext, gold in [
            ("Tailwind CSS", "Tailwind"),
            ("REST API design", "REST API"),
            ("Linux systems", "Linux"),
            ("Helm chart", "Helm"),
            ("Flux GitOps", "Flux"),
            ("Git workflows", "Git"),
        ]:
            self.assertTrue(_skill_equiv(ext, gold), f"{ext!r} should match {gold!r}")
            self.assertTrue(_skill_equiv(gold, ext), "match must be symmetric")

    def test_alias_variants_match(self):
        # Covered by the production canonical matcher (skills_match).
        for a, b in [("Vue.js", "Vue"), ("React.js", "React"),
                     ("RESTful APIs", "REST API"), ("REST API integration", "REST API")]:
            self.assertTrue(_skill_equiv(a, b), f"{a!r} should match {b!r}")

    def test_slash_grouping_matches(self):
        self.assertTrue(_skill_equiv("npm/yarn", "npm"))
        self.assertTrue(_skill_equiv("npm/yarn", "yarn"))
        self.assertTrue(_skill_equiv("yarn", "npm/yarn"))

    # --- the critical guard: do NOT mask recall loss / hallucinations -------
    def test_distinct_skill_not_falsely_matched(self):
        # "Native" is a skill-distinguishing modifier, NOT a generic qualifier.
        self.assertFalse(_skill_equiv("React Native", "React"))
        # Sharing one token must not credit a different skill.
        self.assertFalse(_skill_equiv("Firebase Messaging", "Firebase"))

    def test_unrelated_skills_never_match(self):
        for a, b in [("Kubernetes", "Docker"), ("Kubernetes", "React"),
                     ("EKS", "S3"), ("EKS", "IAM"), ("Vue.js", "React")]:
            self.assertFalse(_skill_equiv(a, b), f"{a!r} must NOT match {b!r}")


class ScoreGuardTests(SimpleTestCase):
    """End-to-end _score: the fix credits variants, never absent skills."""

    def test_verbose_match_is_neither_miss_nor_hallucination(self):
        # Extractor said "Tailwind CSS"; gold wanted "Tailwind". One skill,
        # correctly extracted -> precision 1, recall 1, no hallucination, and
        # NOT double-counted as extra + missed.
        r = _score(["Tailwind CSS"], ["Tailwind"])
        self.assertEqual(r["hallucination_rate"], 0.0)
        self.assertEqual(r["recall"], 1.0)
        self.assertEqual(r["precision"], 1.0)
        self.assertEqual(r["missed"], [])
        self.assertEqual(r["extra"], [])

    def test_recall_loss_not_masked(self):
        # Gold has EKS; extractor emitted nothing resembling it -> still a MISS.
        r = _score(["S3", "IAM", "Lambda"], ["EKS", "S3", "IAM"])
        self.assertIn("EKS", r["missed"])
        self.assertLess(r["recall"], 1.0)

    def test_hallucination_still_counts(self):
        # Extractor emits Kubernetes, not in gold and not a variant of any gold
        # skill -> still a hallucination (the fix must not paper over it).
        r = _score(["React", "Kubernetes"], ["React", "TypeScript"])
        self.assertIn("Kubernetes", r["extra"])
        self.assertGreater(r["hallucination_rate"], 0.0)

    def test_matches_any_basic(self):
        self.assertTrue(_matches_any("Tailwind CSS", ["Webpack", "Tailwind"]))
        self.assertFalse(_matches_any("Kubernetes", ["React", "Tailwind"]))
