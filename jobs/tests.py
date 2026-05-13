"""Tests for jobs/services helpers that don't need an LLM round-trip.

The big win is the post-LLM filter in skill_extractor — every patched
fixture below corresponds to a real hallucination observed in
benchmarks/results/2026-04-25/skill_extractor_eval.json. End-to-end
extract_skills() behavior is measured by benchmarks/skill_extractor_eval.py.
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from jobs.services.skill_extractor import (
    _GENERIC_SOFT_SKILL_DENYLIST,
    _is_jd_anchored,
    extract_skills,
)


class IsJdAnchoredTests(SimpleTestCase):
    def test_full_substring_match_passes(self):
        self.assertTrue(_is_jd_anchored("PostgreSQL", "we use postgresql for storage"))
        self.assertTrue(_is_jd_anchored("AWS", "deploy on aws and gcp"))

    def test_skill_lowercased_internally(self):
        # Contract: caller passes a pre-lowercased JD; helper lowercases the
        # skill name itself so canonical-cased extractions still match.
        self.assertTrue(_is_jd_anchored("TypeScript", "strong typescript fluency required"))

    def test_boilerplate_suffix_stripped(self):
        # "REST API" should match a JD that only says "REST".
        self.assertTrue(_is_jd_anchored("REST API", "design rest endpoints for clients"))
        # "CI/CD pipelines" should match a JD that says "CI/CD".
        self.assertTrue(_is_jd_anchored("CI/CD pipelines", "we run a ci/cd workflow"))

    def test_multi_word_canonical_passes_when_all_words_present(self):
        # "Tailwind CSS" canonicalised; JD just says "Tailwind" + "CSS" elsewhere.
        self.assertTrue(_is_jd_anchored("Tailwind CSS", "tailwind for styling and css basics"))

    def test_pure_invention_rejected(self):
        # Real benchmark hits — none of these tokens appear in the JD text.
        jd = "build mobile apps with flutter and dart"
        self.assertFalse(_is_jd_anchored("Pairing Sessions", jd))
        self.assertFalse(_is_jd_anchored("Bundle Analysis", jd))

    def test_short_words_dont_falsely_anchor(self):
        # "Go" is a programming language but a 2-letter word — anchoring should
        # not pass on incidental matches like "to go to".
        # (Word-anchoring requires len > 2, so "Go" must match as substring.)
        self.assertTrue(_is_jd_anchored("Go", "we use go and rust"))
        # But "Go" should NOT match if the JD never mentions it.
        self.assertFalse(_is_jd_anchored("Go", "we use python and rust"))

    def test_empty_input(self):
        self.assertFalse(_is_jd_anchored("", "anything"))
        self.assertFalse(_is_jd_anchored("   ", "anything"))


class ExtractSkillsFilterTests(SimpleTestCase):
    """End-to-end behavior of extract_skills with the LLM mocked."""

    def _mock_llm(self, returned_skills):
        """Build a fake structured LLM whose .invoke() returns a
        JobExtractionResult — the v2 contract — with all skills in the
        must-have tier. extract_skills() will collapse that to a flat list."""
        from profiles.services.schemas import JobExtractionResult
        result = JobExtractionResult(
            must_have_skills=list(returned_skills),
            nice_to_have_skills=[],
            domain="",
        )

        class _StructuredLLM:
            def invoke(self, _prompt):
                return result

        return _StructuredLLM()

    def test_denylisted_soft_skills_dropped_when_absent_from_jd(self):
        """The single biggest hallucination class: LLM adds 'Technical Leadership'
        and 'Problem Solving' on senior-ish JDs even when not mentioned."""
        with patch("jobs.services.skill_extractor.get_structured_llm",
                   return_value=self._mock_llm([
                       "Python", "Django", "Technical Leadership", "Problem Solving",
                   ])):
            out = extract_skills("Backend role with Python and Django.")
        self.assertEqual(out, ["Python", "Django"])

    def test_denylisted_term_kept_when_jd_uses_it_verbatim(self):
        """If the JD literally mentions 'code review', it's a real requirement."""
        with patch("jobs.services.skill_extractor.get_structured_llm",
                   return_value=self._mock_llm(["Python", "Code Review"])):
            out = extract_skills("Python role; participate in daily code review.")
        self.assertIn("Code Review", out)

    def test_unanchored_skills_dropped(self):
        """Real hallucinations like 'Pairing Sessions' on the senior frontend JD."""
        with patch("jobs.services.skill_extractor.get_structured_llm",
                   return_value=self._mock_llm([
                       "React", "TypeScript", "Pairing Sessions", "Bundle Analysis",
                   ])):
            out = extract_skills("React/TypeScript role with strong testing.")
        self.assertEqual(out, ["React", "TypeScript"])

    def test_canonical_mapping_preserved_when_words_present(self):
        """'Tailwind CSS' should survive when JD mentions 'Tailwind' and 'CSS'."""
        with patch("jobs.services.skill_extractor.get_structured_llm",
                   return_value=self._mock_llm(["Tailwind CSS"])):
            out = extract_skills("Style with Tailwind on top of CSS.")
        self.assertEqual(out, ["Tailwind CSS"])

    def test_empty_text_short_circuits_without_llm_call(self):
        # The early return shouldn't even instantiate the LLM client.
        with patch("jobs.services.skill_extractor.get_structured_llm",
                   side_effect=AssertionError("LLM must not be called for empty input")):
            self.assertEqual(extract_skills(""), [])
            self.assertEqual(extract_skills(None), [])

    def test_llm_failure_returns_empty_list(self):
        def _raise(*_a, **_kw):
            raise RuntimeError("Groq down")
        with patch("jobs.services.skill_extractor.get_structured_llm", side_effect=_raise):
            self.assertEqual(extract_skills("Any JD."), [])


