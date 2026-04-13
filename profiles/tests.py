"""Tests for profiles.services.cv_parser.

Covers the deterministic text-processing stages (sanitization, regex-based
personal info, section-header detection, fuzzy matching) — the parts that
have been patched repeatedly (9b998a9, e6fce11, dd00e14). LLM refinement is
out of scope for these tests; get_llm_client is patched so no network/API
calls happen.
"""
from unittest.mock import patch

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
