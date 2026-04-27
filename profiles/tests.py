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

    def test_authorization_header_set_when_token_configured(self):
        captured = {}
        routes = {
            '/users/octocat/repos': _fake_response([]),
            '/users/octocat/events/public': _fake_response([]),
            '/users/octocat': _fake_response({'name': 'Octo'}),
        }
        stub = _stub_session(routes)
        stub.headers.update.side_effect = lambda h: captured.update(h)
        with patch('profiles.services.github_aggregator.GITHUB_TOKEN', 'ghp_test123'), \
             patch('profiles.services.github_aggregator.requests.Session', return_value=stub):
            fetch_github_snapshot('octocat')
        self.assertEqual(captured.get('Authorization'), 'Bearer ghp_test123')

    def test_authorization_header_absent_when_token_unset(self):
        captured = {}
        routes = {
            '/users/octocat/repos': _fake_response([]),
            '/users/octocat/events/public': _fake_response([]),
            '/users/octocat': _fake_response({'name': 'Octo'}),
        }
        stub = _stub_session(routes)
        stub.headers.update.side_effect = lambda h: captured.update(h)
        with patch('profiles.services.github_aggregator.GITHUB_TOKEN', ''), \
             patch('profiles.services.github_aggregator.requests.Session', return_value=stub):
            fetch_github_snapshot('octocat')
        self.assertNotIn('Authorization', captured)





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

from profiles.services.cv_parser import _is_plausible_skill_name


class IsPlausibleSkillNameTests(SimpleTestCase):
    """Junk filter applied to every skill before it leaves parse_cv.

    Every rejected pattern was an actual hit in benchmarks/results/2026-04-25
    on cv_frontend_senior_react_vue (24 garbage strings extracted) — the
    filter exists to defend against PDF-extraction noise leaking into the
    profile's skills list, not against hypothetical inputs.
    """

    def test_real_skills_pass(self):
        for s in ("Python", "React.js", "PostgreSQL", "CI/CD", "Power BI",
                  "scikit-learn", "Machine Learning", "Vue", "Node.js"):
            self.assertTrue(_is_plausible_skill_name(s), f"rejected real skill: {s!r}")

    def test_blank_or_too_short_rejected(self):
        for s in ("", " ", "X"):
            self.assertFalse(_is_plausible_skill_name(s))

    def test_too_long_rejected(self):
        # 41+ chars — typical of a glued-on bullet body
        self.assertFalse(_is_plausible_skill_name("Developing a high-traffic e-commerce platform"))

    def test_sentence_fragment_rejected(self):
        # Trailing period: bullet body, not a skill.
        self.assertFalse(_is_plausible_skill_name("Mentoring Junior Developers."))
        self.assertFalse(_is_plausible_skill_name("increased sales by 40%."))

    def test_percent_phrase_rejected(self):
        # Real benchmark hit: "increased sales by 40%."
        self.assertFalse(_is_plausible_skill_name("increased sales by 40%"))
        self.assertFalse(_is_plausible_skill_name("growth 25%"))

    def test_url_fragments_rejected(self):
        for s in ("www.enhancv.com", "https://github.com", "http://x"):
            self.assertFalse(_is_plausible_skill_name(s))

    def test_embedded_link_marker_rejected(self):
        # PDF embed artifacts come through as "[Embedded Link: '<glyph>"
        self.assertFalse(_is_plausible_skill_name("[Embedded Link: '"))

    def test_more_than_four_words_rejected(self):
        # Real hit: "completion of several high"  (5 words and a fragment).
        self.assertFalse(_is_plausible_skill_name("completion of several high profile"))

    def test_non_alpha_leading_rejected(self):
        # "(React)", "/python", "-Vue" — bullet artifacts.
        self.assertFalse(_is_plausible_skill_name("(React)"))
        self.assertFalse(_is_plausible_skill_name("- Vue"))
        self.assertFalse(_is_plausible_skill_name("123 Skills"))


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

    def test_compute_handles_list_shaped_descriptions(self):
        """Resume schemas store descriptions as list[str] (bullets).
        Profile-strength scoring must not crash on them."""
        from profiles.services.profile_strength import compute_profile_strength
        profile = self._make_profile(
            full_name='J', email='j@e.com',
            data_content={
                'experiences': [
                    {'title': 'A', 'description': ['Shipped 3 services', 'Mentored 5 juniors']},
                    {'title': 'B', 'description': ['Built data pipeline']},
                    {'title': 'C', 'description': ['Led incident response']},
                ],
                'projects': [
                    {'name': 'X', 'description': ['Open-source tool for X', 'Used by 200+ teams']},
                ],
            },
        )
        # Must not raise.
        s = compute_profile_strength(profile, self.user)
        comps = {c['key']: c for c in s['components']}
        met = {i['key']: i['met'] for i in comps['completeness']['items']}
        self.assertTrue(met['has_three_exps'])
        met_ev = {i['key']: i['met'] for i in comps['evidence']['items']}
        self.assertTrue(met_ev['has_project'])
        # Digit in "Shipped 3 services" triggers the metric item.
        self.assertTrue(met_ev['descriptions_metric'])

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


