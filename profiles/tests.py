"""Tests for profiles services.

Covers the deterministic text-processing stages (sanitization, regex-based
personal info, section-header detection, fuzzy matching) — the parts that
have been patched repeatedly (9b998a9, e6fce11, dd00e14). LLM refinement is
out of scope for these tests; get_llm_client is patched so no network/API
calls happen.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


def _make_extractor():
    """Construct a CVExtractor with the LLM client stubbed out."""
    with patch('profiles.services.cv_parser.get_llm_client', return_value=None):
        from profiles.services.cv_parser import CVExtractor
        return CVExtractor(use_llm=False)


class SanitizeTextLetterSpacingTests(SimpleTestCase):
    """PDF kerning often splits words; the repair must preserve original casing."""

    def setUp(self):
        self.ex = _make_extractor()

    def test_all_caps_letter_spaced_word_stays_all_caps(self):
        out = self.ex._sanitize_text("B ACH ELOR OF SCIEN CE")
        self.assertIn("BACHELOR", out)
        self.assertIn("SCIENCE", out)

    def test_title_case_letter_spaced_word_stays_title_case(self):
        out = self.ex._sanitize_text("Com puter Scien ce")
        self.assertIn("Computer", out)
        self.assertIn("Science", out)

    def test_lowercase_letter_spaced_word_stays_lowercase(self):
        out = self.ex._sanitize_text("com puter scien ce")
        self.assertIn("computer", out)
        self.assertIn("science", out)

    def test_non_spaced_words_are_untouched(self):
        out = self.ex._sanitize_text("BACHELOR of Science in Computer Science")
        self.assertIn("BACHELOR", out)
        self.assertIn("Science", out)


class SanitizeTextNoiseRemovalTests(SimpleTestCase):
    def setUp(self):
        self.ex = _make_extractor()

    def test_page_numbers_are_removed(self):
        out = self.ex._sanitize_text("Name\nPage 1 of 3\nSummary")
        self.assertNotIn("Page 1 of 3", out)
        self.assertIn("Name", out)
        self.assertIn("Summary", out)

    def test_confidential_marker_is_removed(self):
        out = self.ex._sanitize_text("Confidential\nJohn Doe")
        self.assertNotIn("Confidential", out)
        self.assertIn("John Doe", out)

    def test_excessive_newlines_are_collapsed(self):
        out = self.ex._sanitize_text("line1\n\n\n\n\nline2")
        self.assertNotIn("\n\n\n", out)
        self.assertIn("line1", out)
        self.assertIn("line2", out)

    def test_excess_horizontal_whitespace_collapses_to_single_space(self):
        out = self.ex._sanitize_text("word1     word2\t\tword3")
        self.assertEqual(out, "word1 word2 word3")


class FuzzyMatchTests(SimpleTestCase):
    def setUp(self):
        self.ex = _make_extractor()

    def test_exact_regex_match_returns_true(self):
        self.assertTrue(self.ex.fuzzy_match("summary", [r"^summary$"]))

    def test_loose_match_above_threshold_returns_true(self):
        # "worrk experience" vs pattern "work experience" — 1 typo
        self.assertTrue(
            self.ex.fuzzy_match("worrk experience", [r"^work\s+experience$"], threshold=0.8)
        )

    def test_unrelated_text_returns_false(self):
        self.assertFalse(
            self.ex.fuzzy_match("pineapple smoothie", [r"^experience$"], threshold=0.8)
        )


class FindSectionHeadersTests(SimpleTestCase):
    def setUp(self):
        self.ex = _make_extractor()

    def test_all_caps_header_is_detected(self):
        text = "EXPERIENCE\nSoftware Engineer at ACME"
        sections = self.ex.find_section_headers(text)
        self.assertEqual(sections['experience'], [0])

    def test_title_case_header_is_detected(self):
        text = "Name: Jane\n\nTechnical Skills\nPython, Django"
        sections = self.ex.find_section_headers(text)
        self.assertTrue(sections['skills'], "skills section should be detected")

    def test_regular_paragraph_is_not_a_header(self):
        text = "I worked on a really interesting project that taught me a lot."
        sections = self.ex.find_section_headers(text)
        # No section should register — all should be empty lists
        self.assertTrue(all(v == [] for v in sections.values()))


class ExtractPersonalInfoContactTests(SimpleTestCase):
    """Email/phone/URL extraction — pure regex."""

    def setUp(self):
        self.ex = _make_extractor()

    def test_email_is_extracted(self):
        info = self.ex.extract_personal_info("Contact: jane.doe@example.com")
        self.assertEqual(info['email'], 'jane.doe@example.com')

    def test_international_phone_with_plus_is_extracted(self):
        info = self.ex.extract_personal_info("Phone: +20 1234567890")
        self.assertIsNotNone(info['phone'])
        # Must retain enough digits to be usable
        digits = ''.join(c for c in info['phone'] if c.isdigit())
        self.assertGreaterEqual(len(digits), 10)

    def test_github_url_is_detected(self):
        info = self.ex.extract_personal_info("Links: https://github.com/janedoe")
        self.assertEqual(info['github'], 'https://github.com/janedoe')

    def test_linkedin_url_is_detected(self):
        info = self.ex.extract_personal_info("https://www.linkedin.com/in/janedoe/")
        self.assertTrue(info['linkedin'].startswith('https://www.linkedin.com/in/'))


class ExtractPersonalInfoNameTests(SimpleTestCase):
    """Name extraction is conservative — these are the regressions prior fixes targeted."""

    def setUp(self):
        self.ex = _make_extractor()

    def test_title_case_name_is_extracted(self):
        info = self.ex.extract_personal_info("Karen Santos\njane@example.com")
        self.assertEqual(info['name'], 'Karen Santos')

    def test_all_caps_name_is_titlecased(self):
        info = self.ex.extract_personal_info("JOHANN BACH\n+20 1234567890")
        self.assertEqual(info['name'], 'Johann Bach')

    def test_placeholder_first_last_is_rejected(self):
        info = self.ex.extract_personal_info("First Last\njane@example.com")
        self.assertIsNone(info['name'])

    def test_section_header_is_not_mistaken_for_name(self):
        info = self.ex.extract_personal_info("Work Experience\nSoftware Engineer at ACME")
        self.assertIsNone(info['name'])

    def test_job_title_is_not_mistaken_for_name(self):
        info = self.ex.extract_personal_info("Software Engineer\njane@example.com")
        self.assertIsNone(info['name'])

    def test_academic_field_is_not_mistaken_for_name(self):
        info = self.ex.extract_personal_info("Computer Science\njane@example.com")
        self.assertIsNone(info['name'])


class ExtractPersonalInfoLocationTests(SimpleTestCase):
    def setUp(self):
        self.ex = _make_extractor()

    def test_city_country_comma_pattern_is_extracted(self):
        info = self.ex.extract_personal_info("Jane Doe\nCairo, Egypt\njane@example.com")
        self.assertEqual(info['address'], 'Cairo, Egypt')

    def test_known_standalone_city_is_extracted(self):
        info = self.ex.extract_personal_info("Jane Doe\nCairo\njane@example.com")
        self.assertEqual(info['address'], 'Cairo')

    def test_random_single_word_is_not_treated_as_location(self):
        """Regression: a single capitalized word like a company shouldn't be picked as location."""
        info = self.ex.extract_personal_info("Jane Doe\nGoogle\njane@example.com")
        self.assertIsNone(info['address'])


