"""Shared helpers for benchmark scripts: Django bootstrap, results I/O, stats."""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime
from pathlib import Path

import django

# Make sure Django is set up exactly once when any benchmark module is imported
# directly (e.g. `python -m benchmarks.ats_eval`).
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
try:
    django.setup()
except Exception:
    # Already set up by an outer process (e.g. run_all imported us first).
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / 'benchmarks' / 'results'
FIXTURES_DIR = REPO_ROOT / 'benchmarks' / 'fixtures'


def results_dir() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


def write_section(name: str, payload: dict) -> Path:
    """Write a per-benchmark JSON snapshot to benchmarks/results/<date>/<name>.json."""
    date = datetime.utcnow().strftime('%Y-%m-%d')
    out_dir = results_dir() / date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    return out_path


# ─── Crash-safe / resumable checkpointing ────────────────────────────────────
# Each eval appends one completed item as a single JSON line to a per-eval
# ``<name>.partial.jsonl`` IMMEDIATELY after it's produced (fsync'd), so a crash
# never loses completed work and a re-run resumes from where it stopped.
#
# The partial path is deliberately DATE-INDEPENDENT (unlike write_section's
# dated summary): a multi-day sweep that pauses on a daily-cap exhaustion and
# resumes the *next* day must find the prior progress, so it can't be stamped
# with the run date. The dated summary is still written on full completion.

def partial_path(name: str) -> Path:
    return results_dir() / f"{name}.partial.jsonl"


def append_partial(name: str, row: dict) -> Path:
    """Append one completed item as a JSON line and fsync, so disk reflects
    per-item progress (crash-safe)."""
    path = partial_path(name)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, default=str) + '\n')
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    return path


def read_partial(name: str) -> list:
    """All rows from the partial, tolerating a torn final line from a crash
    mid-write (that item simply reprocesses on resume)."""
    path = partial_path(name)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def row_has_error(row: dict) -> bool:
    """True if a partial row represents a FAILED item — top-level ``error`` /
    ``stage`` marker, or any errored sub-run. Such items must be RETRIED on
    resume, never skipped."""
    if not isinstance(row, dict):
        return True
    if row.get('error') or row.get('stage'):
        return True
    for run in (row.get('runs') or []):
        if isinstance(run, dict) and run.get('error'):
            return True
    return False


def completed_keys(name: str, key_of) -> set:
    """Keys of items with a genuinely SUCCESSFUL partial row. Errored rows are
    EXCLUDED so they retry on resume (the load-bearing correctness rule)."""
    done = set()
    for row in read_partial(name):
        if not row_has_error(row):
            done.add(key_of(row))
    return done


def assemble_rows(name: str, key_of) -> list:
    """One row per item from the partial, success-preferred: a retried item has
    both its old error row and its new success row — keep the success."""
    by_key = {}
    for row in read_partial(name):
        k = key_of(row)
        if k not in by_key or not row_has_error(row):
            by_key[k] = row
    return list(by_key.values())


def clear_partial(name: str) -> None:
    """Remove the checkpoint (called only AFTER the final summary write succeeds,
    so a subsequent fresh run doesn't resume a finished sweep)."""
    p = partial_path(name)
    if p.exists():
        p.unlink()


# ─── Stats helpers ───────────────────────────────────────────────────────────

def percentile(values, p: float):
    """Linear-interpolation percentile, mirroring core/metrics._percentile so
    the benchmark numbers compare cleanly with the live observability snapshot.
    """
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return round(s[0], 2)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return round(s[f], 2)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def summary(values) -> dict:
    """min/mean/median/p95/max/std for a list of numbers."""
    if not values:
        return {'n': 0, 'min': None, 'mean': None, 'median': None,
                'p95': None, 'max': None, 'std': None}
    return {
        'n': len(values),
        'min': round(min(values), 4),
        'mean': round(statistics.fmean(values), 4),
        'median': round(statistics.median(values), 4),
        'p95': percentile(values, 95),
        'max': round(max(values), 4),
        'std': round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
    }


def cohens_d(a, b) -> float | None:
    """Effect size between two groups. None if either group is empty / σ=0."""
    if not a or not b:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    if len(a) < 2 or len(b) < 2:
        return None
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    pooled = math.sqrt(((len(a) - 1) * sa ** 2 + (len(b) - 1) * sb ** 2) / (len(a) + len(b) - 2))
    if pooled == 0:
        return None
    return round((ma - mb) / pooled, 3)


def precision_recall_f1(predicted, labeled) -> dict:
    """Compute precision/recall/F1 over two iterables (treated as sets)."""
    p = set(predicted)
    g = set(labeled)
    tp = len(p & g)
    fp = len(p - g)
    fn = len(g - p)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'f1': round(f1, 4),
        'tp': tp,
        'fp': fp,
        'fn': fn,
    }
