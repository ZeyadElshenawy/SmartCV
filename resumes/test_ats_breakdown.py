"""Slice 1 — read-only ATS breakdown panel.

Covers the resume-aware helper (`breakdown_for_resume`), the idempotent
`refresh_ats_score`, the `score_reconciles` clamp flag, the read-only GET
endpoint, and the `base` key just added to the deterministic scorer.

Run: ``python manage.py test resumes`` (Django TestCase — these resolve
resume.gap_analysis.job over the ORM and exercise the view via the test
Client, so they belong in the app suite, not pytest tests/).
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from jobs.models import Job
from analysis.models import GapAnalysis
from resumes.models import GeneratedResume
from resumes.services.scoring import compute_ats_breakdown
from resumes.services.ats_breakdown import (
    breakdown_for_resume,
    refresh_ats_score,
    score_reconciles,
)

# Keys the panel/endpoint depend on. `base` is the Slice-1 addition.
ATS_BREAKDOWN_KEYS = {
    "score", "raw_score", "base", "matched_count", "total_count",
    "in_context_count", "in_context_bonus", "stuffed_skills",
    "stuffing_penalty", "keyword_counts",
    "must_have", "nice_to_have", "in_context", "stuffing",
}

# A general fixture (not tuned to any real profile): tiered job + a résumé that
# matches some must/nice skills, demonstrates two in bullets (in-context bonus),
# and stuffs one (Python) past the threshold.
RICH_SKILLS = ["Python", "Django", "PostgreSQL", "Docker", "Kubernetes", "GraphQL"]
RICH_TIERS = {
    "must_have": ["Python", "Django", "Docker"],
    "nice_to_have": ["PostgreSQL", "Kubernetes", "GraphQL"],
}
RICH_CONTENT = {
    "professional_summary": "Backend engineer who built REST APIs in Python.",
    "skills": ["Python", "Django", "PostgreSQL"],
    "experience": [{
        "title": "Engineer",
        "description": [
            "Built APIs in Python. Python Python Python Python Python services everywhere.",
            "Used Django and PostgreSQL daily.",
        ],
    }],
    "projects": [{
        "name": "x",
        "description": ["REST API integration with Python and Django"],
    }],
}


def _user(email):
    return get_user_model().objects.create_user(
        username=email, email=email, password="x",
    )


def _make_chain(user, *, skills=None, tiers=None, content=None, ats_score=0.0):
    job = Job.objects.create(
        user=user, title="Engineer", description="JD text.",
        extracted_skills=skills or [],
        extracted_skills_tiers=tiers or {},
    )
    gap = GapAnalysis.objects.create(user=user, job=job)
    resume = GeneratedResume.objects.create(
        gap_analysis=gap, content=content or {}, ats_score=ats_score,
    )
    return job, gap, resume


class BreakdownForResumeTests(TestCase):
    def setUp(self):
        self.user = _user("helper@example.com")
        self.job, self.gap, self.resume = _make_chain(
            self.user, skills=RICH_SKILLS, tiers=RICH_TIERS, content=RICH_CONTENT,
        )

    def test_determinism(self):
        a = breakdown_for_resume(self.resume)
        b = breakdown_for_resume(self.resume)
        self.assertEqual(a, b)

    def test_one_computation_matches_pure_scorer(self):
        # The helper must be a thin resolver over the pure scorer — same triple,
        # same number, no second path.
        direct = compute_ats_breakdown(
            self.resume.content, self.job.extracted_skills,
            self.job.extracted_skills_tiers,
        )
        self.assertEqual(breakdown_for_resume(self.resume), direct)

    def test_content_param_scores_the_hypothetical_not_resume_content(self):
        # Locks the Slice-2 reuse contract: an explicit content is scored as-is.
        hypo = breakdown_for_resume(self.resume, {"skills": []})
        self.assertEqual(hypo["matched_count"], 0)
        self.assertEqual(hypo["score"], 0.0)
        self.assertGreater(breakdown_for_resume(self.resume)["matched_count"], 0)

    def test_explicit_empty_content_is_honoured(self):
        # content is None selects resume.content; an explicit {} must NOT.
        self.assertEqual(breakdown_for_resume(self.resume, {})["matched_count"], 0)

    def test_base_key_present_and_reconciles_when_not_clamped(self):
        bd = breakdown_for_resume(self.resume)
        self.assertIn("base", bd)
        # base + in-context − stuffing == score (clamp didn't bind here).
        self.assertAlmostEqual(
            bd["base"] + bd["in_context_bonus"] - bd["stuffing_penalty"],
            bd["score"], delta=0.1,
        )
        self.assertTrue(score_reconciles(bd))


class ScoreReconcilesTests(TestCase):
    def test_true_when_clamp_did_not_bind(self):
        bd = {"base": 58.3, "in_context_bonus": 6.0, "stuffing_penalty": 5.0, "score": 59.3}
        self.assertTrue(score_reconciles(bd))

    def test_false_when_clamp_bound_high(self):
        # Unclamped 104 → score clamped to 100 → equation must NOT be asserted.
        bd = {"base": 100.0, "in_context_bonus": 4.0, "stuffing_penalty": 0.0, "score": 100.0}
        self.assertFalse(score_reconciles(bd))

    def test_real_over_hundred_case_flags_not_reconciling(self):
        user = _user("clamp@example.com")
        _job, _gap, resume = _make_chain(
            user,
            skills=["Python", "Django"],
            tiers={"must_have": ["Python", "Django"], "nice_to_have": []},
            content={
                "skills": ["Python", "Django"],
                "experience": [{"title": "Eng",
                                "description": ["Used Python and Django heavily."]}],
            },
        )
        bd = breakdown_for_resume(resume)
        self.assertEqual(bd["score"], 100.0)        # clamped
        self.assertGreater(
            bd["base"] + bd["in_context_bonus"] - bd["stuffing_penalty"], 100.0,
        )
        self.assertFalse(score_reconciles(bd))


class LegacyAndEmptyEdgeTests(TestCase):
    def test_no_tiers_collapses_to_must_have(self):
        user = _user("notiers@example.com")
        _job, _gap, resume = _make_chain(
            user,
            skills=["Python", "SQL"],
            tiers=None,                       # → extracted_skills_tiers == {} → None path
            content={"skills": ["Python"]},   # Python in skills only: no bonus/penalty
        )
        bd = breakdown_for_resume(resume)
        self.assertEqual(bd["nice_to_have"], {"matched": [], "missed": [], "coverage": 0.0})
        self.assertEqual(bd["base"], bd["raw_score"])   # tier-weighting didn't apply
        self.assertEqual(bd["score"], bd["raw_score"])  # no in-context/stuffing here

    def test_empty_job_skills_yields_zeroed_breakdown(self):
        user = _user("noskills@example.com")
        _job, _gap, resume = _make_chain(user, skills=[], content={"skills": ["Python"]})
        bd = breakdown_for_resume(resume)
        self.assertEqual(bd["score"], 0.0)
        self.assertEqual(bd["total_count"], 0)
        self.assertEqual(bd["must_have"], {"matched": [], "missed": [], "coverage": 0.0})


class AtsBreakdownEndpointTests(TestCase):
    def setUp(self):
        self.user = _user("owner@example.com")
        self.job, self.gap, self.resume = _make_chain(
            self.user, skills=RICH_SKILLS, tiers=RICH_TIERS, content=RICH_CONTENT,
        )
        self.url = reverse("resume_ats_breakdown_api", args=[self.resume.id])

    def test_get_returns_full_breakdown_contract(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(ATS_BREAKDOWN_KEYS.issubset(data.keys()))
        self.assertIsInstance(data["base"], (int, float))
        self.assertIsInstance(data["must_have"], dict)
        # One computation across the HTTP boundary too.
        self.assertEqual(data["score"], breakdown_for_resume(self.resume)["score"])

    def test_empty_skills_endpoint_200_zeroed(self):
        user = _user("noskills2@example.com")
        _job, _gap, resume = _make_chain(user, skills=[], content={"skills": ["Python"]})
        self.client.force_login(user)
        resp = self.client.get(reverse("resume_ats_breakdown_api", args=[resume.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["score"], 0.0)

    def test_ownership_guard_rejects_other_user(self):
        other = _user("intruder@example.com")
        self.client.force_login(other)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_edit_view_injects_breakdown_into_context(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("resume_edit", args=[self.resume.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ats_breakdown", resp.context)
        self.assertEqual(
            resp.context["ats_breakdown"]["score"],
            breakdown_for_resume(self.resume)["score"],
        )


class RefreshAtsScoreTests(TestCase):
    def setUp(self):
        self.user = _user("refresh@example.com")
        self.job, self.gap, self.resume = _make_chain(
            self.user, skills=RICH_SKILLS, tiers=RICH_TIERS, content=RICH_CONTENT,
            ats_score=0.0,
        )

    def test_persists_only_ats_score_when_changed(self):
        before_content = dict(self.resume.content)
        before_report = dict(self.resume.validation_report)
        refresh_ats_score(self.resume)
        self.resume.refresh_from_db()
        self.assertGreater(self.resume.ats_score, 0.0)              # synced up
        self.assertEqual(self.resume.content, before_content)       # content untouched
        self.assertEqual(self.resume.validation_report, before_report)

    def test_noop_when_already_in_sync(self):
        refresh_ats_score(self.resume)          # sync once
        self.resume.refresh_from_db()
        with patch.object(self.resume, "save") as mock_save:
            refresh_ats_score(self.resume)      # already correct → no write
        mock_save.assert_not_called()
