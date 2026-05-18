#!/usr/bin/env python
"""Check integration-test recordings for completeness and anomalies.

Run before committing recordings to catch partial captures, version
mismatches, or unusually short recordings that suggest a problem
during the record session.

Usage::

    python tools/check_recordings.py
    python tools/check_recordings.py --fail-on-warn   # for CI

Designed to be cheap and dependency-free — only stdlib.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RECORDINGS_DIR = (
    Path(__file__).parent.parent / "tests" / "integration" / "fixtures" / "recordings"
)
KB_SNAPSHOT_PATH = RECORDINGS_DIR / "_kb_snapshot.json"


def check_kb_snapshot() -> tuple[list[str], list[str]]:
    """Validate the KB snapshot fixture. Returns (warnings, errors)."""
    warnings: list[str] = []
    errors: list[str] = []
    if not KB_SNAPSHOT_PATH.exists():
        errors.append(
            f"{KB_SNAPSHOT_PATH.name} missing — replay tests that "
            f"exercise retrieve_chunks will skip or fail."
        )
        return warnings, errors

    try:
        with open(KB_SNAPSHOT_PATH, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{KB_SNAPSHOT_PATH.name}: failed to load ({exc})")
        return warnings, errors

    version = data.get("_snapshot_version", 0)
    declared_count = data.get("_chunk_count", 0)
    chunks = data.get("chunks", []) or []

    if version != 1:
        warnings.append(
            f"{KB_SNAPSHOT_PATH.name}: snapshot version is {version}; expected 1"
        )
    if declared_count != len(chunks):
        errors.append(
            f"{KB_SNAPSHOT_PATH.name}: chunk-count mismatch "
            f"(header={declared_count}, actual={len(chunks)})"
        )
    if len(chunks) < 50:
        warnings.append(
            f"{KB_SNAPSHOT_PATH.name}: only {len(chunks)} chunks "
            f"(expected ~67). May indicate an incomplete capture."
        )

    if chunks:
        required = ['kb_id', 'type', 'roles', 'embedding']
        sample = chunks[0]
        missing = [f for f in required if f not in sample]
        if missing:
            errors.append(
                f"{KB_SNAPSHOT_PATH.name}: chunks missing required fields "
                f"{missing} (first chunk inspected)"
            )
        null_embedding_kbs = [
            c.get('kb_id') for c in chunks if not c.get('embedding')
        ]
        if null_embedding_kbs:
            warnings.append(
                f"{KB_SNAPSHOT_PATH.name}: {len(null_embedding_kbs)} chunk(s) "
                f"have null/empty embedding (cosine sort will treat them as "
                f"max distance). Sample: {null_embedding_kbs[:3]}"
            )
    return warnings, errors


def check_recordings(fail_on_warn: bool = False) -> int:
    if not RECORDINGS_DIR.exists():
        print(f"No recordings directory at {RECORDINGS_DIR}")
        return 0

    files = sorted(p for p in RECORDINGS_DIR.glob("*.json") if not p.name.startswith("_"))
    if not files:
        print("No recording files found.")
        # Still check the KB snapshot, since it's its own concern.
        snap_warnings, snap_errors = check_kb_snapshot()
        for e in snap_errors:
            print(f"  ERROR: {e}")
        for w in snap_warnings:
            print(f"  WARN: {w}")
        return 1 if snap_errors else 0

    warnings: list[str] = []
    errors: list[str] = []
    call_counts: dict[str, int] = {}
    versions: dict[str, int] = {}

    # KB snapshot — separate concern from per-test recordings.
    snap_warnings, snap_errors = check_kb_snapshot()
    warnings.extend(snap_warnings)
    errors.extend(snap_errors)

    for path in files:
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name}: failed to load ({exc})")
            continue

        # v1: bare list of {call_index, result}
        if isinstance(data, list):
            call_counts[path.stem] = len(data)
            versions[path.stem] = 1
            warnings.append(
                f"{path.name}: v1 format (legacy). Consider re-recording "
                f"to upgrade to v2 with completeness metadata."
            )
            continue

        # v2: dict with metadata
        version = data.get("_recording_version", 1)
        complete = data.get("_recording_complete", True)
        count = data.get("_call_count", 0)
        error = data.get("_error")

        call_counts[path.stem] = count
        versions[path.stem] = version

        if not complete:
            errors.append(
                f"{path.name}: INCOMPLETE — captured {count} call(s); "
                f"recorded error: {error or 'unknown'}"
            )

        if version >= 2 and count == 0:
            errors.append(f"{path.name}: zero calls captured")

    # Tests in the same suite typically have similar call counts (the
    # pipeline shape is identical, only assertions differ). A recording
    # significantly shorter than its peers is a likely partial capture.
    if call_counts:
        max_count = max(call_counts.values())
        for name, count in call_counts.items():
            if count < max_count - 1:  # tolerate 1-call variance
                warnings.append(
                    f"{name}.json: only {count} calls captured; other "
                    f"recordings have up to {max_count}. May indicate "
                    f"a partial capture."
                )

    # Report
    total = len(files)
    print(f"Checked {total} recording(s).")
    print(f"  v1 (legacy): {sum(1 for v in versions.values() if v == 1)}")
    print(f"  v2+:         {sum(1 for v in versions.values() if v >= 2)}")
    if call_counts:
        print(f"  call counts: min={min(call_counts.values())} "
              f"max={max(call_counts.values())} "
              f"mean={sum(call_counts.values()) / len(call_counts):.1f}")

    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  WARN: {w}")

    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors:
            print(f"  ERROR: {e}")
        return 1

    if warnings and fail_on_warn:
        return 1

    if not warnings and not errors:
        print("All recordings look complete and consistent.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Exit non-zero on warnings (for CI gates).",
    )
    args = parser.parse_args()
    sys.exit(check_recordings(fail_on_warn=args.fail_on_warn))
