"""Unit tests for resumes/services/resume_normalizer.py.

PR 1 — Fix 3 (consolidate_coursework rejection conditions).
"""
from __future__ import annotations

import pytest


def _consolidate(bullets):
    """Run consolidate_coursework over a single experience entry and
    return the resulting description list."""
    from resumes.services.resume_normalizer import consolidate_coursework
    resume = {'experience': [{'description': list(bullets)}]}
    out = consolidate_coursework(resume)
    return out['experience'][0]['description']


def _starts_with_coursework_line(bullets) -> bool:
    return any(
        isinstance(b, str) and b.startswith('Coursework included:')
        for b in bullets
    )


class TestConsolidateCoursework:
    """Fix 3 — consolidate_coursework now refuses to collapse runs that
    contain technical tokens / URLs / parenthesised tech-lists, and
    requires at least 3 consecutive coursework-like bullets before it
    will collapse anything."""

    def test_three_real_courses_collapse(self):
        # Three plain noun-phrase courses → one consolidated bullet.
        out = _consolidate([
            'Operating Systems',
            'Computer Networks',
            'Distributed Systems',
        ])
        assert _starts_with_coursework_line(out), (
            f'expected a "Coursework included:" line, got: {out!r}'
        )
        # The consolidated line names every course.
        line = next(b for b in out if b.startswith('Coursework included:'))
        for name in ('Operating Systems', 'Computer Networks', 'Distributed Systems'):
            assert name in line

    def test_technical_bullets_with_parens_do_not_collapse(self):
        # Capstone deliverables with parens / tech tokens — Fix 3
        # forbids collapsing any of these.
        bullets = [
            'Terraform Infrastructure as Code (EC2, S3, IAM modules)',
            'Multi-channel alerting (Slack, Email, Discord)',
            '3 Grafana dashboards (19 panels)',
        ]
        out = _consolidate(bullets)
        assert not _starts_with_coursework_line(out), (
            f'capstone bullets should not be collapsed, got: {out!r}'
        )
        # Each input bullet survives verbatim.
        for b in bullets:
            assert b in out

    def test_two_bullets_below_min_run_do_not_collapse(self):
        # Round 1.5.2 used MIN_RUN=2; PR1 Fix 3 raised it to 3 to err
        # on the side of NOT collapsing.
        bullets = ['Database Systems', 'Algorithms']
        out = _consolidate(bullets)
        assert not _starts_with_coursework_line(out), (
            f'two-bullet run should NOT collapse (MIN=3), got: {out!r}'
        )
        assert out == ['Database Systems', 'Algorithms']

    def test_mixed_technical_then_three_courses_collapses_only_courses(self):
        # The technical bullet stays put. The three plain course names
        # that follow it form their own run of 3 and collapse.
        bullets = [
            'Terraform Infrastructure as Code (EC2, S3, IAM modules)',
            'Operating Systems',
            'Computer Networks',
            'Distributed Systems',
        ]
        out = _consolidate(bullets)
        # Technical bullet survives.
        assert 'Terraform Infrastructure as Code (EC2, S3, IAM modules)' in out
        # Plain run collapsed.
        assert _starts_with_coursework_line(out)

    def test_bullet_with_url_does_not_collapse(self):
        bullets = [
            'See https://github.com/me/project',
            'Database Systems',
            'Algorithms',
        ]
        out = _consolidate(bullets)
        # URL bullet survives + the 2 plain courses don't collapse
        # because MIN=3.
        assert 'See https://github.com/me/project' in out
        assert not _starts_with_coursework_line(out)

    def test_bullet_with_tech_token_does_not_collapse(self):
        # Even a 1-word bullet containing a tech token (e.g. "Docker")
        # is preserved — never collapsed into a "Coursework" line.
        bullets = ['Docker', 'Kubernetes', 'Terraform']
        out = _consolidate(bullets)
        assert not _starts_with_coursework_line(out)
        for b in bullets:
            assert b in out


