"""Unit tests for resumes.services.bullet_validator (§4 of the RAG plan)."""
from __future__ import annotations

import re

from django.test import SimpleTestCase

from resumes.services.bullet_validator import (
    ValidationReport,
    validate_resume,
)


def _one_bullet(bullet: str, *, kind: str = "experience") -> dict:
    """Wrap a single bullet into the minimum resume dict the validator
    accepts. `kind` is 'experience' or 'projects'."""
    role_key = "title" if kind == "experience" else "name"
    return {
        kind: [{role_key: "Test Role", "description": [bullet]}],
    }


def _has_finding(report: ValidationReport, rule_id: str) -> bool:
    return any(f.rule_id == rule_id for f in report.findings)


class TestTierABulletRules(SimpleTestCase):
    """Each Tier-A rule: one bullet that should pass, one that should fail."""

    def test_A1_banned_phrase_flags_leverage(self):
        resume = _one_bullet("Leveraged React to build a 12-component design system used by 4 teams.")
        report = validate_resume(resume)
        self.assertTrue(_has_finding(report, "A1_banned_phrase"))
        offending = [f for f in report.findings if f.rule_id == "A1_banned_phrase"]
        # Substring 'leverage' matches inside 'Leveraged'; suggestion is 'use'.
        self.assertIn("leverage", offending[0].issue.lower())

    def test_A1_banned_phrase_passes_plain_english(self):
        resume = _one_bullet("Built a React design system with 12 reusable components used by 4 product teams.")
        report = validate_resume(resume)
        self.assertFalse(_has_finding(report, "A1_banned_phrase"))

    def test_A3_duty_opener_flags_responsible_for(self):
        resume = _one_bullet("Responsible for ensuring nightly cron jobs run on the data warehouse.")
        report = validate_resume(resume)
        self.assertTrue(_has_finding(report, "A3_duty_opener"))

    def test_A3_duty_opener_passes_accomplishment(self):
        resume = _one_bullet("Ensured nightly cron jobs ran on the data warehouse, reducing manual interventions by 40%.")
        report = validate_resume(resume)
        self.assertFalse(_has_finding(report, "A3_duty_opener"))

    def test_A5_length_short_flags(self):
        resume = _one_bullet("Did stuff.")
        report = validate_resume(resume)
        self.assertTrue(_has_finding(report, "A5_length_short"))

    def test_A5_length_long_flags(self):
        bullet = "Built " + "a long-winded enumeration of details " * 12  # ~470 chars
        resume = _one_bullet(bullet)
        report = validate_resume(resume)
        self.assertTrue(_has_finding(report, "A5_length_long"))

    def test_A5_length_in_range_passes(self):
        resume = _one_bullet("Migrated 47 services from a monolithic deploy to a Kubernetes rollout, cutting median deploy time from 22 min to 5 min.")
        report = validate_resume(resume)
        self.assertFalse(_has_finding(report, "A5_length_short"))
        self.assertFalse(_has_finding(report, "A5_length_long"))

    def test_A6_em_dash_flags_and_suggests_comma(self):
        resume = _one_bullet("Built a CI pipeline—using GitHub Actions—that cut release time from 40 min to 6 min.")
        report = validate_resume(resume)
        self.assertTrue(_has_finding(report, "A6_em_dash"))
        f = [f for f in report.findings if f.rule_id == "A6_em_dash"][0]
        self.assertIsNotNone(f.suggested_fix)
        self.assertNotIn("—", f.suggested_fix)
        self.assertIn(", ", f.suggested_fix)

    def test_A7_demonstrating_closer_flags(self):
        resume = _one_bullet("Built a CI pipeline that cut release time, demonstrating strong DevOps skills.")
        report = validate_resume(resume)
        self.assertTrue(_has_finding(report, "A7_demonstrating_closer"))

    def test_A2_action_verb_passes_capitalized_system_name(self):
        # Per HUMAN_VOICE_RULE rule 4, bullets MAY lead with a system name.
        resume = _one_bullet("Storybook component library now hosts 47 components used by 4 product teams.")
        report = validate_resume(resume)
        self.assertFalse(_has_finding(report, "A2_action_verb_start"))

    def test_A2_action_verb_passes_metric_lead(self):
        # ...or with a metric.
        resume = _one_bullet("p95 latency dropped from 2.3s to 480ms after caching layer landed.")
        report = validate_resume(resume)
        self.assertFalse(_has_finding(report, "A2_action_verb_start"))


