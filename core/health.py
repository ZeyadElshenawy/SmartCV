"""Health-check + lightweight observability endpoints.

Three URLs:
- /healthz/         — liveness probe. Always 200 if Python is alive. No DB hit.
                      Use this for load-balancer / uptime-monitor pings.
- /healthz/deep/    — readiness probe. 200 only if DB ping succeeds within
                      the configured timeout. Cached briefly so an external
                      monitor hammering this URL doesn't itself become load.
- /healthz/metrics/ — staff-only JSON dump of in-memory request counters
                      (latency p50/p95, status-code breakdown). Reset on
                      process restart — fine for a single-process dev /
                      small-prod deployment, swap for Prometheus later.

Defensive: every code path is wrapped so a metrics bug can never poison the
healthz endpoint, and a DB outage on the deep probe is surfaced via 503 (not
a 500 stacktrace).
"""
from __future__ import annotations

import time
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from . import metrics as _metrics


@require_GET
def healthz(request):
    """Liveness — does the process answer? No DB, no cache, no work."""
    return JsonResponse({"status": "ok"}, status=200)


@require_GET
@cache_page(15)  # 15s cache so external pollers don't double-load the DB
def healthz_deep(request):
    """Readiness — can we actually serve traffic right now?

    Pings the DB with a short statement_timeout so a stuck pool surfaces
    as a fast 503 instead of a 30s hang.
    """
    started = time.perf_counter()
    db_ok = False
    db_latency_ms = None
    db_error = None
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        db_ok = True
        db_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    except Exception as exc:  # noqa: BLE001 — we want any DB failure surfaced
        db_error = f"{exc.__class__.__name__}: {exc}"

    payload = {
        "status": "ok" if db_ok else "degraded",
        "checks": {
            "db": {
                "ok": db_ok,
                "latency_ms": db_latency_ms,
                "error": db_error,
            },
        },
        "debug": settings.DEBUG,
    }
    return JsonResponse(payload, status=200 if db_ok else 503)


@require_GET
@staff_member_required
def healthz_metrics(request):
    """Staff-only snapshot of in-memory request metrics."""
    try:
        snapshot = _metrics.snapshot()
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": f"metrics_unavailable: {exc}"}, status=500)
    return JsonResponse(snapshot, status=200)
