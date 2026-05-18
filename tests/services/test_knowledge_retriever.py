"""Unit tests for profiles/services/knowledge_retriever.py.

PR 2a — Fix 1 (_normalize_role AI-family aliases) + Fix 3 (retrieve_chunks
honours both JD and profile roles).
"""
from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# Fix 1 — _normalize_role aliases
# --------------------------------------------------------------------------

class TestNormalizeRoleExistingBehaviour:
    """Pre-PR mappings must not regress when the new ml-family aliases
    are added."""

    @pytest.mark.parametrize('label,expected', [
        ('Software Engineer', 'software_engineer'),
        ('Frontend Developer', 'frontend'),
        ('Backend Engineer', 'backend'),
        ('Full Stack Developer', 'fullstack'),
        ('Mobile Engineer', 'mobile'),
        ('DevOps Engineer', 'devops'),
        ('Site Reliability Engineer', 'devops'),
        ('Platform Engineer', 'devops'),
        ('Data Engineer', 'data_engineer'),
        ('Data Scientist', 'data_scientist'),
        ('ML Engineer', 'ml_engineer'),
        ('Machine Learning Engineer', 'ml_engineer'),
        ('QA Engineer', 'qa'),
        ('Designer', 'designer'),
        ('Product Manager', 'product_manager'),
        ('Random Unknown Title', 'software_engineer'),  # default fallback
    ])
    def test_preserves_existing_mappings(self, label, expected):
        from profiles.services.knowledge_retriever import _normalize_role
        assert _normalize_role(label) == expected


class TestNormalizeRoleAiFamilyAliases:
    """PR 2a Fix 1 — JD titles in the AI / GenAI / LLM / MLOps space
    now route to `ml_engineer` instead of falling through to
    `software_engineer`."""

    @pytest.mark.parametrize('label', [
        'AI Developer',
        'AI Engineer',
        'Junior AI Developer',
        'GenAI Engineer',
        'Generative AI Engineer',
        'LLM Engineer',
        'LLM Developer',
        'Prompt Engineer',
        'MLOps Engineer',
        'ML Ops Engineer',
        'AI/ML Engineer',
        'Computer Vision Engineer',
        'NLP Engineer',
        'Deep Learning Engineer',
    ])
    def test_routes_to_ml_engineer(self, label):
        from profiles.services.knowledge_retriever import _normalize_role
        assert _normalize_role(label) == 'ml_engineer', (
            f'{label!r} should route to ml_engineer'
        )


class TestNormalizeRoleSpecificityGuard:
    """The new aliases run AFTER the data_scientist / data_engineer
    checks, so titles that should still resolve to those keep doing so
    even when they contain "ML" tokens."""

    def test_data_scientist_wins_over_ml(self):
        from profiles.services.knowledge_retriever import _normalize_role
        assert _normalize_role('Data Scientist with ML focus') == 'data_scientist'

    def test_data_engineer_wins_over_ml(self):
        from profiles.services.knowledge_retriever import _normalize_role
        assert _normalize_role('Senior Data Engineer with ML pipelines') == 'data_engineer'


# --------------------------------------------------------------------------
# Fix 3 — retrieve_chunks honours both JD role + profile role
# --------------------------------------------------------------------------

from unittest.mock import MagicMock, patch
from profiles.services.role_classifier import RoleClassification


class _StubChunk:
    """Minimal stand-in for KnowledgeChunk that supports the attributes
    _query_role_specific reads (roles, seniority, region, kb_id). Avoids
    DB setup."""
    def __init__(self, kb_id, roles, seniority=('all',), region='global'):
        self.kb_id = kb_id
        self.roles = list(roles)
        self.seniority = list(seniority)
        self.region = region


def _stub_classification(primary_role, *, profile_role=None,
                          seniority='junior', region='global'):
    """Build a classification with the dual-role extension PR2a adds."""
    cls = RoleClassification(
        primary_role=primary_role,
        seniority=seniority,
        tech_stack_signals=[],
        region=region,
    )
    if profile_role is not None:
        cls.profile_role = profile_role
    return cls


