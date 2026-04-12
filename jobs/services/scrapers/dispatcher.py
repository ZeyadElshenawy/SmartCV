"""
Dispatcher: picks the right scraper based on URL, returns a normalized
job dict. Falls back to the generic JSON-LD scraper for unknown hosts.
"""
import logging
from . import linkedin, indeed, greenhouse, lever, generic
from .base import ScrapeError

logger = logging.getLogger(__name__)


# Order matters: host-specific scrapers checked first, generic is last.
# Each entry: (name, module). `generic.matches(url)` always returns True,
# so it acts as the final fallback.
_SCRAPERS = [
    ('linkedin', linkedin),
    ('indeed', indeed),
    ('greenhouse', greenhouse),
    ('lever', lever),
    ('generic', generic),
]

SUPPORTED_SOURCES = tuple(name for name, _ in _SCRAPERS)


def scrape_job(url: str) -> dict:
    """
    Dispatch to the appropriate scraper by URL host.

    Returns the normalized job dict (see scrapers/__init__.py for shape).
    Raises ScrapeError with a user-facing message on failure.
    """
    if not url or not isinstance(url, str):
        raise ScrapeError("No URL provided.")

    url = url.strip()
    if not (url.startswith('http://') or url.startswith('https://')):
        url = 'https://' + url

    for name, module in _SCRAPERS:
        try:
            if module.matches(url):
                logger.info("Dispatch: using %s scraper for %s", name, url)
                return module.scrape(url)
        except ScrapeError:
            # Propagate user-facing errors immediately; no fallback to generic
            # for host-specific scrapers (the user explicitly pasted e.g. a
            # LinkedIn URL — if LinkedIn fails, generic won't do better).
            if name != 'generic':
                raise
            raise
        except Exception as e:
            logger.exception("Unexpected error in %s scraper: %s", name, e)
            if name != 'generic':
                # Try the next scraper in the chain (shouldn't really happen,
                # but let generic be a safety net).
                continue
            raise ScrapeError(f"Scraping failed: {e}")

    # Shouldn't be reachable because generic.matches() always returns True
    raise ScrapeError("No scraper available for this URL.")