class GenericSoftSkillDenylistTests(SimpleTestCase):
    """Pin the denylist contents — each entry was an actual benchmark hallucination."""

    def test_known_hallucinated_phrases_are_listed(self):
        # v2 (2026-05-14): leadership/communication/collaboration/mentorship
        # removed from the unconditional denylist — they're now legitimate
        # when JD-anchored. What stays here is the pattern set that the LLM
        # hallucinates regardless of context.
        for phrase in ("technical leadership", "problem solving", "pairing sessions",
                       "code review", "teamwork", "pair programming"):
            self.assertIn(phrase, _GENERIC_SOFT_SKILL_DENYLIST)

    def test_v2_soft_skills_removed_from_denylist(self):
        # These are kept by the v2 extractor when verbatim in the JD, so they
        # must NOT live on the unconditional denylist.
        for phrase in ("leadership", "communication", "collaboration", "mentorship"):
            self.assertNotIn(phrase, _GENERIC_SOFT_SKILL_DENYLIST)

    def test_real_technical_skills_are_not_listed(self):
        # Sanity: the denylist must not accidentally cover real technical skills.
        for technical in ("python", "react", "kubernetes", "postgresql", "aws"):
            self.assertNotIn(technical, _GENERIC_SOFT_SKILL_DENYLIST)


# ===============================================================
# Tests for the recommended-jobs feature: scoring, dedup, URL normalization.
# ===============================================================

from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from jobs.models import JobListing, RecommendedJob, ScrapeJob
from jobs.services.url_normalizer import normalize_url


class UrlNormalizerTests(TestCase):
    def test_indeed_keeps_jk_drops_rest(self):
        self.assertEqual(
            normalize_url("https://www.indeed.com/viewjob?jk=abc123&from=junk&tk=foo"),
            "https://www.indeed.com/viewjob?jk=abc123",
        )

    def test_glassdoor_keeps_jl_drops_rest(self):
        self.assertEqual(
            normalize_url("https://www.glassdoor.com/job-listing/foo-IC123?jl=999&srs=trk&pos=2"),
            "https://www.glassdoor.com/job-listing/foo-IC123?jl=999",
        )

    def test_default_strips_query_and_trailing_slash(self):
        self.assertEqual(
            normalize_url("https://example.com/job/x/?utm_source=foo#frag"),
            "https://example.com/job/x",
        )

    def test_blank_input_returns_blank(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url("not-a-url"), "not-a-url")


