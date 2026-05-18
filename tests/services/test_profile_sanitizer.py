"""Unit tests for profiles/services/profile_sanitizer.py.

PR 1 — Fix 2 (looks_like_spoken_language helper).
"""
from __future__ import annotations

import pytest


class TestLooksLikeSpokenLanguage:
    """The heuristic backing sanitize_languages_field. True for real
    spoken languages (with or without proficiency markers), False for
    tech skills and concepts the LLM occasionally misroutes into the
    languages field."""

    @pytest.mark.parametrize('text', [
        'English',
        'Arabic (Mother Tongue)',
        'French - C1',
        'Mandarin Fluent',
        'Arabic',
        'English (Fluent)',
        'Spanish (Intermediate)',
        'German B2',
    ])
    def test_accepts_spoken_languages(self, text):
        from profiles.services.profile_sanitizer import looks_like_spoken_language
        assert looks_like_spoken_language(text), f'should accept {text!r}'

    @pytest.mark.parametrize('text', [
        'Python',
        'TensorFlow',
        'Machine Learning',
        'Cloud Computing',
        'Power BI',
        'scikit-learn',
        'Data Analysis',
    ])
    def test_rejects_tech_and_concepts(self, text):
        from profiles.services.profile_sanitizer import looks_like_spoken_language
        assert not looks_like_spoken_language(text), f'should reject {text!r}'

    def test_rejects_empty_and_non_string(self):
        from profiles.services.profile_sanitizer import looks_like_spoken_language
        assert not looks_like_spoken_language('')
        assert not looks_like_spoken_language('   ')
        assert not looks_like_spoken_language(None)
        assert not looks_like_spoken_language(42)


# ---------------------------------------------------------------------------
# PR 3d — _matches_soft_skill_blocklist (multi-word + substring rule)
# ---------------------------------------------------------------------------


class TestMatchesSoftSkillBlocklist:
    """PR 3d — adds multi-word JD-formatted soft-skill phrases to the
    blocklist and a substring-containment check (15-char min) so
    variants of the same phrase are caught."""

    @pytest.mark.parametrize('skill', [
        # Exact-match — multi-word JD-formatted phrases from the Zeyad audit.
        'Analytical and problem-solving skills',
        'Critical thinking and innovation',
        'Strong communication and collaboration skills',
        'Attention to detail',
        'Project and time management',
        'Ability to work in agile environments',
    ])
    def test_zeyad_audit_phrases_drop(self, skill):
        from profiles.services.profile_sanitizer import (
            _matches_soft_skill_blocklist, _canonical,
        )
        assert _matches_soft_skill_blocklist(_canonical(skill)), (
            f'{skill!r} should be caught as a soft skill'
        )

    @pytest.mark.parametrize('skill', [
        # Real technical skills that must NOT be caught.
        'Python', 'Java', 'TensorFlow', 'PyTorch', 'Scikit-learn',
        'Machine Learning', 'Natural Language Processing',
        'Communication protocols',  # 'communication' substring, but tech skill
        'Network communication',
        'Time series analysis',  # 'time' substring
        'Computer vision',
        'Data structures',
        'Operating systems',
    ])
    def test_real_skills_preserved(self, skill):
        from profiles.services.profile_sanitizer import (
            _matches_soft_skill_blocklist, _canonical,
        )
        assert not _matches_soft_skill_blocklist(_canonical(skill)), (
            f'{skill!r} should NOT be caught — it is a real technical skill'
        )

    def test_substring_rule_catches_variant_without_skills_suffix(self):
        from profiles.services.profile_sanitizer import (
            _matches_soft_skill_blocklist, _canonical,
        )
        # 'Strong analytical and problem-solving' has no "skills" suffix
        # — not exact-match, but substring of an exact-match entry.
        assert _matches_soft_skill_blocklist(
            _canonical('Strong analytical and problem-solving')
        )

    def test_substring_rule_respects_15_char_floor(self):
        from profiles.services.profile_sanitizer import (
            _matches_soft_skill_blocklist, _canonical,
        )
        # 'communication' is in the blocklist but only 13 chars — the
        # substring rule won't fire on it, only the exact-match rule.
        # So 'Communication protocols' (canon = 'communicationprotocols',
        # 22 chars) is NOT caught.
        assert not _matches_soft_skill_blocklist(_canonical('Communication protocols'))

    def test_exact_short_entry_still_caught(self):
        from profiles.services.profile_sanitizer import (
            _matches_soft_skill_blocklist, _canonical,
        )
        # Single-word 'Communication' — caught by exact-match rule.
        assert _matches_soft_skill_blocklist(_canonical('Communication'))

    def test_back_compat_existing_single_word_blocklist(self):
        from profiles.services.profile_sanitizer import (
            _matches_soft_skill_blocklist, _canonical,
        )
        # The pre-PR-3d entries still work.
        for word in ['Teamwork', 'Leadership', 'Adaptability', 'Collaboration']:
            assert _matches_soft_skill_blocklist(_canonical(word)), (
                f'pre-PR-3d entry {word!r} should still be caught'
            )

    def test_empty_canon_returns_false(self):
        from profiles.services.profile_sanitizer import _matches_soft_skill_blocklist
        assert _matches_soft_skill_blocklist('') is False
        assert _matches_soft_skill_blocklist(None) is False
