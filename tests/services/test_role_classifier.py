"""Unit tests for profiles/services/role_classifier.py.

PR 2a — Fix 2 (classify_jd_role + refactored classify_for_jd).

LLM calls are mocked at the boundary (``get_structured_llm``) so the
tests run offline.
"""
from __future__ import annotations

import logging
from unittest.mock import patch, MagicMock

import pytest

from profiles.services.role_classifier import RoleClassification


def _fake_role_classification(primary_role, seniority='mid', stack=None):
    """Build a RoleClassification with sensible defaults for tests."""
    return RoleClassification(
        primary_role=primary_role,
        seniority=seniority,
        tech_stack_signals=stack or [],
        region='global',
    )


def _structured_llm_returning(result):
    """Build a mock that mimics ``get_structured_llm(schema, ...)`` —
    i.e. an object with an ``invoke(prompt)`` method that returns the
    pre-baked result."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = result
    return mock_llm


# --------------------------------------------------------------------------
# classify_jd_role
# --------------------------------------------------------------------------

class TestClassifyJdRole:
    """Fix 2 — classify_jd_role reads the JD (not the candidate) and
    returns a RoleClassification. Fails closed to a junior software_engineer
    default when the LLM call raises."""

    def test_ai_developer_jd_returns_ml_engineer(self):
        # The mocked LLM speaks for itself — we just confirm the function
        # passes through the structured output (and isn't, say, dropping
        # primary_role on the floor).
        from profiles.services import role_classifier
        result = _fake_role_classification('AI/ML Engineer', seniority='junior')
        with patch.object(role_classifier, 'get_structured_llm',
                          return_value=_structured_llm_returning(result)):
            out = role_classifier.classify_jd_role('We need someone to ship GenAI features.')
        assert out.primary_role == 'AI/ML Engineer'
        assert out.seniority == 'junior'

    def test_devops_jd_returns_devops(self):
        from profiles.services import role_classifier
        result = _fake_role_classification('DevOps Engineer', seniority='senior',
                                            stack=['Kubernetes', 'Terraform'])
        with patch.object(role_classifier, 'get_structured_llm',
                          return_value=_structured_llm_returning(result)):
            out = role_classifier.classify_jd_role('Looking for a senior DevOps lead.')
        assert out.primary_role == 'DevOps Engineer'
        assert out.seniority == 'senior'

    def test_data_scientist_jd_returns_data_scientist(self):
        from profiles.services import role_classifier
        result = _fake_role_classification('Data Scientist', seniority='mid')
        with patch.object(role_classifier, 'get_structured_llm',
                          return_value=_structured_llm_returning(result)):
            out = role_classifier.classify_jd_role('Build models for fraud detection.')
        assert out.primary_role == 'Data Scientist'

    def test_llm_failure_returns_fail_safe_and_logs_warning(self, caplog):
        from profiles.services import role_classifier

        def _raising_get_llm(*args, **kwargs):
            raise RuntimeError('groq blew up')

        with patch.object(role_classifier, 'get_structured_llm',
                          side_effect=_raising_get_llm):
            with caplog.at_level(logging.WARNING,
                                  logger='profiles.services.role_classifier'):
                out = role_classifier.classify_jd_role('any JD here')
        # Fail-safe defaults documented in classify_jd_role.
        assert out.primary_role == 'Software Engineer'
        assert out.seniority == 'junior'
        assert out.tech_stack_signals == []
        # WARNING surfaces the exception class so debugging is observable.
        warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any('classify_jd_role' in r.message for r in warns)

    def test_empty_jd_text_returns_fail_safe_without_calling_llm(self):
        from profiles.services import role_classifier
        # If the LLM IS called, this mock will return a wrong-looking
        # result that we'd then assert against — but the function must
        # short-circuit on empty input, so we should never reach it.
        sentinel = _fake_role_classification('SENTINEL ROLE')
        with patch.object(role_classifier, 'get_structured_llm',
                          return_value=_structured_llm_returning(sentinel)):
            out = role_classifier.classify_jd_role('   ')
        assert out.primary_role == 'Software Engineer'


# --------------------------------------------------------------------------
# classify_for_jd — JD vs profile merge
# --------------------------------------------------------------------------

class TestClassifyForJdMerge:
    """Fix 2 — classify_for_jd calls both classifiers, JD wins on
    disagreement, and the profile-derived role is preserved on the
    merged object via the ``extra="allow"`` extension."""

    def test_agreement_both_classify_ml_engineer(self):
        from profiles.services import role_classifier
        profile_cls = _fake_role_classification('AI/ML Engineer')
        jd_cls = _fake_role_classification('AI/ML Engineer', seniority='junior')
        with patch.object(role_classifier, 'detect_role_seniority',
                          return_value=profile_cls), \
             patch.object(role_classifier, 'classify_jd_role',
                          return_value=jd_cls):
            out = role_classifier.classify_for_jd(
                {'full_name': 'X', 'skills': ['Python', 'TensorFlow']},
                'AI/ML JD here',
            )
        assert out.primary_role == 'AI/ML Engineer'
        assert out.seniority == 'junior'
        # profile_role surfaced via the extra attribute.
        assert getattr(out, 'profile_role', None) == 'AI/ML Engineer'

    def test_disagreement_jd_wins(self):
        # Real Zeyad scenario: profile classifies as data_scientist, JD
        # asks for ML engineer. The merged primary_role MUST be the JD's
        # — that's what drives retrieval.
        from profiles.services import role_classifier
        profile_cls = _fake_role_classification('Data Scientist', seniority='mid')
        jd_cls = _fake_role_classification('AI/ML Engineer', seniority='junior')
        with patch.object(role_classifier, 'detect_role_seniority',
                          return_value=profile_cls), \
             patch.object(role_classifier, 'classify_jd_role',
                          return_value=jd_cls):
            out = role_classifier.classify_for_jd(
                {'full_name': 'X', 'skills': ['Python']}, 'AI JD',
            )
        assert out.primary_role == 'AI/ML Engineer', 'JD must win on disagreement'
        assert getattr(out, 'profile_role', None) == 'Data Scientist', (
            'profile-derived role must still be surfaced for downstream union'
        )

    def test_seniority_jd_wins(self):
        # Profile says senior, JD says junior — JD level is what the
        # candidate is APPLYING for, so retrieval should reflect that.
        from profiles.services import role_classifier
        profile_cls = _fake_role_classification('Backend Engineer', seniority='senior')
        jd_cls = _fake_role_classification('Backend Engineer', seniority='junior')
        with patch.object(role_classifier, 'detect_role_seniority',
                          return_value=profile_cls), \
             patch.object(role_classifier, 'classify_jd_role',
                          return_value=jd_cls):
            out = role_classifier.classify_for_jd(
                {'full_name': 'X', 'skills': ['Python']}, 'jd text',
            )
        assert out.seniority == 'junior'

    def test_region_overlay_from_jd_text(self):
        from profiles.services import role_classifier
        profile_cls = _fake_role_classification('Backend Engineer')
        jd_cls = _fake_role_classification('Backend Engineer', seniority='mid')
        with patch.object(role_classifier, 'detect_role_seniority',
                          return_value=profile_cls), \
             patch.object(role_classifier, 'classify_jd_role',
                          return_value=jd_cls):
            out = role_classifier.classify_for_jd(
                {'full_name': 'X', 'skills': ['Python']},
                'Position: Backend Engineer, location Cairo, Egypt',
            )
        assert out.region == 'mena'

    def test_log_emits_both_classifications(self, caplog):
        from profiles.services import role_classifier
        profile_cls = _fake_role_classification('Data Scientist', seniority='mid')
        jd_cls = _fake_role_classification('AI/ML Engineer', seniority='junior')
        with patch.object(role_classifier, 'detect_role_seniority',
                          return_value=profile_cls), \
             patch.object(role_classifier, 'classify_jd_role',
                          return_value=jd_cls):
            with caplog.at_level(logging.INFO,
                                  logger='profiles.services.role_classifier'):
                role_classifier.classify_for_jd(
                    {'full_name': 'X', 'skills': ['Python']}, 'AI JD',
                )
        line = next(
            (r.message for r in caplog.records
             if 'classify_for_jd:' in r.message),
            None,
        )
        assert line is not None, 'expected an INFO line from classify_for_jd'
        assert "profile_role='Data Scientist'" in line
        assert "jd_role='AI/ML Engineer'" in line
        assert "primary_role='AI/ML Engineer'" in line

    def test_empty_profile_still_runs_jd_classifier(self):
        # Old behaviour: empty profile returned a stub immediately. New
        # behaviour: still falls back for the profile side, but the JD
        # classifier ALWAYS runs so the JD role gets honored.
        from profiles.services import role_classifier
        jd_cls = _fake_role_classification('DevOps Engineer', seniority='mid')
        with patch.object(role_classifier, 'classify_jd_role',
                          return_value=jd_cls):
            out = role_classifier.classify_for_jd({}, 'DevOps JD text')
        assert out.primary_role == 'DevOps Engineer'
        # Fallback profile_role is the documented default.
        assert getattr(out, 'profile_role', None) == 'Software Engineer'