class JobScoringTests(TestCase):
    """Exercises the scoring pipeline with the gap analyzer mocked out so
    we don't burn LLM tokens in CI. Verifies the dedup-with-status-preservation
    contract that protects user-set saved/dismissed flags."""

    def setUp(self):
        from profiles.models import UserProfile
        User = get_user_model()
        self.user = User.objects.create_user(username="scoring@test", email="scoring@test", password="x")
        self.profile = UserProfile.objects.create(
            user=self.user,
            data_content={
                "skills": [{"name": "Python"}, {"name": "Django"}],
                "experiences": [{"title": "Backend Engineer", "company": "Acme"}],
                "summary": "Backend engineer with Django experience.",
            },
        )
        self.scrape_job = ScrapeJob.objects.create(
            user=self.user,
            params_json={"keyword": "Backend"},
            status=ScrapeJob.STATUS_RUNNING,
        )

    def _make_listing(self, *, title, url, description="Build APIs in Python and Django."):
        return JobListing.objects.create(
            scrape_job=self.scrape_job,
            source="LinkedIn",
            title=title,
            company="Acme",
            url=url,
            description=description,
            raw_text=description,
            unique_hash=str(uuid4()),
        )

    @patch("jobs.services.job_scoring.compute_gap_analysis")
    @patch("jobs.services.job_scoring.extract_skills", return_value=["Python", "Django"])
    def test_top_k_persisted_with_scores(self, _extract, gap):
        gap.side_effect = [
            {"similarity_score": 0.92},
            {"similarity_score": 0.71},
            {"similarity_score": 0.30},
        ]
        self._make_listing(title="Senior Python Backend Engineer", url="https://l/i/1")
        self._make_listing(title="Mid-level Django Engineer",     url="https://l/i/2")
        self._make_listing(title="Off-topic Sales Manager",        url="https://l/i/3")

        from jobs.services.job_scoring import score_listings_for_user
        n = score_listings_for_user(self.user.id, self.scrape_job.id, top_k=3)

        recs = list(RecommendedJob.objects.filter(user=self.user).order_by("-match_score"))
        self.assertEqual(n, 3)
        self.assertEqual([r.match_score for r in recs], [92, 71, 30])
        self.assertEqual(set(r.status for r in recs), {"new"})

    @patch("jobs.services.job_scoring.compute_gap_analysis")
    @patch("jobs.services.job_scoring.extract_skills", return_value=["Python"])
    def test_dedup_preserves_user_status(self, _extract, gap):
        gap.return_value = {"similarity_score": 0.9}

        # User dismissed this URL on a previous scan.
        from jobs.services.url_normalizer import normalize_url
        normed = normalize_url("https://l/i/dismissed")
        RecommendedJob.objects.create(
            user=self.user,
            url=normed,
            title="old title",
            company="old co",
            description="old",
            match_score=10,
            status="dismissed",
        )
        # Same URL re-emerges from a new scrape.
        self._make_listing(title="Senior Backend Engineer", url="https://l/i/dismissed")

        from jobs.services.job_scoring import score_listings_for_user
        score_listings_for_user(self.user.id, self.scrape_job.id, top_k=5)

        rec = RecommendedJob.objects.get(user=self.user, url=normed)
        self.assertEqual(rec.status, "dismissed", "user dismissal must not be reset")
        # Score + metadata DO refresh.
        self.assertEqual(rec.match_score, 90)
        self.assertEqual(rec.title, "Senior Backend Engineer")

    @patch("jobs.services.job_scoring.compute_gap_analysis")
    @patch("jobs.services.job_scoring.extract_skills", return_value=[])
    def test_no_listings_returns_zero(self, _extract, _gap):
        from jobs.services.job_scoring import score_listings_for_user
        n = score_listings_for_user(self.user.id, self.scrape_job.id)
        self.assertEqual(n, 0)
        self.assertEqual(RecommendedJob.objects.filter(user=self.user).count(), 0)


class JobPreferencesFormSeedTests(TestCase):
    """Verifies the auto-seed-from-CV behaviour for fresh JobPreferences."""

    def test_seed_pulls_latest_role_and_location(self):
        from profiles.forms import seed_defaults_from_profile
        from profiles.models import JobPreferences, UserProfile
        User = get_user_model()
        user = User.objects.create_user(username="seed@test", email="seed@test", password="x")
        profile = UserProfile.objects.create(
            user=user,
            location="Berlin",
            data_content={
                "experiences": [
                    {"title": "Backend Engineer", "company": "Acme"},
                    {"title": "Junior Dev", "company": "Old"},
                ],
                "skills": [{"name": "Python"}],
            },
        )
        prefs = JobPreferences(user=user)
        seed_defaults_from_profile(prefs, profile)
        self.assertEqual(prefs.keyword, "Backend Engineer")
        self.assertEqual(prefs.locations, ["Berlin"])
        self.assertEqual(prefs.sources, ["linkedin"])

    def test_seed_falls_back_to_remote_when_no_location(self):
        from profiles.forms import seed_defaults_from_profile
        from profiles.models import JobPreferences, UserProfile
        User = get_user_model()
        user = User.objects.create_user(username="seed2@test", email="seed2@test", password="x")
        profile = UserProfile.objects.create(user=user, data_content={"experiences": []})
        prefs = JobPreferences(user=user)
        seed_defaults_from_profile(prefs, profile)
        self.assertEqual(prefs.locations, ["Remote"])
        self.assertIn("remote", prefs.workplace_types)


class PreferenceSuggesterCleanupTests(TestCase):
    """Defensive post-processing of LLM-suggested keywords. Catches the
    'Internet of Things (IoT) Internship' shape that echoed a past role title."""

    def test_strips_parens_and_seniority(self):
        from profiles.services.preference_suggester import _clean_keyword
        self.assertEqual(_clean_keyword("Internet of Things (IoT) Internship"), "Internet of Things")

    def test_strips_seniority_prefix(self):
        from profiles.services.preference_suggester import _clean_keyword
        self.assertEqual(_clean_keyword("Senior Backend Engineer"), "Backend Engineer")

    def test_already_clean_keyword_unchanged(self):
        from profiles.services.preference_suggester import _clean_keyword
        self.assertEqual(_clean_keyword("Data Scientist"), "Data Scientist")

    def test_caps_at_four_words(self):
        from profiles.services.preference_suggester import _clean_keyword
        self.assertEqual(
            _clean_keyword("Backend Python Django REST API Engineer"),
            "Backend Python Django REST",
        )

    def test_handles_slash_alternatives(self):
        from profiles.services.preference_suggester import _clean_keyword
        self.assertEqual(_clean_keyword("Engineer / Developer"), "Engineer")
