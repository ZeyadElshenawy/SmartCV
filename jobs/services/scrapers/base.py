"""Shared utilities for job scrapers: HTTP fetch, HTML cleanup, common errors."""
import re
import time
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


DEFAULT_HEADERS = {
    # Standard Chrome UA. Job boards reject requests without a real UA.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

DEFAULT_TIMEOUT = 10  # seconds
MAX_RETRIES = 2


class ScrapeError(Exception):
    """Raised when scraping fails in a way the user should know about."""


def fetch(url: str, headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
          expect_json: bool = False) -> requests.Response:
    """
    Fetch a URL with retries and a sensible user-agent.

    Raises ScrapeError with a user-facing message on any failure.
    """
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    if expect_json:
        merged_headers.setdefault("Accept", "application/json")

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=merged_headers, timeout=timeout,
                                allow_redirects=True)
            if resp.status_code == 429:
                # Rate-limited — back off and retry
                last_error = f"Rate-limited by {url}"
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp
        except requests.Timeout:
            last_error = f"Timed out after {timeout}s"
            logger.warning("Scrape timeout on attempt %d for %s", attempt + 1, url)
        except requests.HTTPError as e:
            # 4xx and some 5xx — don't retry 4xx except 429 (handled above)
            status = getattr(e.response, 'status_code', 'unknown')
            if 400 <= (status if isinstance(status, int) else 0) < 500:
                raise ScrapeError(
                    f"The job site returned {status}. "
                    "The posting may be private, expired, or require login."
                )
            last_error = f"HTTP {status}"
            logger.warning("Scrape HTTP error on attempt %d for %s: %s", attempt + 1, url, e)
        except requests.RequestException as e:
            last_error = str(e)
            logger.warning("Scrape request error on attempt %d for %s: %s", attempt + 1, url, e)

    raise ScrapeError(
        f"Couldn't reach the job page ({last_error}). "
        "Try pasting the job description manually instead."
    )


def html_to_text(html_or_soup) -> str:
    """Convert an HTML string or BeautifulSoup node to readable plain text."""
    if not html_or_soup:
        return ""
    soup = (BeautifulSoup(html_or_soup, 'html.parser')
            if isinstance(html_or_soup, str) else html_or_soup)
    text = soup.get_text(separator='\n', strip=True)
    # Collapse runs of blank lines
    lines, cleaned, empty = text.split('\n'), [], 0
    for line in lines:
        if line.strip():
            cleaned.append(line)
            empty = 0
        else:
            empty += 1
            if empty <= 1:
                cleaned.append('')
    return '\n'.join(cleaned).strip()


def normalize_result(source: str, **fields) -> dict:
    """
    Normalize a scraper's return value into the canonical shape the rest
    of the app expects. Unknown fields are dropped; missing optionals
    default to sensible values.
    """
    result = {
        'title': fields.get('title') or 'Unknown Title',
        'company': fields.get('company') or 'Unknown Company',
        'description': fields.get('description') or '',
        'raw_html': fields.get('raw_html') or '',
        'source': source,
        'cleaned_url': fields.get('cleaned_url') or fields.get('url') or '',
        'location': fields.get('location'),
        'employment_type': fields.get('employment_type'),
        'posted_date': fields.get('posted_date'),
        'company_url': fields.get('company_url'),
    }
    return result
