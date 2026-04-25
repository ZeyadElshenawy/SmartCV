"""Tests for the pure helpers in profiles/services/interviewer.py.

Covers proficiency normalization, canonical skill-name casing, the CV/JD
skill comparator, the contextual nudge generator, and the cache-key helper.
The LLM-backed `process_chat_turn` is out of scope here — those branches
need a mocked Groq round-trip and live in higher-level integration tests.
"""
from django.test import SimpleTestCase

from profiles.services.interviewer import (
    _get_contextual_nudge,
    _normalize_proficiency,
    _normalize_skill_name,
    _state_key,
    compare_cv_with_job,
)


class NormalizeProficiencyTests(SimpleTestCase):
    def test_blank_defaults_to_intermediate(self):
        self.assertEqual(_normalize_proficiency(""), "Intermediate")
        self.assertEqual(_normalize_proficiency(None), "Intermediate")

    def test_exact_canonical_levels_pass_through(self):
        for level in ("Beginner", "Intermediate", "Advanced", "Expert"):
            self.assertEqual(_normalize_proficiency(level), level)

    def test_case_insensitive_exact_match(self):
        self.assertEqual(_normalize_proficiency("EXPERT"), "Expert")
        self.assertEqual(_normalize_proficiency("intermediate"), "Intermediate")

    def test_typos_map_to_beginner(self):
        for raw in ("beginer", "biggener", "begginer"):
            self.assertEqual(_normalize_proficiency(raw), "Beginner")

    def test_synonyms_map_to_their_bucket(self):
        self.assertEqual(_normalize_proficiency("novice"), "Beginner")
        self.assertEqual(_normalize_proficiency("competent"), "Intermediate")
        self.assertEqual(_normalize_proficiency("proficient"), "Advanced")
        self.assertEqual(_normalize_proficiency("guru"), "Expert")

    def test_advanced_takes_precedence_over_intermediate_when_both_present(self):
        # "advanced" and "moderate" both appear; advanced is checked first.
        self.assertEqual(_normalize_proficiency("advanced but moderate days"), "Advanced")

    def test_unknown_text_falls_back_to_intermediate(self):
        self.assertEqual(_normalize_proficiency("¯\\_(ツ)_/¯"), "Intermediate")


class NormalizeSkillNameTests(SimpleTestCase):
    def test_blank_passthrough(self):
        self.assertEqual(_normalize_skill_name(""), "")
        self.assertIsNone(_normalize_skill_name(None))

    def test_canonical_casing_for_known_skills(self):
        self.assertEqual(_normalize_skill_name("pyspark"), "PySpark")
        self.assertEqual(_normalize_skill_name("PYSPARK"), "PySpark")
        self.assertEqual(_normalize_skill_name("javascript"), "JavaScript")
        self.assertEqual(_normalize_skill_name("postgresql"), "PostgreSQL")
        self.assertEqual(_normalize_skill_name("scikit-learn"), "scikit-learn")

    def test_natural_language_processing_canonicalizes_to_nlp(self):
        self.assertEqual(
            _normalize_skill_name("natural language processing"),
            "NLP",
        )

    def test_whitespace_is_stripped(self):
        self.assertEqual(_normalize_skill_name("  fastapi  "), "FastAPI")

    def test_unknown_skill_preserves_original_casing(self):
        # Default branch is "title case" per the docstring, but the implementation
        # actually returns the trimmed original — pin that behavior so a refactor
        # to true title-casing is a deliberate decision, not an accident.
        self.assertEqual(_normalize_skill_name("RustLang"), "RustLang")
        self.assertEqual(_normalize_skill_name("  Acme Co  "), "Acme Co")


class CompareCvWithJobTests(SimpleTestCase):
    def test_string_skills_match_case_insensitively(self):
        out = compare_cv_with_job(["Python", "SQL"], ["python", "Docker"])
        self.assertEqual(out["exact_matches"], ["python"])
        self.assertEqual(out["missing"], ["Docker"])

    def test_dict_skills_use_name_field(self):
        cv = [{"name": "Python"}, {"name": "Pandas"}]
        out = compare_cv_with_job(cv, ["pandas", "ReactJS"])
        self.assertEqual(out["exact_matches"], ["pandas"])
        self.assertEqual(out["missing"], ["ReactJS"])

    def test_empty_inputs_return_empty_buckets(self):
        out = compare_cv_with_job([], [])
        self.assertEqual(out, {"exact_matches": [], "missing": []})

    def test_no_overlap_lists_everything_missing(self):
        out = compare_cv_with_job(["Java"], ["Python", "SQL"])
        self.assertEqual(out["exact_matches"], [])
        self.assertEqual(out["missing"], ["Python", "SQL"])

    def test_dict_without_name_is_treated_as_empty_string(self):
        # Defensive: malformed CV skills shouldn't crash.
        out = compare_cv_with_job([{"years": 3}], ["Python"])
        self.assertEqual(out["exact_matches"], [])
        self.assertEqual(out["missing"], ["Python"])


class GetContextualNudgeTests(SimpleTestCase):
    def test_no_branch_returns_no_branch_nudge(self):
        nudge = _get_contextual_nudge("no", skills_to_probe=[])
        self.assertTrue(
            "exposure" in nudge or "totally fine" in nudge or "explore" in nudge,
            f"unexpected no-branch nudge: {nudge!r}",
        )

    def test_yes_branch_asks_for_more_detail(self):
        nudge = _get_contextual_nudge("yes", skills_to_probe=[])
        # Every yes-branch nudge probes for elaboration.
        self.assertTrue(
            any(token in nudge.lower() for token in ("level", "example", "more")),
            f"unexpected yes-branch nudge: {nudge!r}",
        )

    def test_default_branch_asks_for_elaboration(self):
        nudge = _get_contextual_nudge("kinda dabbled", skills_to_probe=[])
        # Default-branch nudges all ask for elaboration / more detail.
        self.assertTrue(
            any(token in nudge.lower() for token in ("detail", "elaborate", "more")),
            f"unexpected default-branch nudge: {nudge!r}",
        )

    def test_empty_reply_falls_into_default_branch(self):
        nudge = _get_contextual_nudge("", skills_to_probe=[])
        self.assertIsInstance(nudge, str)
        self.assertGreater(len(nudge), 0)

    def test_case_insensitive_yes_no(self):
        # Whitespace + uppercase still triggers the yes branch.
        nudge = _get_contextual_nudge("  YES  ", skills_to_probe=[])
        self.assertTrue(
            any(token in nudge.lower() for token in ("level", "example", "more")),
        )


class StateKeyTests(SimpleTestCase):
    def test_includes_user_and_job(self):
        self.assertEqual(
            _state_key(42, "abc-123"),
            "smartcv:chatbot_state:42:abc-123",
        )

    def test_distinct_user_or_job_yields_distinct_key(self):
        self.assertNotEqual(_state_key(1, "x"), _state_key(2, "x"))
        self.assertNotEqual(_state_key(1, "x"), _state_key(1, "y"))
