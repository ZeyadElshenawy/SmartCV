"""Unit tests for resumes/services/inclusion_planner.py.

PR 2b — Step 3 Fix A (JD-aware base-tech rescue in
_discriminating_tech_overlap), Fix B (fuzzy cert matching), Fix C
(cert plan-filtering policy).
"""
from __future__ import annotations

import pytest


def _canon(s):
    """Local canonicaliser mirroring inclusion_planner._canonical so the
    fixture skill set matches the planner's internal form."""
    return ''.join(c.lower() for c in (s or '') if c.isalnum())


# --------------------------------------------------------------------------
# Fix A — _discriminating_tech_overlap, JD-aware base-tech rescue
# --------------------------------------------------------------------------

class TestDiscriminatingTechOverlapFixA:
    """The base-tech filter is now JD-aware: a tech token in
    _BASE_TECH_CANON (Python, Jupyter, HTML/CSS/JS, Git, …) is COUNTED
    when the JD explicitly listed it, and only filtered when the JD
    didn't ask for it. Returns (count, jd_rescued_tokens)."""

    def test_project_with_tf_keras_cnn_kept_when_jd_lists_tensorflow(self):
        # Real Zeyad Brain Tumor case: tech=['TensorFlow','Keras','CNN'],
        # JD must-have includes 'TensorFlow'. None of these are base
        # tech, so all three would count if all three are in JD; here
        # only TensorFlow is, so disc_overlap == 1.
        from resumes.services.inclusion_planner import _discriminating_tech_overlap
        jd = {_canon('TensorFlow')}
        count, rescued = _discriminating_tech_overlap(
            ['TensorFlow', 'Keras', 'CNN'], jd,
        )
        assert count >= 1, 'TensorFlow in JD must-have should give disc>=1'
        # No base-tech rescue (no tokens are in _BASE_TECH_CANON).
        assert rescued == []

    def test_project_with_tf_keras_cnn_dropped_when_jd_unrelated(self):
        # Same project, JD asks for backend/django — no overlap.
        from resumes.services.inclusion_planner import _discriminating_tech_overlap
        jd = {_canon('Django'), _canon('PostgreSQL')}
        count, rescued = _discriminating_tech_overlap(
            ['TensorFlow', 'Keras', 'CNN'], jd,
        )
        assert count == 0
        assert rescued == []

    def test_pure_base_tech_project_not_promoted_by_jd(self):
        # Project tech is ONLY base-tech (Python, Jupyter), JD doesn't
        # name either as a requirement. Result: 0 — the new rule must
        # not accidentally promote base-tech-only projects.
        from resumes.services.inclusion_planner import _discriminating_tech_overlap
        jd = {_canon('TensorFlow'), _canon('Kubernetes')}
        count, rescued = _discriminating_tech_overlap(
            ['Python', 'Jupyter Notebook'], jd,
        )
        assert count == 0
        assert rescued == []

    def test_per_tech_evaluation_not_all_or_nothing(self):
        # tech=['Python','TensorFlow'], JD must-have='TensorFlow' only.
        # Expected: TensorFlow counts (in JD, not base). Python doesn't
        # count (base AND not in JD). disc == 1.
        from resumes.services.inclusion_planner import _discriminating_tech_overlap
        jd = {_canon('TensorFlow')}
        count, rescued = _discriminating_tech_overlap(
            ['Python', 'TensorFlow'], jd,
        )
        assert count == 1
        # Python was NOT in JD, so it wasn't rescued — just filtered.
        assert rescued == []

    def test_base_tech_rescue_when_jd_explicitly_lists_it(self):
        # tech=['Python','TensorFlow'], JD asks for BOTH. The new rule:
        # Python IS in JD → counts (rescued); TensorFlow IS in JD and
        # NOT base → counts. Total disc=2, Python in rescued list.
        from resumes.services.inclusion_planner import _discriminating_tech_overlap
        jd = {_canon('Python'), _canon('TensorFlow')}
        count, rescued = _discriminating_tech_overlap(
            ['Python', 'TensorFlow'], jd,
        )
        assert count == 2
        assert 'Python' in rescued, (
            'Python should be rescued — JD explicitly lists it'
        )
        # TensorFlow was never filterable → not in the rescued list.
        assert 'TensorFlow' not in rescued

    def test_non_list_input_returns_zero_empty(self):
        from resumes.services.inclusion_planner import _discriminating_tech_overlap
        assert _discriminating_tech_overlap(None, {'python'}) == (0, [])
        assert _discriminating_tech_overlap('Python', {'python'}) == (0, [])


