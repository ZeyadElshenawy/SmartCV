#!/usr/bin/env python
"""Capture the live KnowledgeChunk table to _kb_snapshot.json.

Standalone snapshotter used by PR 4.2 to freeze KB state for the
integration test suite's replay mode. Run once against a reachable
dev DB; the resulting JSON is committed and loaded by
``tests/integration/conftest.py`` whenever ``INTEGRATION_RECORD`` is
not set.

Usage::

    python tools/capture_kb_snapshot.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap Django before importing any app modules.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')

import django  # noqa: E402

django.setup()

from profiles.models import KnowledgeChunk  # noqa: E402

SNAPSHOT_PATH = ROOT / "tests" / "integration" / "fixtures" / "recordings" / "_kb_snapshot.json"


def capture() -> int:
    rows = KnowledgeChunk.objects.all()
    payload_chunks: list[dict] = []
    for row in rows:
        payload_chunks.append({
            "kb_id": row.kb_id,
            "type": row.type,
            "title": row.title or "",
            "concrete_rule": row.concrete_rule or "",
            "body": row.body or "",
            "roles": list(row.roles or []),
            "seniority": list(row.seniority or []),
            "industries": list(row.industries or []),
            "region": row.region or "global",
            "weight": row.weight or "medium",
            "embedding": [float(x) for x in row.embedding] if row.embedding is not None else None,
        })

    payload = {
        "_snapshot_version": 1,
        "_snapshot_created": datetime.now(timezone.utc).isoformat(),
        "_chunk_count": len(payload_chunks),
        "chunks": payload_chunks,
    }

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, default=str)
    return len(payload_chunks)


if __name__ == "__main__":
    n = capture()
    print(f"Captured {n} chunks -> {SNAPSHOT_PATH}")
