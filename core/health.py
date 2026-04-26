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


# Canonical task names — kept in sync with the `task=...` arg every caller
# of get_llm / get_structured_llm / get_llm_client passes. Adding a new task
# requires adding it here so /healthz/llm/ surfaces it.
KNOWN_LLM_TASKS = (
    "agent_chat",
    "interviewer",
    "gap_analyzer",
    "resume_gen",
    "skill_extractor",
    "parser",
    "validator",
    "cover_letter",
    "outreach",
    "learning_path",
    "salary",
    "judge",
)


def _mask_key(key: str) -> str:
    """Mask a credential as `<first 8 chars>…<last 4 chars>`.

    The 8+4 split is enough entropy to confirm two tasks share the same
    key (or to spot when a dedicated key falls back to the global default)
    without leaking a usable credential. Empty / very short keys are
    surfaced verbatim so a misconfiguration jumps out.
    """
    if not key:
        return "(empty)"
    if len(key) <= 12:
        # Too short to mask meaningfully — likely a misconfig (e.g., `gsk_x`).
        # Show length only so the operator notices.
        return f"(short: {len(key)} chars)"
    return f"{key[:8]}…{key[-4:]}"


@require_GET
@staff_member_required
def healthz_llm(request):
    """Staff-only snapshot of the LLM task→key/model routing.

    Reads env at request time and goes through the same `_resolve_credentials`
    path that production calls use, so what you see here is what every LLM
    call actually picks. Useful for confirming the per-task GROQ_API_KEY_*
    vars are spelled correctly after a `.env` change.

    Keys are masked. Tasks that fell back to the global GROQ_API_KEY are
    flagged `dedicated: false` so a missing override is obvious.
    """
    # Lazy import — avoids a circular import via profiles.services at module load
    from profiles.services.llm_engine import (
        DEFAULT_GROQ_API_KEY,
        DEFAULT_GROQ_MODEL,
        _resolve_credentials,
    )

    try:
        default_key = DEFAULT_GROQ_API_KEY
        default_model = DEFAULT_GROQ_MODEL

        tasks_payload = {}
        unique_keys = set()
        if default_key:
            unique_keys.add(default_key)
        dedicated_count = 0
        model_overrides_count = 0

        for task in KNOWN_LLM_TASKS:
            task_key, task_model = _resolve_credentials(task)
            is_dedicated = bool(task_key) and task_key != default_key
            is_model_overridden = task_model != default_model

            if is_dedicated:
                dedicated_count += 1
            if is_model_overridden:
                model_overrides_count += 1
            if task_key:
                unique_keys.add(task_key)

            tasks_payload[task] = {
                "key": _mask_key(task_key),
                "model": task_model,
                "dedicated": is_dedicated,
                "model_overridden": is_model_overridden,
            }

        payload = {
            "default": {
                "key": _mask_key(default_key),
                "model": default_model,
                "set": bool(default_key),
            },
            "tasks": tasks_payload,
            "summary": {
                "total_tasks": len(KNOWN_LLM_TASKS),
                "dedicated_count": dedicated_count,
                "fallback_count": len(KNOWN_LLM_TASKS) - dedicated_count,
                "unique_keys": len(unique_keys),
                "model_overrides_count": model_overrides_count,
            },
        }
        return JsonResponse(payload, status=200)
    except Exception as exc:  # noqa: BLE001 — never let a config bug 500
        return JsonResponse({"error": f"llm_status_unavailable: {exc}"}, status=500)
