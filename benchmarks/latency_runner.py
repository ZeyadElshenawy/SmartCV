"""Endpoint latency benchmark (Phase B).

Hammers a small slice of representative endpoints via Django's in-process
test ``Client`` and reports per-route p50 / p95 / p99 latency. Reuses the
project's own ``RequestObservabilityMiddleware`` so the numbers we report
match the values the live ``/healthz/metrics`` endpoint would surface.

Why ``Client`` instead of HTTP requests? The middleware records latency
in-process, so test-client requests are captured the same as production
ones — and there's no port binding to manage.

What this benchmark covers (v1):
    GET /                       — anonymous landing
    GET /healthz/               — liveness (no DB)
    GET /healthz/deep/          — readiness (one SELECT 1; 15s response cache)
    GET /accounts/login/        — auth form render
    GET /accounts/register/     — registration form render

What it does NOT cover yet:
    Authenticated routes that need a seeded Job / Profile / GapAnalysis
    (dashboard, gap-analysis, generate, outreach campaign). Those require
    fixture curation (Phase C). Once fixtures land the runner will be
    extended; for now we report on the fixture-free slice only.

Run:
    python -m benchmarks.latency_runner
    python -m benchmarks.latency_runner --requests 50  # fewer iterations
"""
from __future__ import annotations

import argparse
import platform
import sys
import time
from typing import Iterable

from django.conf import settings
from django.test import Client

from benchmarks._io import percentile, summary, write_section
from core import metrics as _metrics

DEFAULT_REQUESTS = 100
COLD_PREFIX = 5  # treat first N samples per route as "cold" for the cold-vs-warm split


ENDPOINTS: list[tuple[str, str, int]] = [
    # (label, path, expected_status)
    ("home",            "/",                   200),
    ("healthz",         "/healthz/",           200),
    ("healthz_deep",    "/healthz/deep/",      200),
    ("login_form",      "/accounts/login/",    200),
    ("register_form",   "/accounts/register/", 200),
]


def _bench_one(client: Client, path: str, n: int) -> dict:
    """Issue ``n`` GETs against ``path`` and return per-request latency stats."""
    samples_ms: list[float] = []
    statuses: dict[int, int] = {}
    errors: list[str] = []

    for _ in range(n):
        started = time.perf_counter()
        try:
            # SERVER_NAME drives the Host header; default 'testserver' is
            # rejected by post-hardening ALLOWED_HOSTS in production-mode runs.
            response = client.get(path, follow=False, SERVER_NAME="localhost")
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
        except Exception as exc:  # noqa: BLE001 — surface but keep iterating
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            errors.append(f"{exc.__class__.__name__}: {exc}")
        samples_ms.append(round(elapsed_ms, 3))

    cold = samples_ms[:COLD_PREFIX]
    warm = samples_ms[COLD_PREFIX:] if len(samples_ms) > COLD_PREFIX else []
    return {
        "path": path,
        "requests": n,
        "statuses": statuses,
        "errors": errors[:5],  # cap so a misconfigured route doesn't bloat the JSON
        "error_count": len(errors),
        "latency_ms": {
            **summary(samples_ms),
            "p50": percentile(samples_ms, 50),
            "p95": percentile(samples_ms, 95),
            "p99": percentile(samples_ms, 99),
        },
        "cold_warm": {
            "cold_n": len(cold),
            "cold_mean": round(sum(cold) / len(cold), 3) if cold else None,
            "cold_max": max(cold) if cold else None,
            "warm_n": len(warm),
            "warm_mean": round(sum(warm) / len(warm), 3) if warm else None,
            "warm_p95": percentile(warm, 95) if warm else None,
        },
    }


def run(requests_per_route: int = DEFAULT_REQUESTS) -> dict:
    client = Client()
    # Snapshot before so we can attribute new metrics to this run.
    pre_snapshot = _metrics.snapshot()
    pre_total = pre_snapshot["total"]["requests"]

    results = []
    started = time.perf_counter()
    for label, path, _expected in ENDPOINTS:
        per_route = _bench_one(client, path, requests_per_route)
        per_route["label"] = label
        results.append(per_route)
    wall_seconds = round(time.perf_counter() - started, 2)

    post_snapshot = _metrics.snapshot()
    delta_requests = post_snapshot["total"]["requests"] - pre_total

    payload = {
        "benchmark": "latency_runner",
        "version": 1,
        "requests_per_route": requests_per_route,
        "n_routes": len(ENDPOINTS),
        "total_requests_issued": requests_per_route * len(ENDPOINTS),
        "wall_seconds": wall_seconds,
        "platform": {
            "python": sys.version.split()[0],
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "debug": getattr(settings, "DEBUG", False),
            "database_engine": settings.DATABASES["default"]["ENGINE"],
        },
        "results": results,
        "middleware_snapshot": {
            "delta_requests_recorded": delta_requests,
            "post_run": post_snapshot,
        },
        "disclosure": (
            "Latencies measured in-process via django.test.Client (no network "
            "hop). Numbers reflect view + middleware + DB access cost on the "
            "developer machine; production WAN latency is not included. "
            "First N=5 samples per route are reported separately as 'cold' "
            "to expose lazy-import / connection-warmup cost."
        ),
    }
    out_path = write_section("latency_runner", payload)
    payload["written_to"] = str(out_path)
    return payload


def _format_report(payload: dict) -> str:
    lines = [
        "-- Endpoint latency (Phase B) --",
        f"  Requests : {payload['total_requests_issued']} "
        f"({payload['requests_per_route']} per route x {payload['n_routes']} routes)",
        f"  Wall     : {payload['wall_seconds']}s",
        f"  Platform : {payload['platform']['system']} {payload['platform']['release']} "
        f"/ Python {payload['platform']['python']}",
        "  Per route (ms):",
    ]
    width = max(len(r["label"]) for r in payload["results"])
    header = f"    {'route'.ljust(width)}  {'p50':>8}  {'p95':>8}  {'p99':>8}  {'max':>8}  status"
    lines.append(header)
    for r in payload["results"]:
        lat = r["latency_ms"]
        statuses = ",".join(f"{k}:{v}" for k, v in sorted(r["statuses"].items()))
        lines.append(
            f"    {r['label'].ljust(width)}  "
            f"{str(lat['p50']):>8}  {str(lat['p95']):>8}  "
            f"{str(lat['p99']):>8}  {str(lat['max']):>8}  {statuses}"
        )
    lines.append(f"  Written to : {payload['written_to']}")
    return "\n".join(lines)


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartCV endpoint latency benchmark")
    parser.add_argument(
        "--requests", type=int, default=DEFAULT_REQUESTS,
        help=f"requests per route (default: {DEFAULT_REQUESTS})",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    args = _parse_args()
    result = run(requests_per_route=args.requests)
    print(_format_report(result))
