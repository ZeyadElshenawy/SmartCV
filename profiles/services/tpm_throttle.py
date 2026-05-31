"""Rolling-window TPM throttle for the Groq client.

Groq enforces a *per-minute token throughput* cap (default 30,000 TPM on
the on-demand tier). A single supervised resume generation fires ~12 LLM
calls in under a minute; even if each call fits the per-request ceiling,
the *cumulative* minute window 413s on `tokens per minute (TPM)` —
silently dumping us into the offline fallback. Char-count budgeting at
the request level can't catch this; the constraint is throughput, not
size.

This module exposes a single process-wide `TPMThrottle` that wraps
`.invoke()` on every LangChain object returned from `llm_engine`. Before
each call it estimates the token cost (input chars / ~3.5 + reserved
output tokens) and, if appending that cost to the rolling 60s window
would exceed the budget, sleeps until the oldest events age out enough
to make room. Default budget is 28,000 — 2k headroom under the 30k
Groq limit.

Disable in tests via `GROQ_TPM_THROTTLE_DISABLED=True`. Adjust budget
via `GROQ_TPM_BUDGET`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Iterable

logger = logging.getLogger(__name__)


_CHARS_PER_TOKEN = 3.5
_DEFAULT_BUDGET = 28_000
_WINDOW_SEC = 60.0
_OUTPUT_RESERVE_FLOOR = 200  # safety pad on every call


def _settings():
    try:
        from django.conf import settings as _dj
        return _dj
    except Exception:
        return None


class TPMThrottle:
    """Rolling-minute token-bucket throttle.

    Thread-safe (lock-guarded). Single global instance is fine: every
    LLM call in the process should serialize through one rolling window
    so cross-feature bursts (resume gen + supervisor + classifier all
    firing at once) don't collectively exceed Groq's per-org cap.
    """

    def __init__(self, budget: int = _DEFAULT_BUDGET, window: float = _WINDOW_SEC) -> None:
        self.budget = int(budget)
        self.window = float(window)
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    # --- Public ---

    def reserve(self, tokens: int) -> float:
        """Reserve `tokens` against the rolling minute window.

        Sleeps if needed to stay under budget. Returns the actual sleep
        duration in seconds (0.0 if no wait was needed) — useful for
        tests and logs.
        """
        tokens = max(1, int(tokens))
        slept = 0.0
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            used = sum(t for _, t in self._events)
            need_wait = used + tokens > self.budget
            if need_wait and self._events:
                # Sleep until enough oldest events expire to make room.
                wait_until = self._compute_release_time(tokens, now)
                slept = max(0.0, wait_until - now)
                if slept > 0:
                    logger.info(
                        "TPMThrottle: window %d + %d > budget %d — sleeping %.2fs",
                        used, tokens, self.budget, slept,
                    )
                    # Release the lock while sleeping so a parallel call
                    # can also reserve concurrently after the sleep ends.
                    self._lock.release()
                    try:
                        time.sleep(slept)
                    finally:
                        self._lock.acquire()
                    now = time.monotonic()
                    self._purge(now)
            self._events.append((now, tokens))
        return slept

    def current_usage(self) -> int:
        with self._lock:
            self._purge(time.monotonic())
            return sum(t for _, t in self._events)

    def reset(self) -> None:
        """Clear the window. Test helper; not for production paths."""
        with self._lock:
            self._events.clear()

    # --- Internals ---

    def _purge(self, now: float) -> None:
        cutoff = now - self.window
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def _compute_release_time(self, need: int, now: float) -> float:
        """Earliest time (monotonic) at which `need` more tokens fit."""
        used = sum(t for _, t in self._events)
        for ts, tok in self._events:
            used -= tok
            if used + need <= self.budget:
                return ts + self.window
        # Window can't fit even after full drain (need > budget). Sleep
        # the full window; caller will still proceed but the call may
        # 413 on its own.
        return now + self.window


def _resolve_budget() -> int:
    s = _settings()
    if s is None:
        return _DEFAULT_BUDGET
    try:
        return int(getattr(s, 'GROQ_TPM_BUDGET', _DEFAULT_BUDGET))
    except Exception:
        return _DEFAULT_BUDGET


def _is_disabled() -> bool:
    s = _settings()
    if s is None:
        return False
    return bool(getattr(s, 'GROQ_TPM_THROTTLE_DISABLED', False))


_GLOBAL_THROTTLE: TPMThrottle | None = None
_GLOBAL_LOCK = threading.Lock()


def get_throttle() -> TPMThrottle:
    """Return the process-wide throttle, building it lazily."""
    global _GLOBAL_THROTTLE
    if _GLOBAL_THROTTLE is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_THROTTLE is None:
                _GLOBAL_THROTTLE = TPMThrottle(budget=_resolve_budget())
    return _GLOBAL_THROTTLE


def reset_throttle() -> None:
    """Drop the global throttle (test helper)."""
    global _GLOBAL_THROTTLE
    with _GLOBAL_LOCK:
        _GLOBAL_THROTTLE = None


def estimate_input_tokens(payload) -> int:
    """Char-count → token estimate for a LangChain invoke() argument.

    Handles the three shapes our codebase uses:
      - str (the structured-output prompt)
      - list[BaseMessage] (the plain-chat path)
      - prompt-template values, falls back to repr length
    """
    try:
        if isinstance(payload, str):
            chars = len(payload)
        elif isinstance(payload, (list, tuple)):
            chars = 0
            for m in payload:
                # LangChain messages have .content; tuples carry (role, content)
                if hasattr(m, 'content'):
                    chars += len(getattr(m, 'content') or '')
                elif isinstance(m, (list, tuple)) and len(m) == 2:
                    chars += len(str(m[1]) or '')
                else:
                    chars += len(str(m))
        elif isinstance(payload, dict):
            chars = sum(len(str(v)) for v in payload.values())
        else:
            chars = len(str(payload))
    except Exception:
        chars = 4000  # conservative fallback
    return max(1, int(chars / _CHARS_PER_TOKEN))


def reserve_for_invoke(payload, max_output_tokens: int) -> float:
    """Public helper: reserve budget for an upcoming `.invoke()` call.

    Returns the sleep duration (0.0 if none needed). When throttling is
    disabled via setting, returns 0.0 immediately without recording.
    """
    if _is_disabled():
        return 0.0
    est = estimate_input_tokens(payload) + max(_OUTPUT_RESERVE_FLOOR, int(max_output_tokens or 0))
    return get_throttle().reserve(est)