class TestCourseworkRejectReason:
    """The split-out helper returns the rule-id string that fired, used
    by the consolidator's INFO log. Pinning each new rule here so a
    regression flips one of the reasons to None."""

    @pytest.mark.parametrize('text,reason', [
        ('Terraform Infrastructure as Code', 'technical_token'),
        ('Built with Docker', 'technical_token'),
        # Real-life capstone bullet — first matching technical token (AWS) wins.
        ('Provisioned (AWS, GCP, AZURE)', 'technical_token'),
        ('https://github.com/me/project', 'url'),
        ('see github.com/foo', 'url'),
        # Pure parens-techlist match (no tech tokens, no digits short-
        # circuiting): all-caps acronym list inside parens.
        ('Provisioned (IAM, ECS, SQS)', 'parens_techlist'),
    ])
    def test_new_rules_fire_with_named_reason(self, text, reason):
        from resumes.services.resume_normalizer import _coursework_reject_reason
        assert _coursework_reject_reason(text) == reason

    def test_lowercase_inside_parens_falls_through_to_digit_check(self):
        # "(EC2, S3, IAM modules)" — lowercase "modules" breaks the
        # strict parens_techlist regex, but the digits in EC2/S3 still
        # disqualify the bullet via the older contains_digit rule.
        # Semantically correct rejection — just under a different rule.
        from resumes.services.resume_normalizer import _coursework_reject_reason
        assert _coursework_reject_reason('(EC2, S3, IAM modules)') == 'contains_digit'

    def test_real_course_passes(self):
        from resumes.services.resume_normalizer import _coursework_reject_reason
        assert _coursework_reject_reason('Operating Systems') is None


# ---------------------------------------------------------------------------
# PR 3a — Plan-as-contract restoration (restore_plan_projects + restore_plan_certs)
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _plan(projects=None, certifications=None, skills_to_list=None):
    """Minimal duck-typed InclusionPlan stand-in.

    The real InclusionPlan is a dataclass with ~10 fields; restoration only
    reads `.projects` and `.certifications`, so we use SimpleNamespace.
    Projects are themselves duck-typed (`.name` attribute is all that's
    accessed)."""
    proj_objs = [SimpleNamespace(name=n) for n in (projects or [])]
    return SimpleNamespace(
        projects=proj_objs,
        certifications=list(certifications or []),
        skills_to_list=list(skills_to_list or []),
    )


def _profile(projects=None, certifications=None):
    return {
        'projects': list(projects or []),
        'certifications': list(certifications or []),
    }