# ============================================================
# GitHub aggregator
# ============================================================

from profiles.services.github_aggregator import (
    fetch_github_snapshot,
    parse_github_username,
)


class ParseGithubUsernameTests(SimpleTestCase):
    def test_https_url(self):
        self.assertEqual(parse_github_username("https://github.com/octocat"), "octocat")

    def test_http_url(self):
        self.assertEqual(parse_github_username("http://github.com/octocat"), "octocat")

    def test_www_subdomain(self):
        self.assertEqual(parse_github_username("https://www.github.com/octocat"), "octocat")

    def test_url_with_repo_path(self):
        self.assertEqual(parse_github_username("github.com/octocat/some-repo"), "octocat")

    def test_url_with_trailing_slash(self):
        self.assertEqual(parse_github_username("https://github.com/octocat/"), "octocat")

    def test_at_handle(self):
        self.assertEqual(parse_github_username("@octocat"), "octocat")

    def test_bare_username(self):
        self.assertEqual(parse_github_username("octocat"), "octocat")

    def test_username_with_hyphen(self):
        self.assertEqual(parse_github_username("octo-cat"), "octo-cat")

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_github_username(""))

    def test_none_returns_none(self):
        self.assertIsNone(parse_github_username(None))

    def test_non_github_url_returns_none(self):
        self.assertIsNone(parse_github_username("https://example.com/octocat"))

    def test_path_without_recognized_host_returns_none(self):
        self.assertIsNone(parse_github_username("/some/path"))

    def test_invalid_username_chars_return_none(self):
        # GitHub usernames can't contain underscores or special chars
        self.assertIsNone(parse_github_username("octo_cat"))

    def test_username_starting_with_hyphen_returns_none(self):
        self.assertIsNone(parse_github_username("-octocat"))


