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


# --------------------------------------------------------------------------
# Fix #5 (audit §6.5, 2026-05-30) — project depth bonus.
#
# new_score = max(retrieval, disc*2) + min(depth_bonus, DEPTH_CAP=4)
# depth_bonus = (+1 url) + min(unique_system_stack_tokens, SYSTEM_STACK_CAP=4)
# Tie-break: new_score desc, disc desc, profile_index asc.
# Keep filter (disc>=1 OR retrieval>=3) is unchanged and remains the line of
# defence against depth-only-no-JD-signal projects shipping.
# --------------------------------------------------------------------------


class _ProfileWithProjects:
    """Stand-in profile that carries arbitrary projects under data_content."""

    def __init__(self, projects, skills=None, certifications=None):
        self.data_content = {
            'projects': list(projects or []),
            'skills': list(skills or []),
            'certifications': list(certifications or []),
        }


def _run_planner(projects, must_have, *, matched_must=None):
    """Build a planner output for a fixture profile with the given projects."""
    from resumes.services.inclusion_planner import build_inclusion_plan
    profile = _ProfileWithProjects(projects=projects)
    job = _FakeJob(must_have=must_have)
    gap = _FakeGap(matched_must=matched_must or must_have)
    return build_inclusion_plan(profile, job, gap, per_skill_ev={})


def _names_in_rank(plan):
    return [p.name for p in plan.projects]