class ReviewMasterProfileFormTests(TestCase):
    """Guards the "Build by form" (welcome -> Build by form -> /setup/review/) flow.

    The page used to wrap every per-field <input> in x-show="hasValue(exp.X)",
    so when a fresh user clicked "+ Add position" the new row seeded all-empty
    strings, every x-show evaluated false, and the row rendered with only a
    close button — no fields to type into. Same pattern broke Education,
    Projects, Certifications, and the Objective/Summary textareas.

    These tests pin both the server round-trip AND the page structure, so the
    pattern can't be reintroduced without a red test.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='rev@example.com', email='rev@example.com', password='x',
        )
        self.client.force_login(self.user)

    def test_empty_profile_renders_without_hasvalue_guards(self):
        """Regression: per-field inputs must NEVER be wrapped in
        x-show="hasValue(exp.*|edu.*|proj.*|cert.*)". If they are, a fresh
        user who clicks + Add sees a row with no editable fields."""
        from django.urls import reverse
        resp = self.client.get(reverse('review_master_profile'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')

        # No x-show="hasValue(...)" on elements anywhere in the rendered HTML
        # (the JS function definition `hasValue(val) {` does not match this pattern).
        import re
        offenders = re.findall(r'x-show="hasValue\([^"]*\)"', body)
        self.assertEqual(offenders, [], f'Unexpected x-show guards: {offenders}')

        # The fresh page must expose the Objective + Summary textareas so
        # a fresh user can fill them in.
        self.assertIn('name="objective"', body)
        self.assertIn('name="normalized_summary"', body)

        # The certifications section must be reachable (its <section> used to
        # be x-show="hasValue(certifications)", which hid the "+ Add certification"
        # button itself for fresh users).
        self.assertIn('+ Add certification', body)

    def test_post_round_trips_experiences_education_projects(self):
        """Saving the form from the client must persist the JSON arrays.

        This is the "Build by form, then click Save" path. We simulate what
        Alpine would submit — hidden JSON fields wired via x-model.
        """
        from django.urls import reverse
        from profiles.models import UserProfile
        payload = {
            'full_name': 'Taylor Typist',
            'email': 'taylor@example.com',
            'phone': '+1-555-0000',
            'location': 'Cairo, Egypt',
            'contact_links_json': '[]',
            'skills_json': '["Python", "Django"]',
            'experiences_json': '[{"title":"Engineer","company":"Acme","duration":"2020-2024","description":"Built things"}]',
            'education_json': '[{"degree":"BSc CS","institution":"KSIU","year":"2026"}]',
            'projects_json': '[{"name":"SmartCV","description":"CV tailoring tool"}]',
            'certifications_json': '[{"name":"AWS SAA"}]',
        }
        resp = self.client.post(reverse('review_master_profile'), payload)
        # View redirects to either job_input_view (no jobs) or dashboard.
        self.assertEqual(resp.status_code, 302)

        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.full_name, 'Taylor Typist')
        self.assertEqual(profile.location, 'Cairo, Egypt')
        # Skills stored as list of raw strings.
        self.assertIn('Python', profile.skills)
        # Nested structures round-trip intact.
        self.assertEqual(profile.experiences[0]['company'], 'Acme')
        self.assertEqual(profile.education[0]['institution'], 'KSIU')
        self.assertEqual(profile.projects[0]['name'], 'SmartCV')
        self.assertEqual(profile.certifications[0]['name'], 'AWS SAA')

    def test_add_helpers_seed_every_field_the_template_renders(self):
        """Regression: addExperience used to seed only 4 of the 10 fields the
        template displays, so freshly-added rows had undefined keys for
        location/start_date/end_date/industry/highlights/achievements. Alpine
        auto-vivifies on type, but the inconsistency is brittle — pin that
        the JS seeds match the template field set."""
        import re
        from django.urls import reverse
        resp = self.client.get(reverse('review_master_profile'))
        body = resp.content.decode('utf-8')

        # Extract each add helper's seed object and assert required keys are present.
        m = re.search(r'addExperience\(\)\s*\{[^}]*this\.experiences\.push\(\{([^}]*)\}', body)
        self.assertIsNotNone(m, 'addExperience not found')
        for key in ('title', 'company', 'duration', 'location', 'start_date', 'end_date', 'industry', 'description', 'highlights', 'achievements'):
            self.assertIn(key, m.group(1), f'addExperience missing seed key: {key}')

        m = re.search(r'addEducation\(\)\s*\{[^}]*this\.education\.push\(\{([^}]*)\}', body)
        self.assertIsNotNone(m, 'addEducation not found')
        for key in ('degree', 'institution', 'year', 'field', 'gpa', 'location', 'honors'):
            self.assertIn(key, m.group(1), f'addEducation missing seed key: {key}')

        m = re.search(r'addProject\(\)\s*\{[^}]*this\.projects\.push\(\{([^}]*)\}', body)
        self.assertIsNotNone(m, 'addProject not found')
        for key in ('name', 'role', 'url', 'description', 'highlights', 'technologies'):
            self.assertIn(key, m.group(1), f'addProject missing seed key: {key}')

        m = re.search(r'addCertification\(\)\s*\{[^}]*this\.certifications\.push\(\{([^}]*)\}', body)
        self.assertIsNotNone(m, 'addCertification not found')
        for key in ('name', 'issuer', 'date', 'duration', 'url'):
            self.assertIn(key, m.group(1), f'addCertification missing seed key: {key}')

    def test_current_profile_api_exposes_profile_strength(self):
        """The /profiles/api/current/ endpoint must include a profile_strength
        {score, tier} so the chatbot Completeness tile reads the same number
        the dashboard ring shows — fixing the old 9-field checklist that
        maxed at 100 whenever the basics were filled.
        """
        from django.urls import reverse
        from profiles.models import UserProfile
        UserProfile.objects.create(
            user=self.user, full_name='Jane', email='jane@e.com',
            data_content={'skills': [{'name': 'Python'}]},
        )
        resp = self.client.get(reverse('get_current_profile'))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('profile_strength', body)
        self.assertIn('score', body['profile_strength'])
        self.assertIn('tier', body['profile_strength'])
        self.assertIsInstance(body['profile_strength']['score'], int)
        # A bare profile with just name/email/1 skill should score well under
        # 100 — guards against any future bug where the API shortcuts the
        # score back to the old "has any data = 100" behavior.
        self.assertLess(body['profile_strength']['score'], 100)

    def test_review_redirects_onboarding_user_to_connect_accounts(self):
        """Fresh signup flow: after saving the master profile, the next step
        is the connect-accounts page (so external signals enrich the first
        gap analysis). Existing users editing their profile skip straight to
        job input / dashboard."""
        from django.urls import reverse
        session = self.client.session
        session['in_onboarding'] = True
        session.save()
        resp = self.client.post(reverse('review_master_profile'), {
            'full_name': 'T. Typist', 'email': 't@example.com',
            'phone': '', 'location': '', 'contact_links_json': '[]',
            'skills_json': '[]', 'experiences_json': '[]',
            'education_json': '[]', 'projects_json': '[]',
            'certifications_json': '[]',
        })
        self.assertRedirects(resp, reverse('connect_accounts'))

    def test_review_does_not_route_existing_user_through_connect(self):
        from django.urls import reverse
        # No in_onboarding flag set.
        resp = self.client.post(reverse('review_master_profile'), {
            'full_name': 'T. Typist', 'email': 't@example.com',
            'phone': '', 'location': '', 'contact_links_json': '[]',
            'skills_json': '[]', 'experiences_json': '[]',
            'education_json': '[]', 'projects_json': '[]',
            'certifications_json': '[]',
        })
        # Either job_input (no jobs) or dashboard — never connect_accounts.
        self.assertNotEqual(resp['Location'], reverse('connect_accounts'))
        self.assertIn(resp['Location'], (
            reverse('job_input_view'), reverse('dashboard'),
        ))

    def test_connect_accounts_page_renders_all_four_signal_widgets(self):
        from django.urls import reverse
        resp = self.client.get(reverse('connect_accounts'))
        self.assertEqual(resp.status_code, 200)
        # All four signal aggregation panels must be on the page.
        self.assertContains(resp, 'githubSignals(')
        self.assertContains(resp, 'linkedin')
        self.assertContains(resp, 'scholar')
        self.assertContains(resp, 'kaggle')
        # And the Continue button posts back to the same URL.
        self.assertContains(resp, 'Continue')

    def test_connect_accounts_continue_routes_to_job_input_when_no_jobs(self):
        from django.urls import reverse
        resp = self.client.post(reverse('connect_accounts'))
        self.assertRedirects(resp, reverse('job_input_view'))

    def test_connect_accounts_continue_routes_to_dashboard_when_jobs_exist(self):
        from django.urls import reverse
        from jobs.models import Job
        Job.objects.create(user=self.user, title='Engineer')
        resp = self.client.post(reverse('connect_accounts'))
        self.assertRedirects(resp, reverse('dashboard'))

    def test_review_page_uses_new_yoe_service(self):
        """The review page's Career Snapshot YoE stat must come from
        experience_math.compute_years_of_experience, not the old inline
        regex. Validated by feeding Zeyad's CV data and asserting the
        rendered number is 0 (two single-month internships), which is the
        answer the old algorithm got famously wrong (it produced 3)."""
        from django.urls import reverse
        from profiles.models import UserProfile
        UserProfile.objects.create(
            user=self.user,
            data_content={
                'experiences': [
                    {'title': 'Digital Transformation Intern',
                     'company': 'Almansour Automative',
                     'start_date': 'August 2025', 'end_date': ''},
                    {'title': 'Information Technology Intern',
                     'company': 'Arab Organization for Industrialization',
                     'start_date': 'July 2024', 'end_date': ''},
                ],
            },
        )
        resp = self.client.get(reverse('review_master_profile'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['summary_stats']['total_yoe'], 0)

    def test_add_helpers_flash_and_autofocus_new_rows(self):
        """UX affordance: clicking + Add must scroll the new row into view,
        focus its first input, and briefly highlight it. Implemented by a
        shared _flashLastRow(key) helper that each add method calls after
        pushing. Pin both the helper and the call sites + data-row hooks.
        """
        from django.urls import reverse
        resp = self.client.get(reverse('review_master_profile'))
        body = resp.content.decode('utf-8')

        # The helper exists and does what it says.
        self.assertIn('_flashLastRow(key)', body)
        self.assertIn('scrollIntoView', body)
        self.assertIn("behavior: 'smooth'", body)
        # Auto-focus the first real input inside the new row.
        self.assertIn("first.focus", body)

        # Every add helper calls the flash with its section key.
        for call in (
            "this._flashLastRow('experience')",
            "this._flashLastRow('education')",
            "this._flashLastRow('project')",
            "this._flashLastRow('certification')",
            "this._flashLastRow('link')",
        ):
            self.assertIn(call, body, f'Missing flash call: {call}')

        # Each row card carries the data-row hook the helper queries on.
        for value in ('experience', 'education', 'project', 'certification', 'link'):
            self.assertIn(f'data-row="{value}"', body,
                          f'Missing data-row="{value}" hook on row card')


class ComputeYearsOfExperienceTests(SimpleTestCase):
    """Month-precision YoE with interval merging.

    Replaces the inline regex-year subtraction that used to live in
    review_master_profile. That version had three failure modes: it
    defaulted empty end dates to "this year" (so single-date internships
    were scored as multi-year ongoing roles), it counted at year granularity
    only (so Dec 2021 - Jan 2022 scored 1.0 years), and it summed naively
    across overlapping intervals.
    """

    _TODAY = __import__('datetime').date(2026, 4, 17)

    def _yoe(self, experiences):
        from profiles.services.experience_math import compute_years_of_experience
        return compute_years_of_experience(experiences, today=self._TODAY)

    def test_empty_input_is_zero(self):
        self.assertEqual(self._yoe([]), 0)
        self.assertEqual(self._yoe(None), 0)

    def test_single_date_entry_counts_as_one_month(self):
        """A CV line like 'August 2025' with no end date is a single-month
        entry. One month floors to 0 years."""
        self.assertEqual(
            self._yoe([{'start_date': 'August 2025', 'end_date': ''}]), 0,
        )

    def test_present_keyword_credits_to_today(self):
        # Jan 2020 (inclusive) -> Apr 2026 (inclusive) = 76 months = 6 years.
        self.assertEqual(
            self._yoe([{'start_date': 'Jan 2020', 'end_date': 'Present'}]), 6,
        )

    def test_current_keyword_also_ongoing(self):
        self.assertEqual(
            self._yoe([{'start_date': 'Jan 2020', 'end_date': 'current'}]), 6,
        )

    def test_full_year_range(self):
        # Jan 2020 – Dec 2022 inclusive = 36 months = 3 years.
        self.assertEqual(
            self._yoe([{'start_date': 'Jan 2020', 'end_date': 'Dec 2022'}]), 3,
        )

    def test_overlapping_jobs_merge_not_sum(self):
        # Job A: Jan 2020 – Dec 2022 (36 months)
        # Job B: Jan 2021 – Dec 2023 (36 months)
        # Merged: Jan 2020 – Dec 2023 = 48 months = 4 years (not 6).
        self.assertEqual(
            self._yoe([
                {'start_date': 'Jan 2020', 'end_date': 'Dec 2022'},
                {'start_date': 'Jan 2021', 'end_date': 'Dec 2023'},
            ]),
            4,
        )

    def test_non_overlapping_jobs_sum(self):
        # Jan 2019–Dec 2019 (12mo) + Jan 2021–Dec 2022 (24mo) = 36mo = 3yr.
        self.assertEqual(
            self._yoe([
                {'start_date': 'Jan 2019', 'end_date': 'Dec 2019'},
                {'start_date': 'Jan 2021', 'end_date': 'Dec 2022'},
            ]),
            3,
        )

    def test_back_to_back_months_merge(self):
        # Jan–Dec 2020 + Jan–Dec 2021 should merge to 24 months = 2 years.
        # Without merging of adjacent months, sum would also be 24, but merge
        # avoids drift when there's a 1-day gap in reality.
        self.assertEqual(
            self._yoe([
                {'start_date': 'Jan 2020', 'end_date': 'Dec 2020'},
                {'start_date': 'Jan 2021', 'end_date': 'Dec 2021'},
            ]),
            2,
        )

    def test_zeyad_cv_scenario(self):
        """Regression lock: the CV that motivated this rewrite now returns 0."""
        self.assertEqual(
            self._yoe([
                {'title': 'Digital Transformation Intern',
                 'company': 'Almansour Automative',
                 'start_date': 'August 2025', 'end_date': ''},
                {'title': 'Information Technology Intern',
                 'company': 'Arab Organization for Industrialization',
                 'start_date': 'July 2024', 'end_date': ''},
            ]),
            0,
        )

    def test_end_before_start_is_skipped(self):
        self.assertEqual(
            self._yoe([{'start_date': '2022', 'end_date': '2019'}]), 0,
        )

    def test_unparseable_dates_skip_safely(self):
        self.assertEqual(
            self._yoe([
                {'start_date': 'whenever', 'end_date': 'later'},
                {'start_date': 'Jan 2020', 'end_date': 'Dec 2022'},
            ]),
            3,
        )

    def test_year_only_dates(self):
        # '2020' -> Jan 2020 (month defaults to 1); '2022' -> Jan 2022.
        # Jan 2020 through Jan 2022 inclusive = 25 months = 2 years.
        self.assertEqual(
            self._yoe([{'start_date': '2020', 'end_date': '2022'}]), 2,
        )

    def test_numeric_month_forms(self):
        # "2020-05" and "05/2022" should parse identically to "May 2020"
        # and "May 2022". May 2020 - May 2022 inclusive = 25 months = 2 years.
        self.assertEqual(
            self._yoe([{'start_date': '2020-05', 'end_date': '05/2022'}]), 2,
        )

    def test_duration_field_fallback(self):
        """Some LLM parses store the range in 'duration' instead of
        start_date/end_date. Fallback should still work."""
        self.assertEqual(
            self._yoe([{'duration': 'Jan 2020 – Present'}]), 6,
        )

    def test_duration_fallback_with_explicit_range(self):
        self.assertEqual(
            self._yoe([{'duration': 'January 2020 – December 2022'}]), 3,
        )

    def test_short_month_names(self):
        self.assertEqual(
            self._yoe([{'start_date': 'Mar 2020', 'end_date': 'Feb 2021'}]), 1,
        )

    def test_missing_start_date_is_skipped(self):
        self.assertEqual(
            self._yoe([{'start_date': '', 'end_date': 'Dec 2022'}]), 0,
        )


# ============================================================
# Project enrichment + dedupe (Phase 1)
# ============================================================

class ProjectEnricherTests(TestCase):
    """The enricher transforms GitHub/Scholar/Kaggle signal blobs into
    project-shaped artifacts. We mock the LLM and assert the prompt
    structure + cache + fallback behaviour, NOT the LLM's stylistic choices.
    """

    def setUp(self):
        from profiles.models import UserProfile
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='enrich@example.com', email='enrich@example.com', password='x',
        )
        self.profile = UserProfile.objects.create(
            user=self.user,
            data_content={
                'github_signals': {
                    'username': 'octocat',
                    'profile_url': 'https://github.com/octocat',
                    'public_repos': 3,
                    'total_stars': 50,
                    'top_repos': [
                        {
                            'name': 'alpha', 'full_name': 'octocat/alpha',
                            'description': 'A neat thing', 'html_url': 'https://github.com/octocat/alpha',
                            'stargazers_count': 30, 'forks_count': 4, 'language': 'Python',
                        },
                    ],
                    'language_breakdown': [{'language': 'Python', 'count': 1, 'share': 1.0}],
                    'recent_commit_count': 5,
                },
                'scholar_signals': {
                    'profile_url': 'https://scholar.google.com/citations?user=ABC',
                    'top_publications': [{'title': 'Tabular Deep Learning Survey',
                                          'venue': 'NeurIPS', 'year': '2024', 'citations': 12}],
                },
                'kaggle_signals': {
                    'profile_url': 'https://www.kaggle.com/octocat',
                    'overall_tier': 'Competitions Expert',
                    'competitions': {'count': 12, 'tier': 'Expert',
                                     'medals': {'gold': 0, 'silver': 3, 'bronze': 4}},
                    'datasets': {'count': 0, 'tier': None,
                                 'medals': {'gold': 0, 'silver': 0, 'bronze': 0}},
                    'notebooks': {'count': 5, 'tier': 'Contributor',
                                  'medals': {'gold': 0, 'silver': 0, 'bronze': 1}},
                    'discussion': {'count': 0, 'tier': None,
                                   'medals': {'gold': 0, 'silver': 0, 'bronze': 0}},
                },
            },
        )

    def test_fallback_used_when_llm_unavailable(self):
        """When the LLM raises, the deterministic fallback fires per source
        and produces grounded entries straight from the API payload."""
        from profiles.services import project_enricher

        with patch.object(project_enricher, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = RuntimeError('boom')
            out = project_enricher.enrich_profile(self.profile, force=True)

        # 1 GitHub repo + 1 Scholar publication + 2 Kaggle categories with activity
        self.assertEqual(len(out), 4)
        sources = [p['source'] for p in out]
        self.assertEqual(sources.count('github'), 1)
        self.assertEqual(sources.count('scholar'), 1)
        self.assertEqual(sources.count('kaggle'), 2)
        # GitHub fallback grounds claims in the actual API payload
        gh = next(p for p in out if p['source'] == 'github')
        self.assertEqual(gh['name'], 'alpha')
        self.assertEqual(gh['source_url'], 'https://github.com/octocat/alpha')
        self.assertIn('Python', gh['tech_stack'])
        # Should mention 30 stars (from the payload), nothing fabricated
        joined = ' '.join(gh['bullets']).lower()
        self.assertIn('30 stars', joined)
        # Scholar fallback uses the title verbatim
        sc = next(p for p in out if p['source'] == 'scholar')
        self.assertEqual(sc['name'], 'Tabular Deep Learning Survey')
        self.assertIn('NeurIPS', sc['summary'])
        # Kaggle fallback excludes the empty datasets/discussion categories
        kaggle_names = [p['name'] for p in out if p['source'] == 'kaggle']
        self.assertIn('Kaggle Competitions', kaggle_names)
        self.assertIn('Kaggle Notebooks', kaggle_names)
        self.assertNotIn('Kaggle Datasets', kaggle_names)

    def test_cache_hit_skips_llm_when_inputs_unchanged(self):
        """A second call with identical inputs reads the cached output and
        does NOT invoke the LLM."""
        from profiles.services import project_enricher

        # Prime the cache via the fallback path (LLM forced to error).
        with patch.object(project_enricher, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = RuntimeError('boom')
            first = project_enricher.enrich_profile(self.profile, force=True)
            # Save so the cache survives DB reload.
            self.profile.save()

        # Second call: LLM should NEVER be reached because the hash is unchanged.
        with patch.object(project_enricher, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = AssertionError('LLM must not be called')
            second = project_enricher.enrich_profile(self.profile, force=False)

        self.assertEqual(first, second)

    def test_force_bypasses_cache(self):
        """force=True invalidates the cache even when inputs are unchanged."""
        from profiles.services import project_enricher

        # Prime the cache.
        with patch.object(project_enricher, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = RuntimeError('boom')
            project_enricher.enrich_profile(self.profile, force=True)

        # force=True must call the LLM/fallback again.
        with patch.object(project_enricher, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = RuntimeError('boom')
            project_enricher.enrich_profile(self.profile, force=True)
            # Each source ran its own try → 3 attempts total
            self.assertGreaterEqual(mock_llm.call_count, 3)

    def test_empty_signals_produce_empty_output(self):
        from profiles.services import project_enricher
        self.profile.data_content = {}
        out = project_enricher.enrich_profile(self.profile, force=True)
        self.assertEqual(out, [])


class ProjectDedupeTests(TestCase):
    """Dedupe matches enriched projects against typed projects via one
    batched LLM call. We test the schema, the fallback, and apply_decisions
    correctness — not the LLM's exact verdict."""

    def setUp(self):
        self.typed = [
            {'name': 'pgbench-tuner', 'url': 'https://github.com/me/pgbench-tuner',
             'description': ['Tunes pg.'], 'technologies': ['Python']},
            {'name': 'My Resume Site', 'url': 'https://me.dev',
             'description': [], 'technologies': []},
        ]
        self.enriched = [
            # Same as typed[0] by URL — dedupe should match
            {'name': 'pgbench-tuner', 'source': 'github',
             'source_url': 'https://github.com/me/pgbench-tuner',
             'summary': 'Auto-tuner.', 'tech_stack': ['PostgreSQL'],
             'bullets': ['Built tuner; 50 stars on GitHub.']},
            # Brand new
            {'name': 'climate-modeling', 'source': 'scholar',
             'source_url': 'https://scholar.google.com/citations?user=X',
             'summary': 'Paper.', 'tech_stack': [],
             'bullets': ['Cited 12 times in NeurIPS 2024.']},
        ]

    def test_url_match_fallback_when_llm_fails(self):
        from profiles.services import project_dedupe
        with patch.object(project_dedupe, 'get_structured_llm') as mock_llm:
            mock_llm.return_value.invoke.side_effect = RuntimeError('boom')
            decisions = project_dedupe.dedupe_projects(self.typed, self.enriched)
        # Both enriched projects must have a decision.
        self.assertEqual(len(decisions), 2)
        # Enriched[0] matches typed[0] by URL → merge
        self.assertEqual(decisions[0]['enriched_index'], 0)
        self.assertEqual(decisions[0]['typed_index'], 0)
        self.assertEqual(decisions[0]['action'], 'merge')
        # Enriched[1] has no URL match → add_new
        self.assertEqual(decisions[1]['enriched_index'], 1)
        self.assertEqual(decisions[1]['typed_index'], -1)
        self.assertEqual(decisions[1]['action'], 'add_new')

    def test_no_typed_projects_means_all_add_new(self):
        from profiles.services import project_dedupe
        decisions = project_dedupe.dedupe_projects([], self.enriched)
        self.assertEqual(len(decisions), 2)
        for d in decisions:
            self.assertEqual(d['action'], 'add_new')
            self.assertEqual(d['typed_index'], -1)

    def test_apply_decisions_merge_unions_tech_and_concats_bullets(self):
        from profiles.services.project_dedupe import apply_decisions
        decisions = [{
            'enriched_index': 0, 'typed_index': 0, 'action': 'merge',
            'confidence': 0.95, 'reason': 'URL match.',
        }, {
            'enriched_index': 1, 'typed_index': -1, 'action': 'add_new',
            'confidence': 1.0, 'reason': 'No match.',
        }]
        result = apply_decisions(self.typed, self.enriched, decisions)
        # Original 2 typed + 1 added new = 3 projects (the merge keeps the
        # typed slot in place rather than appending).
        self.assertEqual(len(result), 3)
        merged = result[0]
        self.assertEqual(merged['name'], 'pgbench-tuner')          # typed name preserved
        self.assertIn('Python', merged['technologies'])             # typed tech preserved
        self.assertIn('PostgreSQL', merged['technologies'])         # enriched tech added
        self.assertIn('Tunes pg.', merged['description'])           # typed bullet preserved
        self.assertIn('Built tuner; 50 stars on GitHub.', merged['description'])  # enriched bullet added
        # add_new tail entry
        added = result[2]
        self.assertEqual(added['name'], 'climate-modeling')
        self.assertEqual(added['source'], 'scholar')

    def test_apply_decisions_keep_existing_drops_enriched(self):
        from profiles.services.project_dedupe import apply_decisions
        decisions = [{
            'enriched_index': 0, 'typed_index': 0, 'action': 'keep_existing',
            'confidence': 0.9, 'reason': 'Typed is more accurate.',
        }, {
            'enriched_index': 1, 'typed_index': -1, 'action': 'add_new',
            'confidence': 1.0, 'reason': 'No match.',
        }]
        result = apply_decisions(self.typed, self.enriched, decisions)
        # 2 typed + 1 added; the merge slot stays as-is (no tech merged in).
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]['technologies'], ['Python'])

    def test_apply_decisions_user_override_replaces_llm_verdict(self):
        from profiles.services.project_dedupe import apply_decisions
        # LLM said merge; user overrides to keep_new (drop typed, take enriched).
        decisions = [{
            'enriched_index': 0, 'typed_index': 0, 'action': 'merge',
            'confidence': 0.95, 'reason': 'URL match.',
        }]
        overrides = {0: 'keep_new'}
        result = apply_decisions(self.typed, self.enriched[:1], decisions, overrides=overrides)
        # typed[0] dropped, typed[1] preserved, enriched[0] appended.
        names = [p['name'] for p in result]
        self.assertEqual(names, ['My Resume Site', 'pgbench-tuner'])