class TestTierBRoleRules(SimpleTestCase):

    def test_B1_quantification_flags_when_no_numbers(self):
        resume = {
            "experience": [{
                "title": "Backend Engineer",
                "description": [
                    "Built the payment service backend.",
                    "Wrote integration tests covering edge cases.",
                    "Mentored junior engineers on incident response.",
                ],
            }],
        }
        report = validate_resume(resume)
        self.assertTrue(_has_finding(report, "B1_quantification"))
        # Promoted to ERROR because ≥3 bullets with 0 numbers.
        b1 = [f for f in report.findings if f.rule_id == "B1_quantification"][0]
        self.assertEqual(b1.severity, "error")

    def test_B1_quantification_passes_with_one_metric(self):
        resume = {
            "experience": [{
                "title": "Backend Engineer",
                "description": [
                    "Built the payment service handling 12,000 transactions per minute.",
                    "Wrote integration tests covering the top 30 edge cases.",
                    "Mentored 4 junior engineers on incident response.",
                ],
            }],
        }
        report = validate_resume(resume)
        self.assertFalse(_has_finding(report, "B1_quantification"))

    def test_B2_verb_diversity_flags_repeated_opener(self):
        resume = {
            "experience": [{
                "title": "Lead Engineer",
                "description": [
                    "Led the migration from REST to GraphQL across 12 services.",
                    "Led the on-call rotation for the platform team of 8.",
                    "Coordinated a quarterly capacity-planning review involving 5 teams.",
                ],
            }],
        }
        report = validate_resume(resume)
        self.assertTrue(_has_finding(report, "B2_verb_diversity"))


class TestTierCResumeRules(SimpleTestCase):

    def test_C1_length_flags_too_few_for_senior(self):
        resume = {
            "experience": [{
                "title": "Senior Engineer",
                "description": [
                    "Built a deployment system using Kubernetes for the platform team of 8.",
                    "Migrated 12 services from REST to GraphQL across the org.",
                ],
            }],
        }
        report = validate_resume(resume, seniority="senior")
        # 2 bullets vs senior band 15-35
        self.assertTrue(_has_finding(report, "C1_resume_length"))

    def test_C1_length_in_band_passes(self):
        resume = {
            "experience": [{
                "title": "Mid Engineer",
                "description": [f"Built feature {i+1} reducing latency by {(i+1)*5}%." for i in range(15)],
            }],
        }
        report = validate_resume(resume, seniority="mid")
        self.assertFalse(_has_finding(report, "C1_resume_length"))


class TestAutoFix(SimpleTestCase):

    def test_safe_autofix_replaces_em_dash_and_banned_word(self):
        resume = _one_bullet("Leveraged React—built a 12-component design system across 4 teams.")
        out_resume, report = validate_resume(resume, mode="safe_autofix")
        rewritten = out_resume["experience"][0]["description"][0]
        # em-dash replaced with comma
        self.assertNotIn("—", rewritten)
        self.assertIn(", ", rewritten)
        # case-aware: "Leveraged" → "Used"
        self.assertTrue(rewritten.startswith("Used"))
        self.assertNotIn("Leveraged", rewritten)
        # Report still records the original findings (passed=False because A1 is ERROR).
        self.assertFalse(report.passed)

    def test_report_only_does_not_mutate(self):
        original = "Leveraged React—built it."
        resume = _one_bullet(original)
        report = validate_resume(resume)  # default mode
        self.assertEqual(resume["experience"][0]["description"][0], original)
        self.assertIsInstance(report, ValidationReport)

    def test_kitchen_sink_bullet_finds_all_expected_rules(self):
        """A bullet with several AI tells must hit multiple distinct rules.

        Note A7 requires `, demonstrating <skill>` — the comma is mandatory.
        Here we use one bullet with the comma form (triggers A7) plus a
        separate em-dash so A6 also fires.
        """
        resume = _one_bullet(
            "Leveraged React to spearhead a robust solution, demonstrating strong skills—really."
        )
        report = validate_resume(resume)
        rule_ids = {f.rule_id for f in report.findings}
        for needed in {"A1_banned_phrase", "A6_em_dash", "A7_demonstrating_closer"}:
            self.assertIn(needed, rule_ids, msg=f"missing {needed}; got {rule_ids}")
        # Banned phrases that should have fired (>= 3 distinct tokens):
        a1_phrases = {
            re.search(r"'([^']+)'", f.issue).group(1)
            for f in report.findings
            if f.rule_id == "A1_banned_phrase" and re.search(r"'([^']+)'", f.issue)
        }
        for tok in ("leverage", "spearhead", "robust"):
            self.assertIn(tok, a1_phrases, msg=f"expected '{tok}' in A1 findings; got {a1_phrases}")