class TestProjectDepthBonus:
    """Verify the new depth-bonus contribution to project ranking."""

    def test_smartcv_style_deep_project_lands_at_rank_2_behind_higher_disc(self):
        """Round-1 Banque Misr fixture: a deep project with only one
        JD-matching tech (Python — JD-rescued) lands at #2 behind a
        project with two JD-matching techs PLUS some depth. SmartCV
        was the round-3/4 reviewer's primary complaint."""
        projects = [
            # disc=1 (Python only — JD-rescued from base), system tokens
            # PostgreSQL/Supabase/pgvector/Groq → depth=+4. New=2+4=6.
            {'name': 'SmartCV', 'url': 'https://example.com/smartcv',
             'technologies': ['Python', 'Django', 'PostgreSQL', 'Supabase',
                              'pgvector', 'Groq']},
            # disc=2 (Pandas + scikit-learn match), system tokens
            # Flask + MLflow → depth=+3. New=4+3=7.
            {'name': 'healthcare-prediction-depi',
             'url': 'https://example.com/hp',
             'technologies': ['Python', 'Pandas', 'scikit-learn',
                              'Flask', 'MLflow']},
            # disc=2 (Pandas + scikit-learn), no system tokens, has URL.
            # depth=+1. New=4+1=5.
            {'name': 'HR Analytics (Datacamp case study)',
             'url': 'https://example.com/hr',
             'technologies': ['Python', 'Jupyter Notebook', 'scikit-learn',
                              'Pandas', 'Plotly', 'Streamlit',
                              'Power BI', 'DAX']},
        ]
        # JD must-haves mirror the real Banque Misr DS posting (Python +
        # Pandas + ML — scikit-learn is NOT in the JD, so a project
        # listing it gets no disc credit for it). Three projects have
        # disc=2 (Python rescued + Pandas) under this JD; only depth
        # disambiguates them.
        must = ['Python', 'Pandas', 'Machine Learning']
        plan = _run_planner(projects, must)
        ranked = _names_in_rank(plan)
        assert ranked[0] == 'healthcare-prediction-depi', \
            f'highest disc+depth should lead, got {ranked}'
        assert ranked[1] == 'SmartCV', \
            f'SmartCV should land #2 (reviewer ask), got {ranked}'
        assert ranked[2] == 'HR Analytics (Datacamp case study)', \
            f'shallow case study should fall to #3, got {ranked}'

    def test_shallow_case_study_gets_only_url_bonus(self):
        """A shallow-stack project (no SYSTEM_STACK tokens) gets +1 from
        URL only — NOT lifted above an equal-disc deep project. The
        deep project's +4 depth lifts it past the shallow one's +1
        depth when disc is comparable."""
        projects = [
            # disc=2 (Python rescued + Pandas), 0 system tokens, URL → depth=+1. New=4+1=5.
            {'name': 'shallow-case-study', 'url': 'https://example.com/sc',
             'technologies': ['Python', 'Pandas', 'Jupyter Notebook',
                              'Plotly', 'Streamlit', 'Power BI']},
            # disc=2 (Python rescued + Pandas), 3 system tokens + URL → depth=+4. New=4+4=8.
            {'name': 'deep-project', 'url': 'https://example.com/deep',
             'technologies': ['Python', 'Pandas', 'Django', 'PostgreSQL',
                              'pgvector', 'Groq']},
        ]
        plan = _run_planner(projects, ['Python', 'Pandas'])
        ranked = _names_in_rank(plan)
        assert ranked[0] == 'deep-project', \
            f'deep project should overtake shallow case study, got {ranked}'

    def test_depth_only_no_jd_signal_still_dropped_by_keep_filter(self):
        """Firebase-CRUD case: 4 system tokens but disc=0 and retrieval=0.
        The keep filter MUST still drop it. _MIN_PROJECTS may resurrect
        it as a floor pick, so we assert it isn't in the top _MIN_PROJECTS
        slots when JD-relevant alternatives exist."""
        projects = [
            # JD-aligned: disc=1 (Pandas), 0 system tokens, URL. New=2+1=3.
            {'name': 'jd-relevant', 'url': 'https://example.com/jr',
             'technologies': ['Python', 'Pandas']},
            # disc=2, 0 system tokens. New=4+1=5.
            {'name': 'jd-relevant-2', 'url': 'https://example.com/jr2',
             'technologies': ['Python', 'Pandas', 'scikit-learn']},
            # disc=1 (Python), 0 system tokens. New=2+1=3.
            {'name': 'jd-relevant-3', 'url': 'https://example.com/jr3',
             'technologies': ['Python']},
            # depth-only: disc=0, depth=+4, but FAILS keep filter.
            {'name': 'firebase-crud-app', 'url': 'https://example.com/fc',
             'technologies': ['Flutter', 'Firebase', 'Dart']},
        ]
        must = ['Pandas', 'scikit-learn', 'Python']
        plan = _run_planner(projects, must)
        ranked = _names_in_rank(plan)
        # Three JD-relevant projects fill the top slots; the depth-only
        # one must not appear above any of them.
        assert ranked[:3] == ['jd-relevant-2', 'jd-relevant', 'jd-relevant-3'], \
            f'keep filter must gate depth-only project out of top 3, got {ranked}'

    def test_firebase_triple_count_guard(self):
        """The firebase service is one signal, even if all three of
        {firebase, firebase_auth, firestore} appear. Counting all three
        would let one BaaS decision collect +3 and defeat the cap's
        anti-sprawl design — so the canon DELIBERATELY excludes
        'firebaseauth' and 'firestore'. A project listing all three
        gets +1 for the service, not +3."""
        from resumes.services.inclusion_planner import _project_depth_bonus
        project = {
            'url': 'https://example.com/grimoire',
            'technologies': ['Flutter', 'Firebase', 'Firebase Auth', 'Firestore'],
        }
        # JD doesn't include any of these — depth bonus must score them
        # as: +1 URL, +1 flutter, +1 firebase = +3 total (NOT +5).
        depth, fired = _project_depth_bonus(project, jd_skill_canon=set())
        assert depth == 3, f'expected +3 (URL+flutter+firebase), got +{depth} firing {fired}'
        fired_canon = {f.lower().replace(' ', '') for f in fired}
        assert 'firebase' in fired_canon
        assert 'firebaseauth' not in fired_canon, \
            f'firebaseauth must NOT fire (deliberately excluded from canon): {fired}'
        assert 'firestore' not in fired_canon, \
            f'firestore must NOT fire (deliberately excluded from canon): {fired}'

    def test_tie_break_higher_disc_wins(self):
        """Two projects tied on new_score — the one with higher disc
        (more JD-aligned by raw discriminating tech) wins. This is the
        Fix #5 tie-breaker the dry-run agreed to."""
        projects = [
            # disc=1 (Python), 4 system tokens → depth=+4 (cap). New=2+4=6.
            {'name': 'deep-low-disc', 'url': 'https://example.com/dld',
             'technologies': ['Python', 'Django', 'PostgreSQL',
                              'pgvector', 'Groq']},
            # disc=2 (Python + scikit-learn), 1 system token + URL → depth=+2.
            # New=4+2=6 — same total as deep-low-disc, but higher disc.
            {'name': 'higher-disc-shallow', 'url': 'https://example.com/hds',
             'technologies': ['Python', 'scikit-learn', 'Flask']},
        ]
        plan = _run_planner(projects, ['Python', 'scikit-learn'])
        ranked = _names_in_rank(plan)
        assert ranked[0] == 'higher-disc-shallow', \
            f'tie should resolve to higher-disc project, got {ranked}'
        assert ranked[1] == 'deep-low-disc'

    def test_depth_bonus_respects_both_caps(self):
        """Per-token SYSTEM_STACK_CAP=4 and outer DEPTH_CAP=4 mean a
        project with URL + 7 distinct system tokens still scores
        depth=+4, not +5 or +8."""
        from resumes.services.inclusion_planner import _project_depth_bonus
        project = {
            'url': 'https://example.com/sprawl',
            'technologies': [
                # 7 distinct system-stack tokens — well past the per-cap of 4.
                'Django', 'FastAPI', 'PostgreSQL', 'Redis', 'Docker',
                'Kubernetes', 'Terraform',
                # And some non-system tech to confirm filtering.
                'Python', 'Jupyter Notebook',
            ],
        }
        depth, fired = _project_depth_bonus(project, jd_skill_canon=set())
        # URL alone would give +1; 7 stack tokens cap at 4. Sum 5 → cap
        # at outer DEPTH_CAP=4.
        assert depth == 4, f'depth must be clamped to DEPTH_CAP=4, got {depth}'
        # And the base-canon Python/Jupyter never appear in the firing.
        for f in fired:
            assert f.lower() not in ('python', 'jupyter notebook'), \
                f'base-canon token leaked into depth: {fired}'

    def test_jd_token_not_double_counted_in_depth(self):
        """A SYSTEM_STACK token that is ALSO a JD skill is counted by
        disc*2 and must NOT also fire in the depth bonus — otherwise
        the same fact contributes twice."""
        from resumes.services.inclusion_planner import _project_depth_bonus
        # JD lists Django and PostgreSQL — both canonicalised in jd_skill_canon.
        jd_canon = {'django', 'postgresql'}
        project = {
            'url': 'https://example.com/x',
            # All four tokens are in SYSTEM_STACK_CANON. Two overlap the
            # JD canon → must NOT count for depth. Two do not → +2.
            'technologies': ['Django', 'PostgreSQL', 'pgvector', 'Groq'],
        }
        depth, fired = _project_depth_bonus(project, jd_skill_canon=jd_canon)
        # URL (+1) + pgvector + Groq (+2) = +3. Django/PostgreSQL excluded.
        assert depth == 3, f'JD-overlap tokens must not double-count: got +{depth} firing {fired}'
        for f in fired:
            assert f.lower() not in ('django', 'postgresql'), \
                f'JD-overlap token leaked into depth firing: {fired}'


