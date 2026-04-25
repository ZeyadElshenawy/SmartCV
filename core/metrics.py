"""Process-local request metrics — counters + a bounded latency ring buffer.

Cheap (lock-protected dicts, no I/O), zero deps, resets on process restart.
Designed as a stand-in until a real metrics backend (Prometheus, OpenTelemetry)
is wired up. The shape of the snapshot is stable so a future scraper can
read /healthz/metrics/ without changing the route.

Anything that touches this module is wrapped in try/except by callers — a
metrics bug must never break a real request.
"""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Deque

# How many recent latency samples to keep per route. Bounded so memory can't
# grow without limit on a long-running process.
_LATENCY_WINDOW = 500

_lock = threading.Lock()

# {route_label: int}
_request_counts: dict[str, int] = defaultdict(int)
# {route_label: {status_code: int}}
_status_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
# {route_label: deque of latency_ms floats}
_latency: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=_LATENCY_WINDOW))
# {route_label: int} 4xx + 5xx counts (so the dashboard answer is one lookup)
_error_4xx: dict[str, int] = defaultdict(int)
_error_5xx: dict[str, int] = defaultdict(int)


def record(route: str, status: int, latency_ms: float) -> None:
    """Stash one request's outcome. O(1), safe to call from middleware."""
    try:
        with _lock:
            _request_counts[route] += 1
            _status_counts[route][status] += 1
            _latency[route].append(latency_ms)
            if 400 <= status < 500:
                _error_4xx[route] += 1
            elif status >= 500:
                _error_5xx[route] += 1
    except Exception:
        # Metrics must never break a request. Swallow.
        pass


def _percentile(values: list[float], p: float) -> float | None:
    """Linear-interpolation percentile (no numpy dep)."""
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


def snapshot() -> dict:
    """Return a JSON-serializable view of current counters."""
    with _lock:
        routes = sorted(_request_counts.keys())
        out_routes = {}
        total_req = 0
        total_4xx = 0
        total_5xx = 0
        for r in routes:
            samples = list(_latency[r])
            count = _request_counts[r]
            errs_4 = _error_4xx[r]
            errs_5 = _error_5xx[r]
            out_routes[r] = {
                "count": count,
                "by_status": dict(_status_counts[r]),
                "errors_4xx": errs_4,
                "errors_5xx": errs_5,
                "error_rate": round((errs_4 + errs_5) / count, 4) if count else 0,
                "latency_ms": {
                    "p50": _percentile(samples, 50),
                    "p95": _percentile(samples, 95),
                    "p99": _percentile(samples, 99),
                    "samples": len(samples),
                },
            }
            total_req += count
            total_4xx += errs_4
            total_5xx += errs_5
        return {
            "total": {
                "requests": total_req,
                "errors_4xx": total_4xx,
                "errors_5xx": total_5xx,
                "error_rate": round((total_4xx + total_5xx) / total_req, 4) if total_req else 0,
            },
            "routes": out_routes,
        }