class EnrichFromSignalsViewTests(TestCase):
    """The enrich-from-signals JSON endpoint orchestrates enricher + dedupe
    and returns the three lists for the (future) review UI."""

    def setUp(self):
        from profiles.models import UserProfile
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='endpoint@example.com', email='endpoint@example.com', password='x',
        )
        UserProfile.objects.create(
            user=self.user,
            data_content={
                'projects': [
                    {'name': 'alpha-tuner', 'url': 'https://github.com/me/alpha-tuner',
                     'description': ['Original.']}],
                'github_signals': {
                    'profile_url': 'https://github.com/me',
                    'top_repos': [{'name': 'alpha-tuner', 'full_name': 'me/alpha-tuner',
                                   'description': 'A tuner.', 'html_url': 'https://github.com/me/alpha-tuner',
                                   'stargazers_count': 12, 'forks_count': 1, 'language': 'Python'}],
                    'language_breakdown': [{'language': 'Python', 'count': 1, 'share': 1.0}],
                    'recent_commit_count': 3,
                },
            },
        )
        self.client.force_login(self.user)

    def test_endpoint_returns_three_lists(self):
        from profiles.services import project_enricher, project_dedupe
        with patch.object(project_enricher, 'get_structured_llm') as mock_llm_a, \
             patch.object(project_dedupe, 'get_structured_llm') as mock_llm_b:
            mock_llm_a.return_value.invoke.side_effect = RuntimeError('boom')
            mock_llm_b.return_value.invoke.side_effect = RuntimeError('boom')
            resp = self.client.post('/profiles/api/projects/enrich-from-signals/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('enriched', body)
        self.assertIn('decisions', body)
        self.assertIn('typed', body)
        # The single GitHub repo enriches into one project; dedupe sees one
        # typed counterpart and matches via URL fallback.
        self.assertEqual(len(body['enriched']), 1)
        self.assertEqual(len(body['decisions']), 1)
        self.assertEqual(body['decisions'][0]['action'], 'merge')

    def test_endpoint_rejects_get(self):
        resp = self.client.get('/profiles/api/projects/enrich-from-signals/')
        self.assertEqual(resp.status_code, 405)

    def test_endpoint_requires_login(self):
        self.client.logout()
        resp = self.client.post('/profiles/api/projects/enrich-from-signals/')
        # The login_required decorator redirects unauthenticated callers.
        self.assertIn(resp.status_code, (302, 401, 403))
