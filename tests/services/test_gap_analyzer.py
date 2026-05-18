"""Unit tests for analysis/services/gap_analyzer.py.

PR 3e — extend deterministic evidence linking beyond bullet text.
``_collect_profile_evidence`` now scans project ``technologies`` arrays,
certification names (substring with 4-char minimum), experience tech
tags, and the candidate's skills array (with corroboration).
``_reconcile_tier`` rescues skills with deterministic evidence that the
LLM either missed entirely or marked as missing.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixture — Zeyad-shaped profile minimised to the fields the rescue logic
# touches. Real profile would have more, but more fields don't change rescue
# behavior; smaller fixture keeps the test diffs readable.
# ---------------------------------------------------------------------------

def _profile():
    return {
        'skills': [
            {'name': 'TensorFlow'},
            {'name': 'Python'},
            {'name': 'NLP'},
            {'name': 'Keras'},
            {'name': 'Rust'},  # unsupported — no project/cert mentions
        ],
        'projects': [
            {
                'name': 'Brain Tumor Classification',
                'description': 'CNN-based classification of MRI scans.',
                'technologies': ['TensorFlow', 'Keras', 'CNN', 'Python'],
                'highlights': ['Trained on 3000 MRI images', 'Achieved 94% accuracy'],
            },
            {
                'name': 'Customer Segmentation',
                'description': 'K-means clustering for customer segments.',
                'technologies': ['Python', 'scikit-learn', 'Pandas'],
                'highlights': ['Validated k=3 clusters'],
            },
        ],
        'certifications': [
            {'name': 'Natural Language Processing in TensorFlow',
             'issuer': 'DeepLearning.AI'},
            {'name': 'Applied Machine Learning in Python', 'issuer': 'Coursera'},
        ],
        'experiences': [],
    }


# ---------------------------------------------------------------------------
# Group A — _collect_profile_evidence (pure helper)
# ---------------------------------------------------------------------------

class TestCollectProfileEvidence:
    """The new deterministic evidence collector. Covers all four sources
    plus case insensitivity, length floor, and the skills-array
    corroboration rule."""

    def test_a_project_tech_match(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        ev = _collect_profile_evidence('TensorFlow', _profile())
        sources = {(e['source'], e['ref']) for e in ev}
        assert ('projects', 'Brain Tumor Classification') in sources
        # Also picks up the cert that contains "TensorFlow".
        assert ('certifications', 'Natural Language Processing in TensorFlow') in sources

    def test_c_cert_substring_adequate_length(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        # 'TensorFlow' canon is 10 chars — well over the 4-char floor.
        ev = _collect_profile_evidence('TensorFlow', _profile())
        cert_hits = [e for e in ev if e['source'] == 'certifications']
        assert any(e['ref'] == 'Natural Language Processing in TensorFlow' for e in cert_hits)

    def test_b_cert_substring_with_long_skill_name(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        # 'Natural Language Processing' canon: 'naturallanguageprocessing'
        # (25 chars, well over 4-char floor).
        ev = _collect_profile_evidence('Natural Language Processing', _profile())
        cert_hits = [e for e in ev if e['source'] == 'certifications']
        assert any(e['ref'] == 'Natural Language Processing in TensorFlow' for e in cert_hits)

    def test_b_short_skill_rejects_cert_substring(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        # 'NLP' canon is 'nlp' — 3 chars, below the 4-char floor.
        # Even though 'nlp' is a substring of nothing relevant, the
        # length floor must prevent any future cert with 'nlp' in its
        # name (e.g. an URL-fragment cert) from falsely matching.
        # Here we verify the cert-substring path does NOT fire for NLP.
        ev = _collect_profile_evidence('NLP', _profile())
        cert_hits = [e for e in ev if e['source'] == 'certifications']
        assert cert_hits == [], (
            f'NLP should not pass the cert substring rule (canon < 4 chars); got {cert_hits!r}'
        )

    def test_d_skills_array_corroborated(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        # 'Keras' is in skills array AND in Brain Tumor project tech.
        # Corroboration rule: when project_tech evidence exists, the
        # skills-array entry is also surfaced.
        ev = _collect_profile_evidence('Keras', _profile())
        sources = [e['source'] for e in ev]
        assert 'projects' in sources  # project_tech evidence
        assert 'skills' in sources    # corroborated skills-array entry

    def test_e_skills_array_without_corroboration_rejected(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        # 'Rust' is in skills array but has no project/cert/exp mention.
        # Anti-claim-stuffing rule: the skills-array entry is NOT
        # sufficient on its own.
        ev = _collect_profile_evidence('Rust', _profile())
        assert ev == [], (
            f'Rust has no corroborating evidence — must return empty; got {ev!r}'
        )

    def test_f_case_insensitivity(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        ev_lower = _collect_profile_evidence('tensorflow', _profile())
        ev_upper = _collect_profile_evidence('TENSORFLOW', _profile())
        ev_mixed = _collect_profile_evidence('TensorFlow', _profile())
        # All three should return the same evidence (same sources, refs).
        def _key(ev):
            return sorted((e['source'], e['ref']) for e in ev)
        assert _key(ev_lower) == _key(ev_upper) == _key(ev_mixed)

    def test_g_no_evidence_returns_empty(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        # Skill not anywhere in profile.
        ev = _collect_profile_evidence('COBOL', _profile())
        assert ev == []

    def test_empty_skill_name_returns_empty(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        assert _collect_profile_evidence('', _profile()) == []
        assert _collect_profile_evidence(None, _profile()) == []

    def test_empty_profile_returns_empty(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        assert _collect_profile_evidence('TensorFlow', {}) == []
        assert _collect_profile_evidence('TensorFlow', None) == []

    def test_experience_tech_array(self):
        from analysis.services.gap_analyzer import _collect_profile_evidence
        prof = {
            'projects': [], 'certifications': [], 'skills': [],
            'experiences': [
                {'title': 'ML Engineer', 'company': 'Acme',
                 'technologies': ['PyTorch', 'CUDA']},
            ],
        }
        ev = _collect_profile_evidence('PyTorch', prof)
        assert any(e['source'] == 'experience' for e in ev)

    def test_comma_separated_tech_string(self):
        """CV parser sometimes emits ``technologies`` as a comma-separated
        string instead of a list. The collector should handle both."""
        from analysis.services.gap_analyzer import _collect_profile_evidence
        prof = {
            'projects': [{
                'name': 'Demo',
                'technologies': 'TensorFlow, Keras, Python',
            }],
            'certifications': [], 'skills': [], 'experiences': [],
        }
        ev = _collect_profile_evidence('TensorFlow', prof)
        assert any(e['source'] == 'projects' and e['ref'] == 'Demo' for e in ev)


# ---------------------------------------------------------------------------
# Group B — _reconcile_tier integration with the rescue path
# ---------------------------------------------------------------------------

def _matched(name, source='skills', quote='listed'):
    from profiles.services.schemas import MatchedSkill
    return MatchedSkill(name=name, evidence_source=source, evidence_quote=quote)


def _missing(name, reason='No related evidence found in profile'):
    from profiles.services.schemas import MissingSkill
    return MissingSkill(name=name, source_quote='', proximity=0.0,
                        proximity_reason=reason, bridge_hint=None)


def _tier(matched_must=None, matched_nice=None, missing_must=None,
          missing_nice=None, soft_skill_gaps=None):
    from profiles.services.schemas import TieredGapAnalysisResult
    return TieredGapAnalysisResult(
        matched_must_have=list(matched_must or []),
        matched_nice_to_have=list(matched_nice or []),
        missing_must_have=list(missing_must or []),
        missing_nice_to_have=list(missing_nice or []),
        soft_skill_gaps=list(soft_skill_gaps or []),
    )


class TestReconcileTierRescue:
    """PR 3e — _reconcile_tier now promotes LLM-marked-missing skills AND
    rescues unaccounted-for skills when deterministic evidence exists."""

    def test_promotes_llm_marked_missing_with_evidence(self, caplog):
        """The Zeyad case: LLM marked TensorFlow as missing_must_have, but
        the project tech stack evidences it. Rescue promotes to matched."""
        from analysis.services.gap_analyzer import _reconcile_tier
        import logging

        result = _tier(missing_must=[_missing('TensorFlow')])
        with caplog.at_level(logging.INFO, logger='analysis.services.gap_analyzer'):
            out = _reconcile_tier(result, ['TensorFlow'], [], profile_data=_profile())

        tf_matched = [m for m in out.matched_must_have if m.name == 'TensorFlow']
        assert len(tf_matched) == 1, 'TensorFlow should be rescued into matched'
        assert tf_matched[0].evidence_quote, 'rescued skill must carry evidence_quote'
        assert not [m for m in out.missing_must_have if m.name == 'TensorFlow'], (
            'TensorFlow should no longer be in missing_must_have'
        )
        # Log line surfaces the rescue.
        rescued_logs = [r.message for r in caplog.records if 'rescued must-have' in r.message]
        assert rescued_logs, f'expected a "rescued must-have" log line; got {caplog.messages!r}'

    def test_rescues_unaccounted_with_evidence(self, caplog):
        """The LLM didn't include 'scikit-learn' in either matched or
        missing; it was unaccounted. Customer Segmentation project's
        tech stack has it — rescue into matched."""
        from analysis.services.gap_analyzer import _reconcile_tier
        import logging

        result = _tier()  # LLM produced nothing for this skill
        with caplog.at_level(logging.INFO, logger='analysis.services.gap_analyzer'):
            out = _reconcile_tier(result, ['scikit-learn'], [], profile_data=_profile())

        matched_names = [m.name for m in out.matched_must_have]
        assert 'scikit-learn' in matched_names
        assert not [m for m in out.missing_must_have if m.name == 'scikit-learn']
        # Log line for unaccounted rescue.
        rescued_logs = [r.message for r in caplog.records if 'matched unaccounted must-have' in r.message]
        assert rescued_logs

    def test_unaccounted_without_evidence_stays_missing(self):
        """Java is in the JD but not in profile anywhere — should remain
        in missing_must_have. Anti-rescue guard."""
        from analysis.services.gap_analyzer import _reconcile_tier
        result = _tier()
        out = _reconcile_tier(result, ['Java'], [], profile_data=_profile())
        missing_names = [m.name for m in out.missing_must_have]
        matched_names = [m.name for m in out.matched_must_have]
        assert 'Java' in missing_names
        assert 'Java' not in matched_names

    def test_skills_array_alone_does_not_trigger_rescue(self):
        """Rust is in the candidate's skills array but with no
        corroboration — must NOT be rescued. Preserves the
        anti-claim-stuffing rule."""
        from analysis.services.gap_analyzer import _reconcile_tier
        result = _tier()
        out = _reconcile_tier(result, ['Rust'], [], profile_data=_profile())
        missing_names = [m.name for m in out.missing_must_have]
        assert 'Rust' in missing_names

    def test_rescue_applies_to_nice_to_have_tier(self):
        from analysis.services.gap_analyzer import _reconcile_tier
        result = _tier()
        out = _reconcile_tier(result, [], ['Keras'], profile_data=_profile())
        matched_names = [m.name for m in out.matched_nice_to_have]
        assert 'Keras' in matched_names

    def test_backward_compat_when_profile_data_not_passed(self):
        """``profile_data`` defaults to None — older call sites still
        get the original 'reconcile unaccounted as missing' behavior
        without crashing."""
        from analysis.services.gap_analyzer import _reconcile_tier
        result = _tier()
        out = _reconcile_tier(result, ['TensorFlow'], [])
        # Without profile_data, no rescue — TensorFlow stays missing.
        assert 'TensorFlow' in [m.name for m in out.missing_must_have]

    def test_zeyad_end_to_end_via_reconcile(self):
        """End-to-end-ish check at the reconcile layer: simulate the LLM
        producing nothing useful (all skills unaccounted), then verify
        rescue promotes the supported ones and leaves Java in missing."""
        from analysis.services.gap_analyzer import _reconcile_tier
        must_skills = ['TensorFlow', 'PyTorch', 'scikit-learn',
                       'Natural Language Processing', 'Python', 'Java']
        # Add PyTorch to the profile so we have something to test
        # ALL of the JD's ML skills against.
        prof = _profile()
        prof['projects'].append({
            'name': 'Side Project', 'technologies': ['PyTorch'],
        })
        result = _tier()  # LLM gave up
        out = _reconcile_tier(result, must_skills, [], profile_data=prof)
        matched_names = {m.name for m in out.matched_must_have}
        missing_names = {m.name for m in out.missing_must_have}
        for ml_skill in ['TensorFlow', 'PyTorch', 'scikit-learn',
                         'Natural Language Processing', 'Python']:
            assert ml_skill in matched_names, (
                f'{ml_skill!r} should be rescued; matched={matched_names} missing={missing_names}'
            )
        assert 'Java' in missing_names
        assert 'Java' not in matched_names
