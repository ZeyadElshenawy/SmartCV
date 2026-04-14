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