def _patch_role_specific(monkeypatch, chunks):
    """Bypass the DB. Substitute _query_role_specific so it filters the
    stubbed `chunks` in Python by the exact same logic the real
    function would have applied — letting us assert union/dedupe
    behaviour without standing up a test database."""
    import profiles.services.knowledge_retriever as kr

    def _fake(jd_embedding, role_tag, seniority, region, top_n, over_fetch=4):
        # Mirror the real filter loop.
        filtered = []
        for c in chunks:
            if not kr._facet_matches(c.roles, role_tag):
                continue
            if not kr._facet_matches(c.seniority, seniority):
                continue
            if c.region not in ('global', region):
                continue
            filtered.append(c)
            if len(filtered) >= top_n:
                break
        return filtered

    monkeypatch.setattr(kr, '_query_role_specific', _fake)
    # PR 3b — retrieve_chunks now calls the diversified variant. Patch
    # both names so the test fake applies regardless of which
    # internal helper retrieve_chunks routes through.
    monkeypatch.setattr(kr, '_query_role_specific_diversified', _fake)


def _stub_universal_empty(monkeypatch):
    """Skip the universal-pool query — we only care about the role
    pool in these tests. (`retrieve_chunks` still gets the universal
    share count from the args.)"""
    import profiles.services.knowledge_retriever as kr
    monkeypatch.setattr(kr, '_query_universal', lambda emb, n: [])


def _stub_embed(monkeypatch):
    """No real embedding model needed for these tests."""
    import profiles.services.knowledge_retriever as kr
    monkeypatch.setattr(kr, 'embed_text', lambda text: [0.0] * 384)


class TestRetrieveChunksDualRole:
    """Fix 3 — retrieve_chunks unions JD-derived and profile-derived
    roles via _facet_matches' new list-acceptance, dedupes by kb_id,
    and honours top_n after merge."""

    def setup_method(self, _method):
        # Stubbed KB pool: one chunk per role of interest.
        self.chunks = [
            _StubChunk('chunk-ml-1', roles=['ml_engineer']),
            _StubChunk('chunk-ds-1', roles=['data_scientist']),
            _StubChunk('chunk-be-1', roles=['backend']),
            _StubChunk('chunk-both', roles=['ml_engineer', 'data_scientist']),
        ]

    def test_union_includes_chunks_from_both_role_pools(self, monkeypatch):
        from profiles.services.knowledge_retriever import retrieve_chunks
        _stub_embed(monkeypatch)
        _stub_universal_empty(monkeypatch)
        _patch_role_specific(monkeypatch, self.chunks)

        # JD=ml_engineer, profile=data_scientist — both pools surface.
        cls = _stub_classification(
            'AI/ML Engineer', profile_role='Data Scientist',
        )
        out = retrieve_chunks('jd body', cls, k=10, universal_share=0)
        kb_ids = {c.kb_id for c in out}
        assert 'chunk-ml-1' in kb_ids, 'ml_engineer chunk missing'
        assert 'chunk-ds-1' in kb_ids, 'data_scientist chunk missing'
        assert 'chunk-be-1' not in kb_ids, 'backend chunk should not surface'

    def test_dedupes_chunk_tagged_to_both_roles(self, monkeypatch):
        from profiles.services.knowledge_retriever import retrieve_chunks
        _stub_embed(monkeypatch)
        _stub_universal_empty(monkeypatch)
        _patch_role_specific(monkeypatch, self.chunks)

        cls = _stub_classification(
            'AI/ML Engineer', profile_role='Data Scientist',
        )
        out = retrieve_chunks('jd body', cls, k=10, universal_share=0)
        kb_ids = [c.kb_id for c in out]
        assert kb_ids.count('chunk-both') == 1, (
            f'multi-role chunk appeared {kb_ids.count("chunk-both")} times'
        )

    def test_backward_compat_when_profile_role_absent(self, monkeypatch):
        # Classification with no profile_role extra — behave as before
        # (only JD role drives retrieval).
        from profiles.services.knowledge_retriever import retrieve_chunks
        _stub_embed(monkeypatch)
        _stub_universal_empty(monkeypatch)
        _patch_role_specific(monkeypatch, self.chunks)

        cls = _stub_classification('AI/ML Engineer')  # no profile_role
        out = retrieve_chunks('jd body', cls, k=10, universal_share=0)
        kb_ids = {c.kb_id for c in out}
        assert 'chunk-ml-1' in kb_ids
        assert 'chunk-both' in kb_ids
        # data_scientist-only chunk should NOT surface (profile role
        # didn't widen the pool).
        assert 'chunk-ds-1' not in kb_ids

    def test_top_n_honoured_after_merge(self, monkeypatch):
        from profiles.services.knowledge_retriever import retrieve_chunks
        _stub_embed(monkeypatch)
        _stub_universal_empty(monkeypatch)
        _patch_role_specific(monkeypatch, self.chunks)

        cls = _stub_classification(
            'AI/ML Engineer', profile_role='Data Scientist',
        )
        out = retrieve_chunks('jd body', cls, k=2, universal_share=0)
        assert len(out) <= 2, f'expected at most 2 chunks, got {len(out)}'

    def test_log_emits_combined_role_label_when_roles_differ(
        self, monkeypatch, caplog,
    ):
        import logging as _logging
        from profiles.services.knowledge_retriever import retrieve_chunks
        _stub_embed(monkeypatch)
        _stub_universal_empty(monkeypatch)
        _patch_role_specific(monkeypatch, self.chunks)

        cls = _stub_classification(
            'AI/ML Engineer', profile_role='Data Scientist',
        )
        with caplog.at_level(_logging.INFO,
                              logger='profiles.services.knowledge_retriever'):
            retrieve_chunks('jd body', cls, k=4, universal_share=0)
        msgs = [r.message for r in caplog.records
                if 'knowledge_retriever: jd_chars=' in r.message]
        assert msgs, 'expected the main retrieve_chunks INFO line'
        # ml_engineer wins primary slot; profile role is data_scientist.
        # Log label is "jd+profile" with the canonical tags.
        assert 'role=ml_engineer+data_scientist' in msgs[-1], msgs[-1]

    def test_log_collapses_to_single_role_when_equal(
        self, monkeypatch, caplog,
    ):
        import logging as _logging
        from profiles.services.knowledge_retriever import retrieve_chunks
        _stub_embed(monkeypatch)
        _stub_universal_empty(monkeypatch)
        _patch_role_specific(monkeypatch, self.chunks)

        cls = _stub_classification(
            'AI/ML Engineer', profile_role='AI/ML Engineer',
        )
        with caplog.at_level(_logging.INFO,
                              logger='profiles.services.knowledge_retriever'):
            retrieve_chunks('jd body', cls, k=4, universal_share=0)
        msgs = [r.message for r in caplog.records
                if 'knowledge_retriever: jd_chars=' in r.message]
        assert msgs
        # When both normalise to the same tag, only one shows up.
        assert 'role=ml_engineer ' in msgs[-1] or 'role=ml_engineer\n' in msgs[-1] \
            or msgs[-1].endswith('role=ml_engineer'), msgs[-1]
        assert '+' not in msgs[-1].split('role=')[1].split()[0], (
            f"single role expected, got: {msgs[-1]!r}"
        )


