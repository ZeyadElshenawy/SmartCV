"""Greenhouse job board scraper.

Greenhouse exposes a public JSON API for every board:
    https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}

This is FAR more reliable than HTML scraping, and Greenhouse's ToS
explicitly permits reading public job data via this API.

Typical URL patterns users will paste:
    https://boards.greenhouse.io/{board}/jobs/{id}
    https://job-boards.greenhouse.io/{board}/jobs/{id}
    https://{company}.greenhouse.io/jobs/{id}  (rare)
"""
import logging
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from .base import fetch, html_to_text, normalize_result, ScrapeError

logger = logging.getLogger(__name__)

_GH_URL_RE = re.compile(
    r"greenhouse\.io/(?:embed/job_app\?for=|[^/]+/jobs/|jobs/)([^/?#]+)",
    re.IGNORECASE,
)


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == 'greenhouse.io' or host.endswith('.greenhouse.io')


def _extract_board_and_id(url: str) -> tuple[str, str] | None:
    """Extract (board_token, job_id) from a Greenhouse URL."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    # boards.greenhouse.io/{board}/jobs/{id}
    # job-boards.greenhouse.io/{board}/jobs/{id}
    m = re.match(r'/([^/]+)/jobs/(\d+)', path)
    if m and ('boards.greenhouse.io' in host or 'job-boards.greenhouse.io' in host):
        return m.group(1), m.group(2)

    # {company}.greenhouse.io/jobs/{id}
    m = re.match(r'/jobs/(\d+)', path)
    if m and host.endswith('.greenhouse.io'):
        board = host.split('.')[0]
        return board, m.group(1)

    return None


def scrape(url: str) -> dict:
    parts = _extract_board_and_id(url)
    if not parts:
        raise ScrapeError(
            "Couldn't parse this Greenhouse URL. Expected a board URL like "
            "boards.greenhouse.io/acme/jobs/1234567."
        )
    board, job_id = parts
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=false"
    logger.info("Greenhouse scraper calling API: %s", api_url)

    resp = fetch(api_url, expect_json=True)
    try:
        data = resp.json()
    except ValueError:
        raise ScrapeError("Greenhouse returned a non-JSON response — the posting may have been removed.")

    title = data.get('title') or 'Unknown Title'
    company_data = data.get('company') or {}
    company = company_data.get('name') or board.replace('-', ' ').title()

    # Description is HTML — convert to text for the rest of the pipeline
    description_html = data.get('content') or ''
    # Greenhouse escapes HTML entities in content; un-unescape via BeautifulSoup
    description_text = html_to_text(BeautifulSoup(description_html, 'html.parser'))

    # Location
    location = None
    loc_obj = data.get('location') or {}
    if isinstance(loc_obj, dict):
        location = loc_obj.get('name')
    elif isinstance(loc_obj, str):
        location = loc_obj

    return normalize_result(
        'greenhouse',
        title=title,
        company=company,
        description=description_text,
        raw_html=description_html,
        cleaned_url=data.get('absolute_url') or url,
        location=location,
        posted_date=data.get('updated_at'),
    )