class _FakeChunk:
    """Minimal stand-in for the candidate_evidence_retriever Chunk type
    that build_inclusion_plan reads. The planner touches .source_id,
    .chunk_id, .source_type, and .text — fixture carries all four."""

    __slots__ = ('source_id', 'chunk_id', 'source_type', 'text')

    def __init__(self, source_id: str, chunk_id: str = '',
                 source_type: str = 'project', text: str = ''):
        self.source_id = source_id
        self.chunk_id = chunk_id or f'{source_id}:bullet:fake'
        self.source_type = source_type
        self.text = text


class TestRealDataPair1Ranking:
    """Pin the REAL-DATA headline ranking for Zeyad-A × Data Scientist @
    Banque Misr.

    This fixture mirrors what improving_resume_output/real_planner_pair1_check.py
    observed when run against the live dev DB on 2026-05-30 — including
    the actual retrieval_score per project, NOT the dry-run's disc*2
    approximation. The dry-run predicted SmartCV at #2 behind healthcare;
    the real planner with real retrieval delivered the same ranking. This
    test locks that headline so a future change to scoring weights, canon
    set, or retrieval index drift can't silently regress it.

    Real-data observation (Pair-1 from the implementation report):
        retrieval_score      disc   depth   combined
        SmartCV                 0    1        4         6
        healthcare-prediction   2    2        3         7
        HR Datacamp dashboard   2    2        1         5
        apotheosis              2    2        1         5
        BookShop                0    0        1         1   (filtered out)
        hr-analytics-dashboard  2    0        1         3   (filtered out)
        END-TO-END              0    0        0         0   (filtered out)
        BRAIN TUMOR             0    0        0         0   (filtered out)

    Note the dry-run's disc*2 approximation would have given HR Datacamp
    and apotheosis cur=4 each; the real run shows them at max(2, 4)=4 —
    same — because retrieval (2) ≤ disc*2 (4) for those projects. The
    `max()` made the approximation harmless here. SmartCV (disc=1,
    retrieval=0) still gets max(0, 2)=2 from the base and +4 from depth,
    landing at 6 to take #2."""

    PROJECTS = [
        {'name': 'SmartCV', 'url': 'https://example.com/smartcv',
         'technologies': ['Python', 'Django 5.2', 'PostgreSQL', 'Supabase',
                          'pgvector', 'Groq',
                          'meta-llama/llama-4-scout-17b-16e-instruct']},
        {'name': 'HR ANALYTICS DASHBOARD (CASE STUDY - DATACAMP)',
         'url': 'https://example.com/hr',
         'technologies': ['Python', 'Jupyter Notebook', 'scikit-learn',
                          'Pandas', 'Plotly', 'Streamlit', 'Power BI', 'DAX']},
        {'name': 'healthcare-prediction-depi',
         'url': 'https://example.com/healthcare',
         'technologies': ['Python', 'Jupyter Notebook', 'Pandas',
                          'scikit-learn', 'Flask', 'MLflow']},
        {'name': 'BookShop', 'url': 'https://example.com/bookshop',
         'technologies': ['HTML', 'CSS', 'JavaScript', 'Swiper']},
        {'name': 'apotheosis-traffic-sign-detection',
         'url': 'https://example.com/apotheosis',
         'technologies': ['Python', 'OpenCV', 'NumPy', 'Jupyter Notebook']},
        {'name': 'hr-analytics-dashboard',
         'url': 'https://example.com/hr2',
         'technologies': ['Power BI', 'DAX']},
        {'name': 'END-TO-END DATA PIPELINE & MACHINE LEARNING PROJECT',
         'url': '', 'technologies': []},
        {'name': 'BRAIN TUMOR CLASSIFICATION APP',
         'url': '', 'technologies': []},
    ]

    # Real Banque Misr Data Scientist JD must-haves + nice-to-haves
    # (verified against Job.extracted_skills_tiers in the dev DB).
    JD_MUST = ['Statistical Modeling', 'Machine Learning', 'Python', 'R',
               'SQL', 'Pandas', 'NumPy', 'Communication', 'presentation']
    JD_NICE = ['Deep Learning', 'TensorFlow', 'PyTorch', 'MLOps']

    # Real retrieval_score values observed in the live planner run:
    # 4 projects retrieved 2 chunks each across the JD-skill queries,
    # 4 retrieved 0. The fixture reproduces this exactly so the test
    # exercises the real max(retrieval, disc*2) path.
    REAL_RETRIEVAL_BY_INDEX = {
        0: 0,  # SmartCV
        1: 2,  # HR Datacamp dashboard
        2: 2,  # healthcare-prediction-depi
        3: 0,  # BookShop
        4: 2,  # apotheosis
        5: 2,  # hr-analytics-dashboard (retrieval-heavy but disc=0)
        6: 0,  # END-TO-END
        7: 0,  # BRAIN TUMOR
    }

    def _build_per_skill_ev(self):
        """Build a per_skill_ev dict whose flattened source_id tally
        matches REAL_RETRIEVAL_BY_INDEX after _retrieval_counter sums
        across skills. The planner only cares about the count per
        source_id, so spreading the chunks across distinct skill keys
        is fine — what matters is that source_id='project:<i>' appears
        N times in total."""
        per_skill_ev: dict = {}
        for i, n in self.REAL_RETRIEVAL_BY_INDEX.items():
            for c in range(n):
                key = f'skill_{i}_{c}'
                per_skill_ev[key] = [_FakeChunk(source_id=f'project:{i}')]
        return per_skill_ev

    def test_real_pair1_headline_ranking(self):
        """Real Pair-1 ranking is locked: healthcare#1, SmartCV#2,
        HR-Datacamp#3, apotheosis#4. The two filtered candidates
        (BookShop, hr-analytics-dashboard, END-TO-END, BRAIN TUMOR)
        must not appear above any of the four headline projects."""
        from resumes.services.inclusion_planner import build_inclusion_plan
        profile = _ProfileWithProjects(projects=self.PROJECTS)
        job = _FakeJob(must_have=self.JD_MUST, nice_to_have=self.JD_NICE)
        gap = _FakeGap(matched_must=[], matched_nice=[])
        plan = build_inclusion_plan(profile, job, gap,
                                    per_skill_ev=self._build_per_skill_ev())
        ranked = [p.name for p in plan.projects]
        # Headline assertion — the four JD-aligned projects in their
        # observed real-data order.
        assert ranked[0] == 'healthcare-prediction-depi', \
            f'real Pair-1 #1 should be healthcare, got {ranked}'
        assert ranked[1] == 'SmartCV', \
            f'real Pair-1 #2 should be SmartCV, got {ranked}'
        assert ranked[2] == 'HR ANALYTICS DASHBOARD (CASE STUDY - DATACAMP)', \
            f'real Pair-1 #3 should be HR Datacamp dashboard, got {ranked}'
        assert ranked[3] == 'apotheosis-traffic-sign-detection', \
            f'real Pair-1 #4 should be apotheosis, got {ranked}'
        # Retrieval-heavy / disc=0 projects (hr-analytics-dashboard,
        # BookShop) must not slip in above the headline four —
        # the keep filter's job. Confirm the implementation-report's
        # "bounded but untested" concern stays bounded with real
        # retrieval_score values in play.
        for filtered in ('hr-analytics-dashboard', 'BookShop'):
            if filtered in ranked:
                pos = ranked.index(filtered)
                assert pos >= 4, (
                    f'{filtered!r} must NOT rank above the headline four; '
                    f'got rank {pos+1}: {ranked}'
                )
