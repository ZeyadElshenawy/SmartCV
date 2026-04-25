"""Lightweight observability middleware.

Adds:
- `X-Response-Time-ms` header on every response.
- `request.duration_ms` attribute for downstream code.
- Structured logging at WARN for >1500 ms requests, WARN for 5xx, INFO for
  4xx (>=401, ignoring expected 401/302/304 noise from auth/cache flows).
- In-memory metrics counter via core.metrics.record().

Defensive: the entire middleware body is wrapped so a metrics bug or logger
misconfig cannot break the response. Health-check URLs are skipped to avoid
self-referential noise.
"""
from __future__ import annotations

import logging
import time

from . import metrics as _metrics

logger = logging.getLogger("smartcv.requests")

# Cutoffs — tunable; matches the video's "slow" guidance loosely.
_SLOW_MS = 1500       # warn above this
_VERY_SLOW_MS = 3000  # users start abandoning ~3s

_SKIP_PREFIXES = ("/static/", "/media/", "/healthz")


def _route_label(request) -> str:
    """Return a low-cardinality route label for metrics grouping."""
    try:
        match = getattr(request, "resolver_match", None)
        if match and match.url_name:
            ns = match.namespace + ":" if match.namespace else ""
            return f"{request.method} {ns}{match.url_name}"
    except Exception:
        pass
    # Fallback: method + path (unbounded — fine for low-traffic dev / small prod).
    return f"{request.method} {request.path}"


class RequestObservabilityMiddleware:
    """Wraps each request with timing + status logging + metrics counter."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip static / media / healthz — they don't tell us anything about
        # real user traffic and would drown the metrics dict.
        path = getattr(request, "path", "") or ""
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return self.get_response(request)

        started = time.perf_counter()
        response = self.get_response(request)
        try:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            request.duration_ms = duration_ms
            try:
                response["X-Response-Time-ms"] = str(duration_ms)
            except Exception:
                pass

            status = getattr(response, "status_code", 0) or 0
            route = _route_label(request)
            _metrics.record(route, status, duration_ms)

            if status >= 500:
                logger.warning(
                    "5xx %s -> %s in %.0fms", route, status, duration_ms
                )
            elif status >= 400 and status not in (401, 404):
                # 401 happens on every anon hit to a protected page; 404 spam from
                # bots is noise. Still counted in metrics, just not logged.
                logger.info("4xx %s -> %s in %.0fms", route, status, duration_ms)
            elif duration_ms >= _VERY_SLOW_MS:
                logger.warning(
                    "VERY_SLOW %s -> %s in %.0fms (>%sms)",
                    route, status, duration_ms, _VERY_SLOW_MS,
                )
            elif duration_ms >= _SLOW_MS:
                logger.info(
                    "SLOW %s -> %s in %.0fms (>%sms)",
                    route, status, duration_ms, _SLOW_MS,
                )
        except Exception:
            # Observability must never break the response.
            pass
        return response