def _fake_response(json_data, status=200, ok=True):
    """Helper to build a minimal mocked requests.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.ok = ok
    resp.headers = {}
    resp.json.return_value = json_data
    resp.text = ""
    return resp


def _stub_session(routes):
    """Build a Session whose .get(url, ...) returns one of the configured
    responses based on a substring match in the URL."""
    session = MagicMock()
    def fake_get(url, params=None, timeout=None):
        for needle, response in routes.items():
            if needle in url:
                return response
        return _fake_response({}, status=404, ok=False)
    session.get.side_effect = fake_get
    return session


class FetchGithubSnapshotTests(SimpleTestCase):
    def test_invalid_input_returns_error_snapshot(self):
        snap = fetch_github_snapshot("https://example.com/notgithub")
        self.assertEqual(snap['username'], '')
        self.assertIn('Could not parse', snap['error'])
        self.assertEqual(snap['top_repos'], [])
        self.assertEqual(snap['language_breakdown'], [])

    def test_user_404_returns_error_snapshot(self):
        routes = {'/users/ghostuser': _fake_response(None, status=404, ok=False)}
        with patch('profiles.services.github_aggregator.requests.Session', return_value=_stub_session(routes)):
            snap = fetch_github_snapshot('ghostuser')
        self.assertEqual(snap['username'], 'ghostuser')
        self.assertIn('not found', snap['error'])

    def test_happy_path_extracts_top_repos_languages_stars(self):
        user_payload = {
            'name': 'Octo Cat', 'bio': 'Hi.', 'public_repos': 3,
            'followers': 100, 'following': 5, 'created_at': '2018-01-15T10:00:00Z',
        }
        repos_payload = [
            {'name': 'alpha', 'full_name': 'octocat/alpha', 'description': 'a',
             'html_url': 'https://github.com/octocat/alpha', 'stargazers_count': 50,
             'forks_count': 2, 'language': 'Python', 'pushed_at': '2026-04-01T00:00:00Z'},
            {'name': 'beta', 'full_name': 'octocat/beta', 'description': 'b',
             'html_url': 'https://github.com/octocat/beta', 'stargazers_count': 5,
             'forks_count': 0, 'language': 'Python', 'pushed_at': '2026-03-01T00:00:00Z'},
            {'name': 'gamma', 'full_name': 'octocat/gamma', 'description': 'c',
             'html_url': 'https://github.com/octocat/gamma', 'stargazers_count': 2,
             'forks_count': 0, 'language': 'TypeScript', 'pushed_at': '2026-02-01T00:00:00Z'},
            # A blocklisted language (Jupyter Notebook) — must not appear in breakdown
            {'name': 'notes', 'full_name': 'octocat/notes', 'description': '',
             'html_url': 'https://github.com/octocat/notes', 'stargazers_count': 0,
             'forks_count': 0, 'language': 'Jupyter Notebook', 'pushed_at': '2025-01-01T00:00:00Z'},
        ]
        # Recent push event within 90 days — should count toward recent_commits
        recent_iso = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace('+00:00', 'Z')
        old_iso = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat().replace('+00:00', 'Z')
        events_payload = [
            {'type': 'PushEvent', 'created_at': recent_iso, 'payload': {'size': 4}},
            {'type': 'PushEvent', 'created_at': recent_iso, 'payload': {'size': 1}},
            {'type': 'WatchEvent', 'created_at': recent_iso, 'payload': {}},  # not a push
            {'type': 'PushEvent', 'created_at': old_iso, 'payload': {'size': 99}},  # too old
        ]
        routes = {
            '/users/octocat/repos': _fake_response(repos_payload),
            '/users/octocat/events/public': _fake_response(events_payload),
            '/users/octocat': _fake_response(user_payload),
        }
        with patch('profiles.services.github_aggregator.requests.Session', return_value=_stub_session(routes)):
            snap = fetch_github_snapshot('octocat', top_n=3)

        self.assertIsNone(snap['error'])
        self.assertEqual(snap['username'], 'octocat')
        self.assertEqual(snap['name'], 'Octo Cat')
        self.assertEqual(snap['public_repos'], 3)
        self.assertEqual(snap['account_created'], '2018-01-15')
        self.assertEqual(snap['total_stars'], 57)
        # Top 3 repos sorted by stars
        self.assertEqual([r['name'] for r in snap['top_repos']], ['alpha', 'beta', 'gamma'])
        # Language breakdown counts only non-blocklisted languages
        langs = dict(snap['language_breakdown'])
        self.assertEqual(langs.get('Python'), 2)
        self.assertEqual(langs.get('TypeScript'), 1)
        self.assertNotIn('Jupyter Notebook', langs)
        # Recent commits = sum of PushEvent sizes within 90 days
        self.assertEqual(snap['recent_commit_count'], 5)





# ============================================================
# LinkedIn / Scholar / Kaggle aggregators
# ============================================================

import json as _json
from profiles.services.linkedin_aggregator import (
    make_linkedin_snapshot,
    parse_linkedin_handle,
)
from profiles.services.scholar_aggregator import (
    fetch_scholar_snapshot,
    parse_scholar_user_id,
)
from profiles.services.kaggle_aggregator import (
    fetch_kaggle_snapshot,
    parse_kaggle_username,
)


# ---- LinkedIn -------------------------------------------------

class ParseLinkedinHandleTests(SimpleTestCase):
    def test_full_url(self):
        self.assertEqual(parse_linkedin_handle("https://www.linkedin.com/in/jane-doe-123"), "jane-doe-123")

    def test_url_with_country_subdomain(self):
        self.assertEqual(parse_linkedin_handle("https://uk.linkedin.com/in/jane-doe"), "jane-doe")

    def test_url_with_trailing_slash(self):
        self.assertEqual(parse_linkedin_handle("linkedin.com/in/jane-doe-123/"), "jane-doe-123")

    def test_in_handle_form(self):
        self.assertEqual(parse_linkedin_handle("in/jane-doe-123"), "jane-doe-123")

    def test_bare_handle(self):
        self.assertEqual(parse_linkedin_handle("jane-doe-123"), "jane-doe-123")

    def test_foreign_url_returns_none(self):
        self.assertIsNone(parse_linkedin_handle("https://example.com/in/jane"))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_linkedin_handle(""))


class MakeLinkedinSnapshotTests(SimpleTestCase):
    def test_valid_url_builds_canonical_snapshot(self):
        snap = make_linkedin_snapshot("https://www.linkedin.com/in/jane-doe")
        self.assertIsNone(snap["error"])
        self.assertEqual(snap["username"], "jane-doe")
        self.assertEqual(snap["profile_url"], "https://www.linkedin.com/in/jane-doe/")

    def test_invalid_input_returns_error(self):
        snap = make_linkedin_snapshot("https://example.com/something")
        self.assertIn("Couldn", snap["error"])
        self.assertEqual(snap["username"], "")


# ---- Scholar --------------------------------------------------

class ParseScholarUserIdTests(SimpleTestCase):
    def test_full_url_with_hl(self):
        self.assertEqual(parse_scholar_user_id("https://scholar.google.com/citations?user=ABC123XY&hl=en"), "ABC123XY")

    def test_url_without_hl(self):
        self.assertEqual(parse_scholar_user_id("scholar.google.com/citations?user=ABC123XY"), "ABC123XY")

    def test_bare_id(self):
        self.assertEqual(parse_scholar_user_id("ABC123XY"), "ABC123XY")

    def test_foreign_url_returns_none(self):
        self.assertIsNone(parse_scholar_user_id("https://example.com/?user=NOPE"))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_scholar_user_id(""))

    def test_too_short_id_returns_none(self):
        self.assertIsNone(parse_scholar_user_id("abc"))


class FetchScholarSnapshotTests(SimpleTestCase):
    def test_invalid_input_returns_error_snapshot(self):
        snap = fetch_scholar_snapshot("https://example.com/notscholar")
        self.assertIn("Could not parse", snap["error"])

    def test_captcha_interstitial_returns_error_snapshot(self):
        resp = MagicMock()
        resp.url = "https://scholar.google.com/sorry/index?q=CAPTCHA"
        resp.ok = True
        resp.status_code = 200
        resp.text = "Please show you are not a robot - unusual traffic detected"
        with patch("profiles.services.scholar_aggregator.requests.get", return_value=resp):
            snap = fetch_scholar_snapshot("ABC123XY")
        self.assertIsNotNone(snap["error"])
        self.assertIn("CAPTCHA", snap["error"])

    def test_happy_path_extracts_name_citations_and_pubs(self):
        html = """
        <html><body>
            <div id="gsc_prf_in">Dr Octocat</div>
            <div id="gsc_prf_i"><div class="gsc_prf_il">Stripe Research</div></div>
            <table id="gsc_rsb_st">
                <tr><td class="gsc_rsb_std">1234</td><td class="gsc_rsb_std">800</td></tr>
                <tr><td class="gsc_rsb_std">42</td><td class="gsc_rsb_std">35</td></tr>
                <tr><td class="gsc_rsb_std">88</td><td class="gsc_rsb_std">70</td></tr>
            </table>
            <table>
              <tr class="gsc_a_tr">
                <td><a class="gsc_a_at">Distributed training</a><div class="gs_gray">co-author</div><div class="gs_gray">NeurIPS</div></td>
                <td><a class="gsc_a_ac">300</a></td>
                <td class="gsc_a_y"><span>2024</span></td>
              </tr>
              <tr class="gsc_a_tr">
                <td><a class="gsc_a_at">PySpark optimization</a><div class="gs_gray">a</div><div class="gs_gray">VLDB</div></td>
                <td><a class="gsc_a_ac">120</a></td>
                <td class="gsc_a_y"><span>2023</span></td>
              </tr>
            </table>
        </body></html>
        """
        resp = MagicMock()
        resp.url = "https://scholar.google.com/citations?user=ABC123XY"
        resp.ok = True
        resp.status_code = 200
        resp.text = html
        with patch("profiles.services.scholar_aggregator.requests.get", return_value=resp):
            snap = fetch_scholar_snapshot("ABC123XY")
        self.assertIsNone(snap["error"])
        self.assertEqual(snap["name"], "Dr Octocat")
        self.assertEqual(snap["affiliation"], "Stripe Research")
        self.assertEqual(snap["total_citations"], 1234)
        self.assertEqual(snap["h_index"], 42)
        self.assertEqual(snap["i10_index"], 88)
        self.assertEqual(len(snap["top_publications"]), 2)
        self.assertEqual(snap["top_publications"][0]["title"], "Distributed training")
        self.assertEqual(snap["top_publications"][0]["venue"], "NeurIPS")
        self.assertEqual(snap["top_publications"][0]["year"], "2024")
        self.assertEqual(snap["top_publications"][0]["citations"], 300)


# ---- Kaggle ---------------------------------------------------

class ParseKaggleUsernameTests(SimpleTestCase):
    def test_full_url(self):
        self.assertEqual(parse_kaggle_username("https://www.kaggle.com/octocat"), "octocat")

    def test_url_with_subpath(self):
        self.assertEqual(parse_kaggle_username("kaggle.com/octocat/competitions"), "octocat")

    def test_bare_username(self):
        self.assertEqual(parse_kaggle_username("octocat"), "octocat")

    def test_foreign_url_returns_none(self):
        self.assertIsNone(parse_kaggle_username("https://example.com/octocat"))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_kaggle_username(""))


class FetchKaggleSnapshotTests(SimpleTestCase):
    def test_invalid_input_returns_error_snapshot(self):
        snap = fetch_kaggle_snapshot("https://example.com/octocat")
        self.assertIn("Could not parse", snap["error"])

    def test_happy_path_parses_next_data(self):
        next_data = {
            "props": {"pageProps": {"userProfile": {
                "userName": "octocat",
                "displayName": "Octo Cat",
                "performanceTier": "Expert",
                "competitionsCount": 12, "competitionsTier": "Master",
                "competitionsMedals": {"gold": 1, "silver": 3, "bronze": 5},
                "datasetsCount": 4, "datasetsTier": "Contributor",
                "datasetsMedals": {"gold": 0, "silver": 1, "bronze": 2},
                "kernelsCount": 30, "kernelsTier": "Master",
                "kernelsMedals": {"gold": 2, "silver": 5, "bronze": 8},
                "discussionCount": 50, "discussionTier": "Contributor",
                "discussionMedals": {"gold": 0, "silver": 0, "bronze": 1},
                "followersCount": 200,
            }}}
        }
        # Use double quotes inside the embedded script tag to avoid the apostrophe issue.
        html = (
            "<html><body>"
            "<script id=\"__NEXT_DATA__\" type=\"application/json\">"
            + _json.dumps(next_data) +
            "</script></body></html>"
        )
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = html
        with patch("profiles.services.kaggle_aggregator.requests.get", return_value=resp):
            snap = fetch_kaggle_snapshot("octocat")
        self.assertIsNone(snap["error"])
        self.assertEqual(snap["username"], "octocat")
        self.assertEqual(snap["display_name"], "Octo Cat")
        self.assertEqual(snap["overall_tier"], "Expert")
        self.assertEqual(snap["competitions"]["count"], 12)
        self.assertEqual(snap["competitions"]["tier"], "Master")
        self.assertEqual(snap["competitions"]["medals"]["gold"], 1)
        self.assertEqual(snap["notebooks"]["count"], 30)
        self.assertEqual(snap["datasets"]["count"], 4)
        self.assertEqual(snap["discussion"]["count"], 50)
        self.assertEqual(snap["followers"], 200)

    def test_missing_next_data_returns_error(self):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "<html></html>"
        with patch("profiles.services.kaggle_aggregator.requests.get", return_value=resp):
            snap = fetch_kaggle_snapshot("octocat")
        self.assertIn("__NEXT_DATA__", snap["error"])


# ============================================================
# Profile strength scoring
# ============================================================

from django.test import TestCase
from django.contrib.auth import get_user_model


class ProfileStrengthTests(TestCase):
    """compute_profile_strength — see spec 2026-04-15-profile-strength-scoring-design.md."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ps@example.com', email='ps@example.com', password='x'
        )

    def _make_profile(self, **overrides):
        from profiles.models import UserProfile
        defaults = dict(user=self.user, full_name='', email='', data_content={})
        defaults.update(overrides)
        return UserProfile.objects.create(**defaults)

    def test_module_exports_compute_profile_strength(self):
        from profiles.services.profile_strength import compute_profile_strength
        self.assertTrue(callable(compute_profile_strength))

    def test_href_map_covers_every_item_key(self):
        from profiles.services.profile_strength import HREF_BY_KEY
        expected_keys = {
            'has_identity', 'has_three_exps', 'has_education', 'has_five_skills',
            'has_summary', 'has_location_phone',
            'descriptions_rich', 'has_project', 'has_credential', 'descriptions_metric',
            'github_connected', 'scholar_or_kaggle', 'has_linkedin', 'signals_fresh',
        }
        self.assertEqual(set(HREF_BY_KEY.keys()), expected_keys)

    def test_completeness_empty_profile_scores_zero(self):
        from profiles.services.profile_strength import _score_completeness
        profile = self._make_profile()
        c = _score_completeness(profile)
        self.assertEqual(c['key'], 'completeness')
        self.assertEqual(c['max'], 35)
        self.assertEqual(c['score'], 0)
        self.assertTrue(all(not i['met'] for i in c['items']))

    def test_completeness_full_profile_scores_max(self):
        from profiles.services.profile_strength import _score_completeness
        profile = self._make_profile(
            full_name='Jane Doe', email='j@example.com',
            location='Cairo', phone='+20 100 000',
            data_content={
                'summary': 'x' * 50,
                'skills': [{'name': s} for s in ['Python', 'Go', 'SQL', 'React', 'Django']],
                'experiences': [
                    {'title': 'A', 'description': 'Did stuff.'},
                    {'title': 'B', 'description': 'More stuff.'},
                    {'title': 'C', 'description': 'Even more.'},
                ],
                'education': [{'degree': 'BSc', 'institution': 'KSIU'}],
            },
        )
        c = _score_completeness(profile)
        self.assertEqual(c['score'], 35)
        self.assertTrue(all(i['met'] for i in c['items']))

    def test_completeness_partial_only_counts_met_items(self):
        from profiles.services.profile_strength import _score_completeness
        profile = self._make_profile(
            full_name='Jane', email='j@example.com',
            data_content={'skills': [{'name': s} for s in ['Python', 'Go', 'SQL', 'React', 'Django']]},
        )
        c = _score_completeness(profile)
        # identity (5) + skills (5) = 10
        self.assertEqual(c['score'], 10)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['has_identity'])
        self.assertTrue(met['has_five_skills'])
        self.assertFalse(met['has_three_exps'])
        self.assertFalse(met['has_education'])
        self.assertFalse(met['has_summary'])
        self.assertFalse(met['has_location_phone'])

    def test_evidence_empty_profile_scores_zero(self):
        from profiles.services.profile_strength import _score_evidence
        profile = self._make_profile()
        c = _score_evidence(profile)
        self.assertEqual(c['key'], 'evidence')
        self.assertEqual(c['max'], 30)
        self.assertEqual(c['score'], 0)

    def test_evidence_full_scores_max(self):
        from profiles.services.profile_strength import _score_evidence
        long_desc = 'Led a team to deliver 30% faster throughput across 5 services.' * 3
        profile = self._make_profile(
            data_content={
                'experiences': [
                    {'description': long_desc},
                    {'description': long_desc},
                    {'description': long_desc},
                ],
                'projects': [{'name': 'X', 'description': 'Built a thing.'}],
                'certifications': [{'name': 'AWS SAA'}],
            },
        )
        c = _score_evidence(profile)
        self.assertEqual(c['score'], 30)

    def test_evidence_descriptions_metric_requires_digit(self):
        from profiles.services.profile_strength import _score_evidence
        from profiles.models import UserProfile

        profile_with = self._make_profile(
            data_content={
                'experiences': [{'description': 'Improved throughput by 30% and cut latency.'}],
            },
        )
        c = _score_evidence(profile_with)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['descriptions_metric'])

        u2 = get_user_model().objects.create_user(
            username='b@example.com', email='b@example.com', password='x'
        )
        profile_without = UserProfile.objects.create(
            user=u2, full_name='B', email='b@example.com',
            data_content={'experiences': [{'description': 'Improved throughput and cut latency.'}]},
        )
        c2 = _score_evidence(profile_without)
        met2 = {i['key']: i['met'] for i in c2['items']}
        self.assertFalse(met2['descriptions_metric'])

    def test_evidence_credential_accepts_publications_or_awards(self):
        from profiles.services.profile_strength import _score_evidence
        profile = self._make_profile(data_content={'publications': [{'title': 'Paper'}]})
        c = _score_evidence(profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['has_credential'])

    def test_signals_empty_profile_scores_zero(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile()
        c = _score_signals(profile)
        self.assertEqual(c['key'], 'signals')
        self.assertEqual(c['max'], 35)
        self.assertEqual(c['score'], 0)

    def test_signals_github_with_repos_scores_14(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(
            data_content={
                'github_signals': {
                    'username': 'x', 'public_repos': 5,
                    'fetched_at': '2026-04-10T00:00:00Z',
                },
            },
        )
        c = _score_signals(profile)
        met = {i['key']: i['points'] for i in c['items'] if i['met']}
        self.assertEqual(met.get('github_connected'), 14)

    def test_signals_errored_github_counts_as_unmet(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(
            data_content={
                'github_signals': {
                    'error': 'rate limited', 'username': 'x', 'public_repos': 99,
                },
            },
        )
        c = _score_signals(profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertFalse(met['github_connected'])

    def test_signals_scholar_citations_awards_points(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(
            data_content={'scholar_signals': {'total_citations': 25, 'fetched_at': '2026-04-10T00:00:00Z'}},
        )
        c = _score_signals(profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['scholar_or_kaggle'])

    def test_signals_kaggle_competitions_awards_points(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(
            data_content={'kaggle_signals': {'competitions': {'count': 2}, 'fetched_at': '2026-04-10T00:00:00Z'}},
        )
        c = _score_signals(profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['scholar_or_kaggle'])

    def test_signals_linkedin_url_awards_points(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(linkedin_url='https://linkedin.com/in/x')
        c = _score_signals(profile)
        met = {i['key']: i['points'] for i in c['items'] if i['met']}
        self.assertEqual(met.get('has_linkedin'), 4)

    def test_signals_freshness_requires_recent_fetched_at(self):
        from profiles.services.profile_strength import _score_signals
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        fresh_profile = self._make_profile(
            data_content={'github_signals': {'public_repos': 2, 'fetched_at': recent}},
        )
        c = _score_signals(fresh_profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['signals_fresh'])

        from profiles.models import UserProfile
        u2 = get_user_model().objects.create_user(username='s2@example.com', email='s2@example.com', password='x')
        stale_profile = UserProfile.objects.create(
            user=u2, full_name='S', email='s@e.com',
            data_content={'github_signals': {'public_repos': 2, 'fetched_at': old}},
        )
        c2 = _score_signals(stale_profile)
        met2 = {i['key']: i['met'] for i in c2['items']}
        self.assertFalse(met2['signals_fresh'])

    def test_tier_thresholds_boundary_cases(self):
        from profiles.services.profile_strength import _tier
        self.assertEqual(_tier(0), 'Weak')
        self.assertEqual(_tier(34), 'Weak')
        self.assertEqual(_tier(35), 'Developing')
        self.assertEqual(_tier(59), 'Developing')
        self.assertEqual(_tier(60), 'Solid')
        self.assertEqual(_tier(79), 'Solid')
        self.assertEqual(_tier(80), 'Strong')
        self.assertEqual(_tier(100), 'Strong')

    def test_top_actions_returns_three_highest_point_unmet_items(self):
        from profiles.services.profile_strength import _top_actions
        comps = [
            {'key': 'completeness', 'label': 'C', 'score': 0, 'max': 35, 'items': [
                {'key': 'has_identity', 'label': 'Add name+email', 'met': False, 'points': 5},
                {'key': 'has_three_exps', 'label': 'Describe 3 experiences', 'met': False, 'points': 10},
                {'key': 'has_summary', 'label': 'Summary', 'met': True, 'points': 5},
            ]},
            {'key': 'signals', 'label': 'S', 'score': 0, 'max': 35, 'items': [
                {'key': 'github_connected', 'label': 'Connect GitHub', 'met': False, 'points': 14},
                {'key': 'has_linkedin', 'label': 'LinkedIn URL', 'met': False, 'points': 4},
            ]},
        ]
        actions = _top_actions(comps)
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0]['points'], 14)  # GitHub
        self.assertEqual(actions[1]['points'], 10)  # three exps
        self.assertEqual(actions[2]['points'], 5)   # identity
        self.assertIn('+14 points', actions[0]['label'])
        self.assertEqual(actions[0]['href'], '/insights/')
        self.assertEqual(actions[1]['href'], '/profiles/setup/review/')

    def test_top_actions_stable_tiebreak_by_key(self):
        from profiles.services.profile_strength import _top_actions
        comps = [{
            'key': 'completeness', 'label': 'C', 'score': 0, 'max': 35,
            'items': [
                {'key': 'has_summary',     'label': 'Summary',   'met': False, 'points': 5},
                {'key': 'has_education',   'label': 'Education', 'met': False, 'points': 5},
                {'key': 'has_five_skills', 'label': 'Skills',    'met': False, 'points': 5},
            ],
        }]
        actions = _top_actions(comps)
        # Alphabetical tiebreak on key: has_education, has_five_skills, has_summary
        self.assertEqual([a['label'].split(' · ')[0] for a in actions], ['Education', 'Skills', 'Summary'])

    def test_top_actions_empty_when_nothing_unmet(self):
        from profiles.services.profile_strength import _top_actions
        comps = [{
            'key': 'completeness', 'label': 'C', 'score': 35, 'max': 35,
            'items': [
                {'key': 'has_identity', 'label': 'Identity', 'met': True, 'points': 5},
            ],
        }]
        self.assertEqual(_top_actions(comps), [])

    def test_compute_empty_profile_scores_zero_weak(self):
        from profiles.services.profile_strength import compute_profile_strength
        profile = self._make_profile()
        s = compute_profile_strength(profile, self.user)
        self.assertEqual(s['score'], 0)
        self.assertEqual(s['tier'], 'Weak')
        self.assertEqual(len(s['components']), 3)
        self.assertEqual([c['key'] for c in s['components']], ['completeness', 'evidence', 'signals'])

    def test_compute_score_is_sum_of_component_scores(self):
        from profiles.services.profile_strength import compute_profile_strength
        profile = self._make_profile(
            full_name='J', email='j@e.com',
            data_content={
                'skills': [{'name': s} for s in ['A', 'B', 'C', 'D', 'E']],
                'github_signals': {
                    'public_repos': 3,
                    'fetched_at': '2026-04-10T00:00:00Z',
                },
            },
        )
        s = compute_profile_strength(profile, self.user)
        comp_scores = {c['key']: c['score'] for c in s['components']}
        self.assertEqual(s['score'], sum(comp_scores.values()))
        self.assertIn('completeness', comp_scores)
        self.assertIn('evidence', comp_scores)
        self.assertIn('signals', comp_scores)

    def test_compute_top_actions_present_when_gaps_exist(self):
        from profiles.services.profile_strength import compute_profile_strength
        profile = self._make_profile(full_name='J', email='j@e.com')  # mostly empty
        s = compute_profile_strength(profile, self.user)
        self.assertGreater(len(s['top_actions']), 0)
        self.assertLessEqual(len(s['top_actions']), 3)
        for a in s['top_actions']:
            self.assertIn('href', a)
            self.assertIn('label', a)
            self.assertIn('points', a)

    def test_dashboard_view_includes_profile_strength_in_context(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        self._make_profile(full_name='Jane', email='jane@e.com')
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('profile_strength', resp.context)
        ps = resp.context['profile_strength']
        self.assertIn('score', ps)
        self.assertIn('tier', ps)
        self.assertIn('components', ps)
        self.assertIn('top_actions', ps)

    def test_dashboard_renders_profile_strength_ring(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        self._make_profile(full_name='Jane', email='jane@e.com')
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        # Score badge / data attribute visible on page
        self.assertContains(resp, 'data-profile-strength')
        # Tier label appears (new empty-ish profile = Weak)
        self.assertContains(resp, 'Weak')