class TestRestorePlanProjects:
    """PR 3a — restore_plan_projects re-injects plan-ranked projects the
    LLM dropped. Cap, fuzzy-skip already-present, source-CV verbatim."""

    def test_a_restores_dropped_projects_up_to_plan_size(self):
        from resumes.services.resume_normalizer import restore_plan_projects
        resume = {'projects': [
            {'name': 'SmartCV', 'description': 'LLM bullet 1'},
            {'name': 'Healthcare Prediction', 'description': 'LLM bullet 2'},
        ]}
        profile = _profile(projects=[
            {'name': 'SmartCV', 'description': 'src 1'},
            {'name': 'Healthcare Prediction', 'description': 'src 2'},
            {'name': 'Brain Tumor CNN', 'description': 'src 3',
             'technologies': ['TensorFlow', 'Keras', 'CNN']},
            {'name': 'End-to-End Pipeline', 'description': 'src 4',
             'technologies': ['PySpark', 'ML']},
        ])
        plan = _plan(projects=[
            'SmartCV', 'Healthcare Prediction', 'Brain Tumor CNN', 'End-to-End Pipeline'
        ])
        out = restore_plan_projects(resume, plan, profile)
        names = [p['name'] for p in out['projects']]
        assert 'Brain Tumor CNN' in names
        assert 'End-to-End Pipeline' in names
        # SmartCV bullet preserved verbatim from LLM (not overwritten).
        smart = next(p for p in out['projects'] if p['name'] == 'SmartCV')
        assert smart['description'] == 'LLM bullet 1'
        # Restored project uses source-CV description verbatim.
        brain = next(p for p in out['projects'] if p['name'] == 'Brain Tumor CNN')
        assert brain['description'] == 'src 3'
        assert brain['technologies'] == ['TensorFlow', 'Keras', 'CNN']

    def test_b_no_restore_when_at_cap(self):
        from resumes.services.resume_normalizer import (
            restore_plan_projects, _PROJECT_CAP,
        )
        # Already at _PROJECT_CAP (6) entries from LLM.
        resume = {'projects': [
            {'name': f'P{i}', 'description': f'd{i}'} for i in range(_PROJECT_CAP)
        ]}
        profile = _profile(projects=[
            {'name': f'P{i}', 'description': f's{i}'} for i in range(_PROJECT_CAP)
        ] + [{'name': 'Extra', 'description': 'should not be restored'}])
        plan = _plan(projects=[f'P{i}' for i in range(_PROJECT_CAP)] + ['Extra'])
        out = restore_plan_projects(resume, plan, profile)
        assert len(out['projects']) == _PROJECT_CAP
        assert 'Extra' not in [p['name'] for p in out['projects']]

    def test_c_restoration_stops_at_cap(self):
        from resumes.services.resume_normalizer import (
            restore_plan_projects, _PROJECT_CAP,
        )
        # 5 in resume, 3 candidates to restore -> should restore exactly 1.
        resume = {'projects': [
            {'name': f'P{i}', 'description': f'd{i}'}
            for i in range(_PROJECT_CAP - 1)
        ]}
        profile = _profile(projects=[
            {'name': f'P{i}', 'description': f's{i}'}
            for i in range(_PROJECT_CAP - 1)
        ] + [
            {'name': 'CandA', 'description': 'sA'},
            {'name': 'CandB', 'description': 'sB'},
            {'name': 'CandC', 'description': 'sC'},
        ])
        plan = _plan(projects=[f'P{i}' for i in range(_PROJECT_CAP - 1)]
                              + ['CandA', 'CandB', 'CandC'])
        out = restore_plan_projects(resume, plan, profile)
        assert len(out['projects']) == _PROJECT_CAP
        # First restoration in plan order is CandA.
        assert out['projects'][-1]['name'] == 'CandA'
        # CandB / CandC not restored — cap hit.
        names = [p['name'] for p in out['projects']]
        assert 'CandB' not in names and 'CandC' not in names

    def test_d_plan_project_missing_from_source_logs_warning(self, caplog):
        from resumes.services.resume_normalizer import restore_plan_projects
        import logging
        resume = {'projects': []}
        profile = _profile(projects=[
            {'name': 'RealProject', 'description': 'src'},
        ])
        plan = _plan(projects=['RealProject', 'Ghost', 'Phantom'])
        with caplog.at_level(logging.WARNING, logger='resumes.services.resume_normalizer'):
            out = restore_plan_projects(resume, plan, profile)
        names = [p['name'] for p in out['projects']]
        assert names == ['RealProject']  # Ghost + Phantom skipped, not added
        warns = [r.message for r in caplog.records
                 if 'not in source profile' in r.message]
        assert len(warns) == 2  # one per missing plan project

    def test_e_empty_plan_no_op(self):
        from resumes.services.resume_normalizer import restore_plan_projects
        resume = {'projects': [{'name': 'A', 'description': 'd'}]}
        plan = _plan(projects=[])
        out = restore_plan_projects(resume, plan, _profile(projects=[]))
        assert out == resume

    def test_fuzzy_skip_when_llm_kept_with_minor_rename(self):
        """LLM renamed 'BRAIN TUMOR CLASSIFICATION APP' to 'Brain Tumor Classification'
        — canonical form matches, restoration should NOT re-add."""
        from resumes.services.resume_normalizer import restore_plan_projects
        resume = {'projects': [
            {'name': 'Brain Tumor Classification', 'description': 'LLM polished'},
        ]}
        profile = _profile(projects=[
            {'name': 'BRAIN TUMOR CLASSIFICATION APP', 'description': 'src'},
        ])
        plan = _plan(projects=['BRAIN TUMOR CLASSIFICATION APP'])
        out = restore_plan_projects(resume, plan, profile)
        assert len(out['projects']) == 1
        # LLM-polished version retained, source NOT re-added.
        assert out['projects'][0]['description'] == 'LLM polished'


