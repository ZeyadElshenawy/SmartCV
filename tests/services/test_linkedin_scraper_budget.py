"""Retry-budget tests for :func:`_construct_with_retry`.

The scraper's retry orchestrator is the load-bearing piece of the
LinkedIn scrape path — when Chrome refuses to launch (DevToolsActivePort
errors, zombie processes, version mismatches), the retry cascade can
take ~13 minutes before surfacing failure. The budget cap added on
2026-05-19 bounds total wall-clock time so user-visible hangs stay
under ``SCRAPE_BUDGET_SECONDS``.

These tests pin three properties of the budget logic:
  • fires fast when launches repeatedly fail (no runaway hang)
  • does NOT interrupt a successful attempt within budget
  • preserves the last underlying error for diagnostics
"""
from __future__ import annotations

import time

import pytest
from selenium.common.exceptions import WebDriverException

from profiles.services import linkedin_scraper as scraper_mod
from profiles.services.linkedin_scraper import (
    LinkedInScrapeBudgetExceeded,
    _construct_with_retry,
)


class TestRetryBudget:
    """Pin the budget cap added on 2026-05-19."""

    def test_budget_fires_when_launches_repeatedly_fail(self, monkeypatch):
        """If every Chrome launch raises a transient WebDriverException,
        the budget cap fires within ~SCRAPE_BUDGET_SECONDS rather than
        running through the full retry cascade. This is the production
        hang fix — the 13-minute observed wait should never recur."""
        monkeypatch.setattr(scraper_mod, 'SCRAPE_BUDGET_SECONDS', 2.0)
        # The 2s, 5s, 10s sleep schedule between attempts would already
        # blow the 2s budget on its own. Patch sleep to a no-op so the
        # test only measures the builder calls themselves.
        monkeypatch.setattr(scraper_mod, 'sleep', lambda _s: None)

        attempts = {'n': 0}

        def slow_failing_primary():
            attempts['n'] += 1
            time.sleep(1.0)
            raise WebDriverException("session not created (mock)")

        def slow_failing_fallback():
            attempts['n'] += 1
            time.sleep(1.0)
            raise WebDriverException("session not created (mock fallback)")

        start = time.monotonic()
        with pytest.raises(LinkedInScrapeBudgetExceeded) as exc_info:
            _construct_with_retry(
                slow_failing_primary,
                fallback_builder=slow_failing_fallback,
            )
        elapsed = time.monotonic() - start

        # Allow generous slack: the in-flight attempt that exhausts the
        # budget gets to complete (max 1s mock work), and a small
        # scheduling margin. The 13-minute prod hang would blow well past 6s.
        assert elapsed < 6.0, (
            f"Budget cap didn't fire — elapsed {elapsed:.1f}s. "
            f"The 13-minute hang would still occur."
        )
        assert exc_info.value.budget_seconds == 2.0
        assert exc_info.value.elapsed_seconds >= 2.0
        # Underlying error was preserved so ops/diagnostics can see WHY.
        assert 'session not created' in (
            exc_info.value.last_underlying_error or ''
        )
        # The cap should have prevented exhausting all 3+1 attempts.
        assert attempts['n'] < 4, (
            f"Expected budget to cut off retry cascade; got {attempts['n']} "
            f"attempts (full cascade is 3 primary + 1 fallback = 4)."
        )

    def test_budget_does_not_interrupt_successful_attempt(self, monkeypatch):
        """A successful launch within the budget completes normally
        and returns the builder's result — the budget logic must not
        introduce false-positive failures on the happy path."""
        monkeypatch.setattr(scraper_mod, 'SCRAPE_BUDGET_SECONDS', 5.0)
        monkeypatch.setattr(scraper_mod, 'sleep', lambda _s: None)

        sentinel_driver = object()

        def fast_succeeding_builder():
            time.sleep(0.1)
            return sentinel_driver

        result = _construct_with_retry(fast_succeeding_builder)
        assert result is sentinel_driver

    def test_budget_exceeded_preserves_last_error(self, monkeypatch):
        """LinkedInScrapeBudgetExceeded.last_underlying_error captures
        the most recent transient WebDriverException message so ops
        and aggregator log lines can surface WHY the budget exhausted."""
        monkeypatch.setattr(scraper_mod, 'SCRAPE_BUDGET_SECONDS', 1.0)
        monkeypatch.setattr(scraper_mod, 'sleep', lambda _s: None)

        # Message must match the scraper's `transient` substring set
        # ('cannot connect to chrome' / 'session not created' /
        # 'chrome not reachable') — non-transient errors bypass the
        # retry budget by design and raise immediately.
        underlying = (
            "session not created: DevToolsActivePort file doesn't exist"
        )

        def failing_builder():
            time.sleep(0.4)
            raise WebDriverException(underlying)

        with pytest.raises(LinkedInScrapeBudgetExceeded) as exc_info:
            _construct_with_retry(failing_builder)

        # The aggregator's user-visible message doesn't include the
        # underlying error, but logs and the exception attribute do.
        assert underlying in (exc_info.value.last_underlying_error or '')
        # __str__ also includes both elapsed time and the underlying
        # error so log lines that don't unpack the exception still
        # carry the diagnostic.
        assert underlying in str(exc_info.value)
        assert exc_info.value.budget_seconds == 1.0
