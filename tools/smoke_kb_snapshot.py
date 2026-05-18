#!/usr/bin/env python
"""Smoke-test PR 4.2's KB snapshot patches without DB or LLM access.

Loads `_kb_snapshot.json`, instantiates the same patched query
factories the integration suite installs in replay mode, then runs
them against a synthetic JD embedding. Confirms:

  * snapshot loads and chunks have expected shape
  * `_query_universal` returns chunks in the universal categories
  * `_query_role_specific_diversified` returns chunks tagged with
    requested roles and exercises facet filters

Useful as a sanity check before relying on the patches during a
full replay-mode integration run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# We can use the conftest helpers directly — they don't depend on
# pytest at import time, only on stdlib + the production module
# `profiles.services.knowledge_retriever` (for UNIVERSAL_CATEGORIES
# and the diversifier helpers, which themselves are stdlib-only).
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')

import django  # noqa: E402

django.setup()

from tests.integration.conftest import (  # noqa: E402
    KB_SNAPSHOT_PATH,
    _SnapshotChunk,
    _make_patched_query_universal,
    _make_patched_query_role_specific_diversified,
)
from profiles.services.knowledge_retriever import UNIVERSAL_CATEGORIES  # noqa: E402


def load_snapshot() -> list[_SnapshotChunk]:
    with open(KB_SNAPSHOT_PATH, encoding='utf-8') as f:
        data = json.load(f)
    return [_SnapshotChunk(c) for c in data["chunks"]]


def main() -> int:
    snapshot = load_snapshot()
    print(f"Loaded {len(snapshot)} snapshot chunks")

    # Build a synthetic JD embedding from an existing chunk's embedding
    # — guarantees it's a valid 384-dim vector and yields meaningful
    # ranking (no NaN, no all-zeros).
    seed = next(c for c in snapshot if c.embedding)
    jd_vec = seed.embedding

    # ---- universal query ----
    patched_universal = _make_patched_query_universal(snapshot)
    uni_chunks = patched_universal(jd_vec, top_n=6)
    print(f"_query_universal returned {len(uni_chunks)} chunks")
    bad = [c for c in uni_chunks if c.type not in UNIVERSAL_CATEGORIES]
    assert not bad, f"universal query returned non-universal types: {[c.type for c in bad]}"
    print(f"  types: {sorted({c.type for c in uni_chunks})}")

    # ---- role-specific diversified query ----
    patched_role = _make_patched_query_role_specific_diversified(snapshot)
    # Use roles that should exist for ml/data work + a generic seniority
    role_chunks = patched_role(
        jd_vec,
        role_tag=['ml_engineer', 'data_scientist'],
        seniority='junior',
        region='mena',
        top_n=8,
    )
    print(f"_query_role_specific_diversified returned {len(role_chunks)} chunks")
    print(f"  types: {sorted({c.type for c in role_chunks})}")
    role_hits = [
        getattr(c, 'roles', []) for c in role_chunks
    ]
    print(f"  sample chunk_roles: {role_hits[:3]}")

    # Light invariant: at least one returned chunk advertises one of
    # the requested role tags (this is the assertion test_dual_role_retrieval
    # checks via _retrieval_metadata).
    union_roles = set()
    for rl in role_hits:
        union_roles.update(rl or [])
    matched = union_roles & {'ml_engineer', 'data_scientist'}
    print(f"  matched requested roles in returned chunks: {sorted(matched)}")
    assert matched, "role-specific query returned no chunks tagged with requested roles"

    print("\nSnapshot patches operate correctly — PR 4.2 infrastructure verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