class TestRestorePlanCerts:
    """PR 3a — restore_plan_certs, mirroring restore_plan_projects but for
    cert entries (single-line, no bullets)."""

    def test_a_restores_dropped_certs(self):
        from resumes.services.resume_normalizer import restore_plan_certs
        resume = {'certifications': [
            {'name': 'AI Professional Level', 'issuer': 'WE Telecom'},
        ]}
        profile = _profile(certifications=[
            {'name': 'AI Professional Level', 'issuer': 'WE Telecom'},
            {'name': 'Natural Language Processing in TensorFlow', 'issuer': 'DeepLearning.AI'},
            {'name': 'Applied Machine Learning in Python', 'issuer': 'Coursera'},
        ])
        plan = _plan(certifications=[
            'AI Professional Level',
            'Natural Language Processing in TensorFlow',
            'Applied Machine Learning in Python',
        ])
        out = restore_plan_certs(resume, plan, profile)
        names = [c['name'] for c in out['certifications']]
        assert 'Natural Language Processing in TensorFlow' in names
        assert 'Applied Machine Learning in Python' in names

    def test_b_no_restore_when_at_cap(self):
        from resumes.services.resume_normalizer import (
            restore_plan_certs, _CERT_CAP,
        )
        resume = {'certifications': [
            {'name': f'C{i}', 'issuer': 'X'} for i in range(_CERT_CAP)
        ]}
        profile = _profile(certifications=[
            {'name': f'C{i}', 'issuer': 'X'} for i in range(_CERT_CAP)
        ] + [{'name': 'ExtraCert', 'issuer': 'Y'}])
        plan = _plan(certifications=[f'C{i}' for i in range(_CERT_CAP)] + ['ExtraCert'])
        out = restore_plan_certs(resume, plan, profile)
        assert len(out['certifications']) == _CERT_CAP
        assert 'ExtraCert' not in [c['name'] for c in out['certifications']]

    def test_c_restoration_stops_at_cap(self):
        from resumes.services.resume_normalizer import (
            restore_plan_certs, _CERT_CAP,
        )
        resume = {'certifications': [
            {'name': f'C{i}'} for i in range(_CERT_CAP - 1)
        ]}
        profile = _profile(certifications=[
            {'name': f'C{i}'} for i in range(_CERT_CAP - 1)
        ] + [{'name': 'A'}, {'name': 'B'}, {'name': 'C'}])
        plan = _plan(certifications=[f'C{i}' for i in range(_CERT_CAP - 1)] + ['A', 'B', 'C'])
        out = restore_plan_certs(resume, plan, profile)
        assert len(out['certifications']) == _CERT_CAP
        assert out['certifications'][-1]['name'] == 'A'

    def test_d_plan_cert_missing_from_source_logs_warning(self, caplog):
        from resumes.services.resume_normalizer import restore_plan_certs
        import logging
        resume = {'certifications': []}
        profile = _profile(certifications=[{'name': 'Real'}])
        plan = _plan(certifications=['Real', 'Ghost'])
        with caplog.at_level(logging.WARNING, logger='resumes.services.resume_normalizer'):
            out = restore_plan_certs(resume, plan, profile)
        names = [c['name'] for c in out['certifications']]
        assert names == ['Real']
        warns = [r.message for r in caplog.records
                 if 'not in source profile' in r.message]
        assert len(warns) == 1

    def test_e_empty_plan_no_op(self):
        from resumes.services.resume_normalizer import restore_plan_certs
        resume = {'certifications': [{'name': 'X'}]}
        out = restore_plan_certs(resume, _plan(certifications=[]), _profile(certifications=[]))
        assert out == resume