class TestFacetMatchesAcceptsList:
    """The new _facet_matches signature accepts an iterable of wanted
    values without breaking single-string callers."""

    def test_single_string_call_still_works(self):
        from profiles.services.knowledge_retriever import _facet_matches
        assert _facet_matches(['ml_engineer'], 'ml_engineer') is True
        assert _facet_matches(['data_scientist'], 'ml_engineer') is False

    def test_list_call_matches_any(self):
        from profiles.services.knowledge_retriever import _facet_matches
        assert _facet_matches(['ml_engineer'], ['data_scientist', 'ml_engineer']) is True
        assert _facet_matches(['backend'], ['data_scientist', 'ml_engineer']) is False

    def test_all_wildcard_still_wins_for_list(self):
        from profiles.services.knowledge_retriever import _facet_matches
        assert _facet_matches(['all'], ['data_scientist', 'ml_engineer']) is True

    def test_empty_stored_returns_false(self):
        from profiles.services.knowledge_retriever import _facet_matches
        assert _facet_matches([], ['ml_engineer']) is False
        assert _facet_matches([], 'ml_engineer') is False


# ---------------------------------------------------------------------------
# PR 3b — Category-diversified retrieval (_diversify_per_category)
# ---------------------------------------------------------------------------


class _StubCat:
    """Minimal stub for the pure-function diversification helper. Only
    needs kb_id + type to be testable."""
    def __init__(self, kb_id, type_):
        self.kb_id = kb_id
        self.type = type_


