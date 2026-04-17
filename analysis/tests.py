"""Tests for analysis.services.gap_analyzer.

Focuses on the deterministic Phase 2 reconciliation logic and the early-exit /
fallback branches — not on LLM output quality. The LLM call is mocked so these
run fast and don't need an API key.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from analysis.services.gap_analyzer import compute_gap_analysis
from profiles.services.schemas import GapAnalysisResult


def make_profile(skills=None, experiences=None, projects=None, certifications=None,
                 education=None, github_signals=None):
    """Minimal profile stub matching the attributes gap_analyzer reads.

    Pass `github_signals=<dict>` to populate
    profile.data_content['github_signals'] (consumed by _format_github_activity).
    """
    return SimpleNamespace(
        skills=skills or [],
        experiences=experiences or [],
        projects=projects or [],
        certifications=certifications or [],
        education=education or [],
        data_content={'github_signals': github_signals} if github_signals is not None else {},
    )


def make_job(skills, title="Software Engineer", company="ACME"):
    return SimpleNamespace(extracted_skills=list(skills), title=title, company=company)


def llm_returning(matched=None, missing=None, soft=None, score=0.5):
    """Build a mocked get_structured_llm chain that returns a GapAnalysisResult."""
    result = GapAnalysisResult(
        matched_skills=list(matched or []),
        critical_missing_skills=list(missing or []),
        soft_skill_gaps=list(soft or []),
        similarity_score=score,
    )
    structured_llm = MagicMock()
    structured_llm.invoke.return_value = result
    return structured_llm


class EarlyExitTests(SimpleTestCase):
    def test_no_job_skills_skips_llm_and_returns_zero_score(self):
        profile = make_profile(skills=["Python"])
        job = make_job(skills=[])

        with patch("analysis.services.gap_analyzer.get_structured_llm") as mock_llm:
            result = compute_gap_analysis(profile, job)

        mock_llm.assert_not_called()
        self.assertEqual(result["analysis_method"], "no_job_skills")
        self.assertEqual(result["similarity_score"], 0.0)
        self.assertEqual(result["missing_skills"], [])

    def test_empty_profile_marks_all_job_skills_missing(self):
        profile = make_profile()  # fully empty
        job = make_job(skills=["Python", "Django", "SQL"])

        with patch("analysis.services.gap_analyzer.get_structured_llm") as mock_llm:
            result = compute_gap_analysis(profile, job)

        mock_llm.assert_not_called()
        self.assertEqual(result["analysis_method"], "empty_profile")
        self.assertEqual(result["missing_skills"], ["Python", "Django", "SQL"])
        self.assertEqual(result["similarity_score"], 0.0)


class ReconciliationTests(SimpleTestCase):
    def test_skill_in_both_matched_and_missing_is_deduped_to_matched(self):
        profile = make_profile(skills=["Python"])
        job = make_job(skills=["Python", "Docker"])

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=llm_returning(matched=["Python"], missing=["Python", "Docker"]),
        ):
            result = compute_gap_analysis(profile, job)

        self.assertIn("Python", result["matched_skills"])
        self.assertNotIn("Python", result["missing_skills"])
        self.assertIn("Docker", result["missing_skills"])

    def test_unaccounted_job_skill_is_added_to_missing(self):
        """If LLM forgets to categorize a job skill, reconciliation adds it to missing."""
        profile = make_profile(skills=["Python"])
        job = make_job(skills=["Python", "Kubernetes"])

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=llm_returning(matched=["Python"], missing=[]),  # forgot Kubernetes
        ):
            result = compute_gap_analysis(profile, job)

        self.assertIn("Kubernetes", result["missing_skills"])

    def test_fuzzy_variant_spelling_counts_as_matched_not_missing(self):
        """'PySpark' in job, 'Pyspark' in matched -> not duplicated into missing."""
        profile = make_profile(skills=["Pyspark"])
        job = make_job(skills=["PySpark"])

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=llm_returning(matched=["Pyspark"], missing=[]),
        ):
            result = compute_gap_analysis(profile, job)

        self.assertNotIn("PySpark", result["missing_skills"])
        self.assertEqual(result["missing_skills"], [])

    def test_case_insensitive_match_is_not_duplicated(self):
        profile = make_profile(skills=["python"])
        job = make_job(skills=["Python"])

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=llm_returning(matched=["python"], missing=["Python"]),
        ):
            result = compute_gap_analysis(profile, job)

        self.assertNotIn("Python", result["missing_skills"])

    def test_critical_missing_mirrors_missing_skills(self):
        profile = make_profile(skills=["Python"])
        job = make_job(skills=["Python", "Docker", "Kubernetes"])

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=llm_returning(matched=["Python"], missing=["Docker", "Kubernetes"]),
        ):
            result = compute_gap_analysis(profile, job)

        self.assertEqual(result["critical_missing_skills"], result["missing_skills"])


class ScoreClampingTests(SimpleTestCase):
    def test_score_above_one_is_clamped(self):
        profile = make_profile(skills=["Python"])
        job = make_job(skills=["Python"])

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=llm_returning(matched=["Python"], score=1.7),
        ):
            result = compute_gap_analysis(profile, job)

        self.assertEqual(result["similarity_score"], 1.0)

    def test_score_below_zero_is_clamped(self):
        profile = make_profile(skills=["Python"])
        job = make_job(skills=["Python"])

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=llm_returning(matched=["Python"], score=-0.5),
        ):
            result = compute_gap_analysis(profile, job)

        self.assertEqual(result["similarity_score"], 0.0)


class FallbackTests(SimpleTestCase):
    def test_llm_exception_triggers_fallback_set_matching(self):
        profile = make_profile(skills=["Python", "Django"])
        job = make_job(skills=["Python", "Kubernetes"])

        failing_llm = MagicMock()
        failing_llm.invoke.side_effect = RuntimeError("LLM unavailable")

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=failing_llm,
        ):
            result = compute_gap_analysis(profile, job)

        self.assertEqual(result["analysis_method"], "fallback")
        self.assertIn("Python", result["matched_skills"])
        self.assertIn("Kubernetes", result["missing_skills"])
        # 1 of 2 job skills matched -> score approx 0.5
        self.assertEqual(result["similarity_score"], 0.5)

    def test_fallback_handles_dict_shaped_skills(self):
        """profile.skills can be list[dict] with 'name' keys, not just list[str]."""
        profile = make_profile(skills=[{"name": "Python", "years": 3}, {"name": "Django"}])
        job = make_job(skills=["Python"])

        failing_llm = MagicMock()
        failing_llm.invoke.side_effect = RuntimeError("boom")

        with patch(
            "analysis.services.gap_analyzer.get_structured_llm",
            return_value=failing_llm,
        ):
            result = compute_gap_analysis(profile, job)

        self.assertEqual(result["analysis_method"], "fallback")
        self.assertIn("Python", result["matched_skills"])


# ============================================================
# GitHub activity context block
# ============================================================

from analysis.services.gap_analyzer import (
    _build_full_candidate_context,
    _format_github_activity,
)


class GitHubActivityFormattingTests(SimpleTestCase):
    """Verifies the GITHUB ACTIVITY block format the LLM consumes."""

    SAMPLE_SNAPSHOT = {
        'username': 'octocat',
        'public_repos': 12,
        'total_stars': 247,
        'recent_commit_count': 47,
        'language_breakdown': [['Python', 8], ['TypeScript', 3]],
        'top_repos': [
            {'name': 'ml-pipeline', 'language': 'Python', 'stars': 120,
             'description': 'Distributed training harness'},
            {'name': 'spark-tools', 'language': 'Python', 'stars': 80,
             'description': 'Helpers for PySpark on EMR'},
        ],
        'fetched_at': '2026-04-14T10:00:00Z',
    }

    def test_no_data_content_attr_returns_empty(self):
        profile = SimpleNamespace(skills=[], experiences=[], projects=[],
                                  certifications=[], education=[])
        self.assertEqual(_format_github_activity(profile), '')

    def test_empty_signals_returns_empty(self):
        profile = make_profile()  # data_content={}
        self.assertEqual(_format_github_activity(profile), '')

    def test_error_snapshot_returns_empty(self):
        profile = make_profile(github_signals={'error': 'rate limited', 'username': 'x'})
        self.assertEqual(_format_github_activity(profile), '')

    def test_full_snapshot_includes_header_languages_and_repos(self):
        profile = make_profile(github_signals=self.SAMPLE_SNAPSHOT)
        block = _format_github_activity(profile)

        self.assertIn('GITHUB ACTIVITY', block)
        self.assertIn('@octocat', block)
        self.assertIn('12 public repos', block)
        self.assertIn('247 total stars', block)
        self.assertIn('47 commits in last 90 days', block)
        self.assertIn('Python (8 repos)', block)
        self.assertIn('TypeScript (3 repos)', block)
        self.assertIn('ml-pipeline', block)
        self.assertIn('120\u2605', block)
        self.assertIn('Distributed training harness', block)

    def test_repo_descriptions_are_truncated(self):
        long_desc = 'x' * 500
        snap = dict(self.SAMPLE_SNAPSHOT)
        snap['top_repos'] = [{'name': 'big', 'language': 'Go', 'stars': 1, 'description': long_desc}]
        profile = make_profile(github_signals=snap)
        block = _format_github_activity(profile)
        self.assertNotIn(long_desc, block)
        self.assertIn('x' * 160, block)

    def test_full_context_appends_github_block(self):
        profile = make_profile(skills=['Python'], github_signals=self.SAMPLE_SNAPSHOT)
        ctx = _build_full_candidate_context(profile)
        self.assertIn('CANDIDATE SKILLS', ctx)
        self.assertIn('GITHUB ACTIVITY', ctx)
        self.assertLess(ctx.index('CANDIDATE SKILLS'), ctx.index('GITHUB ACTIVITY'))

    def test_full_context_skips_github_when_absent(self):
        profile = make_profile(skills=['Python'])
        ctx = _build_full_candidate_context(profile)
        self.assertNotIn('GITHUB ACTIVITY', ctx)



# ============================================================
# Scholar / Kaggle activity context blocks
# ============================================================

from analysis.services.gap_analyzer import (
    _format_scholar_activity,
    _format_kaggle_activity,
)


def _profile_with(**signals):
    """Build a stub profile with arbitrary signal blocks pre-cached."""
    return SimpleNamespace(
        skills=[], experiences=[], projects=[], certifications=[], education=[],
        data_content=signals,
    )


class FormatScholarActivityTests(SimpleTestCase):
    SAMPLE = {
        "user_id": "ABC123",
        "name": "Dr Octocat",
        "affiliation": "Stripe Research",
        "total_citations": 1234,
        "h_index": 42,
        "i10_index": 88,
        "top_publications": [
            {"title": "Distributed training", "venue": "NeurIPS", "year": "2024", "citations": 300},
            {"title": "PySpark optimization", "venue": "VLDB",   "year": "2023", "citations": 120},
        ],
    }

    def test_no_signals_returns_empty(self):
        self.assertEqual(_format_scholar_activity(_profile_with()), "")

    def test_error_snapshot_returns_empty(self):
        p = _profile_with(scholar_signals={"error": "boom", "user_id": "X"})
        self.assertEqual(_format_scholar_activity(p), "")

    def test_full_snapshot_renders_block(self):
        block = _format_scholar_activity(_profile_with(scholar_signals=self.SAMPLE))
        self.assertIn("GOOGLE SCHOLAR", block)
        self.assertIn("Dr Octocat", block)
        self.assertIn("Stripe Research", block)
        self.assertIn("Citations: 1234 total", block)
        self.assertIn("h-index: 42", block)
        self.assertIn("i10: 88", block)
        self.assertIn("Distributed training", block)
        self.assertIn("NeurIPS", block)
        self.assertIn("300 citations", block)


class FormatKaggleActivityTests(SimpleTestCase):
    SAMPLE = {
        "username": "octocat",
        "display_name": "Octo Cat",
        "overall_tier": "Master",
        "competitions": {"count": 12, "tier": "Master",
                         "medals": {"gold": 1, "silver": 3, "bronze": 5}},
        "datasets":     {"count": 4, "tier": "Contributor",
                         "medals": {"gold": 0, "silver": 1, "bronze": 2}},
        "notebooks":    {"count": 30, "tier": "Master",
                         "medals": {"gold": 2, "silver": 5, "bronze": 8}},
        "discussion":   {"count": 0, "tier": None,
                         "medals": {"gold": 0, "silver": 0, "bronze": 0}},
    }

    def test_no_signals_returns_empty(self):
        self.assertEqual(_format_kaggle_activity(_profile_with()), "")

    def test_error_snapshot_returns_empty(self):
        p = _profile_with(kaggle_signals={"error": "blocked", "username": "x"})
        self.assertEqual(_format_kaggle_activity(p), "")

    def test_full_snapshot_renders_block(self):
        block = _format_kaggle_activity(_profile_with(kaggle_signals=self.SAMPLE))
        self.assertIn("KAGGLE", block)
        self.assertIn("Octo Cat", block)
        self.assertIn("Master", block)  # overall tier
        self.assertIn("Competitions: 12", block)
        self.assertIn("Notebooks: 30", block)
        self.assertIn("Datasets: 4", block)
        # medals shown when category has any
        self.assertIn("medals", block)
        # Discussion has 0 count + no medals — should be skipped
        self.assertNotIn("Discussion: 0", block)


class ComputeMatchScoreTests(SimpleTestCase):
    """Formula used by analysis/services/skill_score.compute_match_score.

    Must stay in lockstep with the JS implementation in
    templates/analysis/gap_analysis.html (the `computedScore` getter on
    skillDragDrop) so the live-updated % doesn't drift from what the
    server persists to GapAnalysis.similarity_score.
    """

    def _score(self, m, mi, s):
        from analysis.services.skill_score import compute_match_score
        return compute_match_score(m, mi, s)

    def test_all_zero_returns_zero(self):
        self.assertEqual(self._score(0, 0, 0), 0.0)

    def test_all_matched_returns_one(self):
        self.assertEqual(self._score(5, 0, 0), 1.0)

    def test_all_missing_returns_zero(self):
        self.assertEqual(self._score(0, 5, 0), 0.0)

    def test_all_soft_returns_half(self):
        self.assertEqual(self._score(0, 0, 5), 0.5)

    def test_matched_and_missing_split_evenly(self):
        self.assertEqual(self._score(5, 5, 0), 0.5)

    def test_soft_weighted_half(self):
        # 2 matched + 0 missing + 2 soft = (2 + 1) / 4 = 0.75
        self.assertEqual(self._score(2, 0, 2), 0.75)

    def test_moving_missing_to_matched_increases_score(self):
        before = self._score(2, 3, 0)   # 2/5 = 0.4
        after  = self._score(3, 2, 0)   # 3/5 = 0.6
        self.assertGreater(after, before)
        self.assertAlmostEqual(after - before, 0.2, places=4)

    def test_moving_matched_to_missing_decreases_score(self):
        before = self._score(3, 2, 0)
        after  = self._score(2, 3, 0)
        self.assertLess(after, before)


class UpdateGapSkillsRecomputesScoreTests(TestCase):
    """POSTing skill buckets must recompute similarity_score on the server so
    a page reload shows the same % the user just saw live in the Alpine UI.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from jobs.models import Job
        from analysis.models import GapAnalysis
        User = get_user_model()
        self.user = User.objects.create_user(
            username='gap@example.com', email='gap@example.com', password='x',
        )
        self.client.force_login(self.user)
        self.job = Job.objects.create(user=self.user, title='Data Scientist')
        self.gap = GapAnalysis.objects.create(
            job=self.job, user=self.user,
            similarity_score=0.30,
            matched_skills=['Python'],
            missing_skills=['SQL', 'Airflow'],
            partial_skills=['Communication'],
        )

    def test_drag_missing_to_matched_raises_persisted_score(self):
        from django.urls import reverse
        import json as _json
        resp = self.client.post(
            reverse('update_gap_skills', args=[self.job.id]),
            data=_json.dumps({
                'matched_skills': ['Python', 'SQL', 'Airflow'],
                'missing_skills': [],
                'soft_skill_gaps': ['Communication'],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # (3 + 0.5*1) / 4 = 0.875
        self.assertAlmostEqual(body['similarity_score'], 0.875, places=3)
        self.gap.refresh_from_db()
        self.assertAlmostEqual(self.gap.similarity_score, 0.875, places=3)

    def test_empty_buckets_preserve_old_score(self):
        """Never clobber the LLM-computed baseline with a meaningless 0 just
        because the user cleared every bucket."""
        from django.urls import reverse
        import json as _json
        resp = self.client.post(
            reverse('update_gap_skills', args=[self.job.id]),
            data=_json.dumps({
                'matched_skills': [],
                'missing_skills': [],
                'soft_skill_gaps': [],
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.gap.refresh_from_db()
        self.assertAlmostEqual(self.gap.similarity_score, 0.30, places=3)
