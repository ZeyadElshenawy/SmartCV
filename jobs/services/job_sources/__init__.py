"""Job-board scraping sources.

Ported from a standalone Playwright-based scraper. Each source is an async
function that returns a list of `JobRecord` and reports progress through a
`ProgressReporter` callback. The threaded runner (`runner.py`) drives them.

Login flow is one-time per source via `python manage.py login_<source>`,
which saves a Playwright `storage_state` JSON under
`settings.JOB_SCRAPER_STORAGE_DIR`.
"""

from .base import JobRecord, ProgressReporter
from .linkedin import scrape_linkedin
from .indeed import scrape_indeed
from .glassdoor import scrape_glassdoor

__all__ = [
    "JobRecord",
    "ProgressReporter",
    "scrape_linkedin",
    "scrape_indeed",
    "scrape_glassdoor",
]