# --------------------------------------------------------------------------
# Fix B — fuzzy cert matching (substring containment with 4-char minimum)
# --------------------------------------------------------------------------

class _FakeJob:
    """Minimal stand-in for jobs.models.Job that build_inclusion_plan reads.
    Only the two skill-tier fields are accessed."""
    def __init__(self, must_have=None, nice_to_have=None):
        self.extracted_skills_tiers = {
            'must_have': list(must_have or []),
            'nice_to_have': list(nice_to_have or []),
        }
        self.extracted_skills = list(must_have or []) + list(nice_to_have or [])


class _FakeGap:
    """Stand-in for analysis.models.GapAnalysis. Only the matched_* lists
    are read by build_inclusion_plan."""
    def __init__(self, matched_must=None, matched_nice=None, matched_v1=None,
                 missing_must=None):
        self.matched_must_have = [{'name': n} for n in (matched_must or [])]
        self.matched_nice_to_have = [{'name': n} for n in (matched_nice or [])]
        self.matched_skills = list(matched_v1 or [])
        self.missing_must_have = list(missing_must or [])


class _FakeProfile:
    """Minimal stand-in for UserProfile. Only data_content is read."""
    def __init__(self, certifications=None, **extras):
        self.data_content = {'certifications': list(certifications or []), **extras}


def _build_plan(certs, matched_skills, must_have=None, nice_to_have=None):
    """Convenience wrapper around build_inclusion_plan with empty
    per_skill_ev (so retrieval doesn't contribute)."""
    from resumes.services.inclusion_planner import build_inclusion_plan
    profile = _FakeProfile(certifications=[{'name': n} for n in certs])
    job = _FakeJob(must_have=must_have or [], nice_to_have=nice_to_have or [])
    gap = _FakeGap(matched_must=matched_skills)
    return build_inclusion_plan(profile, job, gap, per_skill_ev={})


class TestFuzzyCertMatchingFixB:
    """Cert keeping now accepts substring containment in EITHER direction
    on the canonical form, with a 4-char minimum on the skill canon to
    keep short acronyms (AI/ML) from triggering false matches."""

    def test_nlp_skill_matches_nlp_in_tensorflow_cert(self):
        plan = _build_plan(
            certs=['Natural Language Processing in TensorFlow'],
            matched_skills=['Natural Language Processing'],
        )
        assert 'Natural Language Processing in TensorFlow' in plan.certifications

    def test_computer_vision_skill_matches_long_cert_name(self):
        plan = _build_plan(
            certs=['AI Professional Level | Deep Learning & Computer Vision'],
            matched_skills=['Computer Vision'],
        )
        assert 'AI Professional Level | Deep Learning & Computer Vision' in plan.certifications

    def test_python_fundamentals_dropped_when_skill_is_java(self):
        # No canonical overlap meeting the 4-char threshold.
        plan = _build_plan(
            certs=['Python Programming Fundamentals'],
            matched_skills=['Java'],
        )
        assert 'Python Programming Fundamentals' not in plan.certifications

    def test_two_char_skill_does_not_fuzzy_trigger(self):
        # Skill canon='ai' is only 2 chars → minimum-length guard kicks
        # in → cert NOT kept by fuzzy rule. Cert should drop unless
        # there's a retrieval surface (there isn't in this fixture).
        plan = _build_plan(
            certs=['AI Associate Level'],
            matched_skills=['AI'],
        )
        assert 'AI Associate Level' not in plan.certifications

    def test_exact_canonical_match_still_works(self):
        # Backward compat — the original strict-equality path is preserved.
        plan = _build_plan(
            certs=['Deep Learning'],
            matched_skills=['Deep Learning'],
        )
        assert 'Deep Learning' in plan.certifications


# --------------------------------------------------------------------------
# Fix C — trim_certs_to_plan, Option X (cap-only + JD-relevance ranking)
# --------------------------------------------------------------------------

class _MiniPlan:
    """Minimal stand-in carrying just the certifications attribute that
    trim_certs_to_plan inspects via getattr."""
    def __init__(self, certifications):
        self.certifications = list(certifications or [])


