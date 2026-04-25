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
