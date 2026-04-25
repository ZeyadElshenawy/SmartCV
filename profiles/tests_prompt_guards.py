"""Tests for the HUMAN_VOICE_RULE shared prompt fragment.

The rule is consumed by four prompts (resume_generator, cover_letter_generator,
outreach_generator, llm_judge). These tests pin the structural sections that
were added in the 2026-04-25 voice tightening so a future edit can't quietly
drop them — the LLM-judge `human_voice` score regressed when those sections
were missing, so they're load-bearing.
"""
from django.test import SimpleTestCase

from profiles.services.prompt_guards import HUMAN_VOICE_RULE, append_human_voice


class HumanVoiceRuleSectionsTests(SimpleTestCase):
    """Each section here corresponds to a documented failure mode in the
    benchmarks/results/2026-04-25/tailoring_eval.json judge rationales —
    the rule must keep coverage of all of them."""

    def test_banned_words_section_present(self):
        # AI-tell words the recruiter community calls out by name.
        for tok in ("leverage", "utilize", "synergy", "robust", "spearhead",
                    "transformative", "results-driven"):
            self.assertIn(tok, HUMAN_VOICE_RULE)

    def test_demonstrating_closer_pattern_called_out(self):
        self.assertIn("demonstrating", HUMAN_VOICE_RULE)

    def test_specificity_rule_present(self):
        # 2026-04-25: judge rationale "lacks ... specific achievements".
        self.assertIn("SPECIFICITY", HUMAN_VOICE_RULE)
        self.assertIn("Concrete", HUMAN_VOICE_RULE)

    def test_sentence_structure_rule_present(self):
        # 2026-04-25: top judge complaint was sentence-shape sameness.
        self.assertIn("VARY SENTENCE STRUCTURE", HUMAN_VOICE_RULE)
        self.assertIn("3 consecutive bullets", HUMAN_VOICE_RULE)

    def test_inside_out_opener_ban(self):
        self.assertIn("INSIDE-OUT OPENERS", HUMAN_VOICE_RULE)
        self.assertIn("With <N> years of experience", HUMAN_VOICE_RULE)

    def test_summary_tone_eye_roll_list(self):
        # 2026-04-25 add: senior-recruiter eye-roll phrases.
        for phrase in ("Highly motivated", "results-oriented", "team player",
                       "self-starter", "passionate about"):
            self.assertIn(phrase, HUMAN_VOICE_RULE)


class AppendHumanVoiceTests(SimpleTestCase):
    def test_appended_at_the_end(self):
        out = append_human_voice("Some prompt body.")
        self.assertTrue(out.startswith("Some prompt body."))
        self.assertTrue(out.endswith(HUMAN_VOICE_RULE))

    def test_strips_trailing_whitespace_on_body(self):
        out = append_human_voice("Some prompt body.   \n\n")
        # Body's trailing whitespace shouldn't leak into the rule block.
        self.assertIn("Some prompt body.\n\n=== HUMAN VOICE", out)