class TestDiversifyPerCategory:
    """PR 3b — _diversify_per_category is the pure two-pass algorithm
    extracted from _query_role_specific_diversified for unit testing
    without a DB. Validates: (1) one-per-category breadth first,
    (2) per-category-rank fill for remaining slots, (3) graceful
    fallback when the role bucket is thin in some categories."""

    def _make(self, distribution: dict[str, int]):
        """Build per_category dict where each category has the given
        count of stub chunks. Chunks are ordered by per-category rank."""
        per_cat = {}
        for cat, n in distribution.items():
            per_cat[cat] = [_StubCat(f'{cat}_{i}', cat) for i in range(n)]
        return per_cat

    def test_a_five_categories_top_3_returns_three_distinct(self):
        from profiles.services.knowledge_retriever import _diversify_per_category
        per_cat = self._make({
            'action_verb': 2,
            'bullet_pattern': 2,
            'industry_norm': 2,
            'seniority_norm': 2,
            'mena_context': 2,
        })
        result = _diversify_per_category(per_cat, top_n=3)
        assert len(result) == 3
        cats = [c.type for c in result]
        # All 3 from different categories (category-diversity guarantee).
        assert len(set(cats)) == 3

    def test_b_two_categories_top_3_returns_one_per_then_fill(self):
        from profiles.services.knowledge_retriever import _diversify_per_category
        # 5 industry_norm chunks, 2 bullet_pattern chunks. top_n=3.
        per_cat = self._make({
            'industry_norm': 5,
            'bullet_pattern': 2,
        })
        result = _diversify_per_category(per_cat, top_n=3)
        assert len(result) == 3
        cats = [c.type for c in result]
        # Pass 1: 1 industry_norm + 1 bullet_pattern (2 distinct categories).
        # Pass 2: 1 more from either, by per-category rank.
        assert 'industry_norm' in cats
        assert 'bullet_pattern' in cats

    def test_c_only_one_category_returns_top_n_from_it(self):
        from profiles.services.knowledge_retriever import _diversify_per_category
        per_cat = self._make({'industry_norm': 5})
        result = _diversify_per_category(per_cat, top_n=3)
        assert len(result) == 3
        assert all(c.type == 'industry_norm' for c in result)
        # In rank order (first-pass takes index-0, then pass-2 fills with index-1, 2).
        assert [c.kb_id for c in result] == [
            'industry_norm_0', 'industry_norm_1', 'industry_norm_2',
        ]

    def test_d_dual_role_pool_still_diversifies(self):
        """Simulates the dual-role pool (PR 2a Fix 3) — chunks come from
        ml_engineer + data_scientist buckets; per_category dict is the
        merged result. Category diversity logic doesn't care about
        which role contributed the chunk — only about category type."""
        from profiles.services.knowledge_retriever import _diversify_per_category
        per_cat = {
            'bullet_pattern': [_StubCat('rag_pattern_ml', 'bullet_pattern')],
            'industry_norm': [
                _StubCat('ml_eng_ind', 'industry_norm'),
                _StubCat('data_sci_ind', 'industry_norm'),
            ],
            'action_verb': [_StubCat('ml_verbs', 'action_verb')],
        }
        result = _diversify_per_category(per_cat, top_n=3)
        assert len(result) == 3
        cats = {c.type for c in result}
        # All three categories represented.
        assert cats == {'bullet_pattern', 'industry_norm', 'action_verb'}

    def test_e_thin_bucket_falls_back_gracefully(self):
        from profiles.services.knowledge_retriever import _diversify_per_category
        per_cat = self._make({'bullet_pattern': 1})
        result = _diversify_per_category(per_cat, top_n=3)
        assert len(result) == 1  # only 1 chunk exists; no padding/inventing
        assert result[0].type == 'bullet_pattern'

    def test_empty_input_returns_empty(self):
        from profiles.services.knowledge_retriever import _diversify_per_category
        assert _diversify_per_category({}, top_n=3) == []

    def test_top_n_zero_returns_empty(self):
        from profiles.services.knowledge_retriever import _diversify_per_category
        per_cat = self._make({'industry_norm': 5, 'bullet_pattern': 3})
        assert _diversify_per_category(per_cat, top_n=0) == []

    def test_dedupe_via_kb_id(self):
        """Same chunk appearing in two categories (shouldn't happen in
        practice, but defensive): dedupe by kb_id keeps the first."""
        from profiles.services.knowledge_retriever import _diversify_per_category
        shared = _StubCat('shared_id', 'industry_norm')
        per_cat = {
            'industry_norm': [shared],
            'bullet_pattern': [shared],
        }
        result = _diversify_per_category(per_cat, top_n=3)
        ids = [c.kb_id for c in result]
        assert ids.count('shared_id') == 1