class TestTrimCertsFixC:
    """trim_certs_to_plan: under the cap, NOTHING changes (Round 1.5
    invariant). Over the cap, plan-membership certs win the surviving
    slots and low-signal ones get demoted into the truncation tail."""

    def test_under_cap_keeps_everything_in_original_order(self):
        # Round 1.5 invariant — fewer than _CERT_CAP certs means
        # no reranking, no drops, order preserved.
        from resumes.services.resume_normalizer import trim_certs_to_plan
        resume = {'certifications': [
            {'name': 'A'}, {'name': 'B'}, {'name': 'C'},
        ]}
        plan = _MiniPlan(certifications=['Z'])  # totally unrelated
        out = trim_certs_to_plan(resume, plan)
        names = [c['name'] for c in out['certifications']]
        assert names == ['A', 'B', 'C']

    def test_over_cap_in_plan_cert_survives(self):
        # 9 certs, cap is 8. The 9th cert is the ONLY one in the plan
        # → it must survive (replaces a low-signal one in the tail).
        from resumes.services.resume_normalizer import trim_certs_to_plan, _CERT_CAP
        assert _CERT_CAP == 8, 'test assumes _CERT_CAP == 8'
        resume = {'certifications': [
            {'name': f'Low {i}'} for i in range(1, 9)  # 8 low-signal
        ] + [
            {'name': 'Plan Cert'},                     # 9th, in plan
        ]}
        plan = _MiniPlan(certifications=['Plan Cert'])
        out = trim_certs_to_plan(resume, plan)
        names = [c['name'] for c in out['certifications']]
        assert len(names) == 8
        assert 'Plan Cert' in names, (
            'plan-membership cert must survive truncation'
        )
        # In-plan cert goes FIRST in the truncated output.
        assert names[0] == 'Plan Cert'

    def test_over_cap_non_plan_certs_demoted_to_tail(self):
        # 10 certs: 3 in plan + 7 not. Cap=8 → all 3 plan certs survive
        # plus 5 of the 7 non-plan. The 2 demoted are the LAST in
        # original order.
        from resumes.services.resume_normalizer import trim_certs_to_plan
        resume = {'certifications': (
            [{'name': f'L{i}'} for i in range(1, 8)]   # L1..L7  (7 low-sig)
            + [{'name': 'P1'}, {'name': 'P2'}, {'name': 'P3'}]  # 3 plan
        )}
        plan = _MiniPlan(certifications=['P1', 'P2', 'P3'])
        out = trim_certs_to_plan(resume, plan)
        names = [c['name'] for c in out['certifications']]
        assert len(names) == 8
        for p in ('P1', 'P2', 'P3'):
            assert p in names
        # The 2 demoted should be the original-order TAIL of the
        # non-plan group: L6 and L7.
        assert 'L6' not in names
        assert 'L7' not in names

    def test_cap_still_applies_when_plan_is_empty(self):
        # With no plan signal, ranking is a no-op; cap still applies in
        # original order (no certs are "in plan" → all go to out_of_plan
        # in original order → trimmed to _CERT_CAP from the tail).
        from resumes.services.resume_normalizer import trim_certs_to_plan
        resume = {'certifications': [
            {'name': f'C{i}'} for i in range(1, 11)  # 10 certs
        ]}
        out = trim_certs_to_plan(resume, _MiniPlan(certifications=[]))
        names = [c['name'] for c in out['certifications']]
        assert names == [f'C{i}' for i in range(1, 9)]

    def test_in_plan_ordering_follows_plan_order(self):
        # Two in-plan certs but plan lists them in a specific order:
        # the surviving sequence should match plan order.
        from resumes.services.resume_normalizer import trim_certs_to_plan
        resume = {'certifications': (
            [{'name': f'L{i}'} for i in range(1, 8)]  # 7 low-sig
            + [{'name': 'PlanB'}, {'name': 'PlanA'}]   # 2 plan (reverse order)
        )}
        plan = _MiniPlan(certifications=['PlanA', 'PlanB'])
        out = trim_certs_to_plan(resume, plan)
        names = [c['name'] for c in out['certifications']]
        assert names[0] == 'PlanA', f'plan order should win, got {names}'
        assert names[1] == 'PlanB', f'plan order should win, got {names}'
