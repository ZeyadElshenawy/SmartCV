"""Per-source session storage. Lets the scrapers reuse a logged-in session
saved by `python manage.py login_<source>` — no passwords in env vars, works
even with magic-link / SSO / MFA logins.

Storage location is configured by `settings.JOB_SCRAPER_STORAGE_DIR`.
"""

from pathlib import Path

from django.conf import settings


def _state_dir() -> Path:
    p = Path(settings.JOB_SCRAPER_STORAGE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


# Public alias, kept for parity with the reference module
def state_path(source: str) -> Path:
    return _state_dir() / f"{source}.json"


def has_saved_state(source: str) -> bool:
    p = state_path(source)
    return p.exists() and p.stat().st_size > 50


# Re-export for callers that historically used STATE_DIR as a constant.
# Resolved lazily so tests that override settings.JOB_SCRAPER_STORAGE_DIR
# pick up the override.
def STATE_DIR() -> Path:  # noqa: N802 — deliberately named to mirror the reference constant
    return _state_dir()