class TestNormalizeResumeRestoresPlanItems:
    """PR 3a integration test — full normalize_resume with both projects
    and certs dropped by the simulated LLM; verify both are restored."""

    def test_integration_restores_both(self, caplog):
        from resumes.services.resume_normalizer import normalize_resume
        import logging

        resume = {
            'professional_summary': 'Backend engineer.',
            'skills': [{'name': 'Python'}],
            'projects': [{'name': 'SmartCV', 'description': 'd'}],  # 1 of 3 plan-ranked
            'certifications': [{'name': 'AI Pro'}],  # 1 of 3 plan-ranked
        }
        profile = _profile(
            projects=[
                {'name': 'SmartCV', 'description': 'src1'},
                {'name': 'Brain Tumor CNN', 'description': 'src2'},
                {'name': 'End-to-End Pipeline', 'description': 'src3'},
            ],
            certifications=[
                {'name': 'AI Pro'},
                {'name': 'NLP in TensorFlow', 'issuer': 'DeepLearning.AI'},
                {'name': 'Applied ML in Python', 'issuer': 'Coursera'},
            ],
        )
        plan = _plan(
            projects=['SmartCV', 'Brain Tumor CNN', 'End-to-End Pipeline'],
            certifications=['AI Pro', 'NLP in TensorFlow', 'Applied ML in Python'],
            skills_to_list=['Python'],
        )
        with caplog.at_level(logging.INFO, logger='resumes.services.resume_normalizer'):
            out = normalize_resume(resume, plan, job=None, profile_data=profile)

        proj_names = [p['name'] for p in out['projects']]
        assert 'Brain Tumor CNN' in proj_names
        assert 'End-to-End Pipeline' in proj_names

        cert_names = [c['name'] for c in out['certifications']]
        assert 'NLP in TensorFlow' in cert_names
        assert 'Applied ML in Python' in cert_names

        # Both log lines fire.
        msgs = ' | '.join(r.message for r in caplog.records)
        assert 'restored' in msgs and 'project' in msgs
        assert 'restored' in msgs and 'cert' in msgs


# ---------------------------------------------------------------------------
# PR 3c — Extended _BANNED_SUMMARY_OPENERS_RE coverage
# ---------------------------------------------------------------------------


class TestCleanSummaryPhrasingBannedOpeners:
    """PR 3c — phrases the Zeyad audit hit ("Highly skilled" opener) plus
    the broader recruiter-jargon set added at the same time."""

    @pytest.mark.parametrize('opener', [
        'Highly skilled',
        'Highly accomplished',
        'Highly experienced',
        'Highly qualified',
        'Self-starter',
        'Innovative',
        'Strategic',
        'Proven',
    ])
    def test_opener_stripped(self, opener):
        from resumes.services.resume_normalizer import clean_summary_phrasing
        resume = {'professional_summary': f'{opener} engineer who ships production code.'}
        out = clean_summary_phrasing(resume)
        cleaned = out['professional_summary']
        # The opener must be gone from the start.
        assert not cleaned.lower().startswith(opener.lower()), (
            f'expected {opener!r} stripped, got {cleaned!r}'
        )
        # Re-capitalized first letter.
        assert cleaned[:1].isupper()

    @pytest.mark.parametrize('text', [
        # The banned phrases appear mid-sentence — should NOT be stripped.
        'Backend engineer with highly skilled team experience in payments.',
        'Shipped 4 highly accomplished features in 2025.',
        'Led the strategic platform consolidation effort.',
        'Built innovative ranking models with proven offline lift.',
    ])
    def test_mid_sentence_phrase_preserved(self, text):
        from resumes.services.resume_normalizer import clean_summary_phrasing
        resume = {'professional_summary': text}
        out = clean_summary_phrasing(resume)
        # Text unchanged when phrase isn't the opener.
        assert out['professional_summary'] == text

    def test_back_compat_existing_openers_still_stripped(self):
        # Sanity check: the pre-PR-3c banned openers still work.
        from resumes.services.resume_normalizer import clean_summary_phrasing
        resume = {'professional_summary': 'Highly motivated engineer focused on delivery.'}
        out = clean_summary_phrasing(resume)
        assert not out['professional_summary'].lower().startswith('highly motivated')
