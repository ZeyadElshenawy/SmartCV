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
        class _Result:
            skills = returned_skills

        class _StructuredLLM:
            def invoke(self, _prompt):
                return _Result()

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
        for phrase in ("technical leadership", "problem solving", "pairing sessions",
                       "code review", "leadership"):
            self.assertIn(phrase, _GENERIC_SOFT_SKILL_DENYLIST)

    def test_real_technical_skills_are_not_listed(self):
        # Sanity: the denylist must not accidentally cover real technical skills.
        for technical in ("python", "react", "kubernetes", "postgresql", "aws"):
            self.assertNotIn(technical, _GENERIC_SOFT_SKILL_DENYLIST)
