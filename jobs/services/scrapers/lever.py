"""Lever job board scraper.

Lever exposes a public JSON API for every posting:
    https://api.lever.co/v0/postings/{org}/{posting_id}

Typical URL patterns users will paste:
    https://jobs.lever.co/{org}/{posting_id}
    https://jobs.lever.co/{org}/{posting_id}/apply
"""
import logging
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from .base import fetch, html_to_text, normalize_result, ScrapeError

logger = logging.getLogger(__name__)


def matches(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in ('jobs.lever.co', 'www.lever.co') or host.endswith('.lever.co')


def _extract_org_and_id(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    # /{org}/{posting_id} or /{org}/{posting_id}/apply
    m = re.match(r'/([^/]+)/([a-f0-9-]{8,})', path)
    if m:
        return m.group(1), m.group(2)
    return None


def scrape(url: str) -> dict:
    parts = _extract_org_and_id(url)
    if not parts:
        raise ScrapeError(
            "Couldn't parse this Lever URL. Expected jobs.lever.co/{company}/{posting_id}."
        )
    org, posting_id = parts
    api_url = f"https://api.lever.co/v0/postings/{org}/{posting_id}"
    logger.info("Lever scraper calling API: %s", api_url)

    resp = fetch(api_url, expect_json=True)
    try:
        data = resp.json()
    except ValueError:
        raise ScrapeError("Lever returned a non-JSON response — the posting may have been removed.")

    title = data.get('text') or 'Unknown Title'

    categories = data.get('categories') or {}
    location = categories.get('location')
    commitment = categories.get('commitment')  # full-time / contract / etc.
    team = categories.get('team')

    # Lever splits description into sections: descriptionPlain + lists[]
    description_parts: list[str] = []

    # Main description (Lever has `description` HTML and `descriptionPlain`)
    desc_html = data.get('description') or ''
    if desc_html:
        description_parts.append(html_to_text(BeautifulSoup(desc_html, 'html.parser')))

    for section in (data.get('lists') or []):
        text = section.get('text') or ''
        content_html = section.get('content') or ''
        if text:
            description_parts.append('')
            description_parts.append(text.upper())
        if content_html:
            description_parts.append(html_to_text(BeautifulSoup(content_html, 'html.parser')))

    additional = data.get('additional') or ''
    if additional:
        description_parts.append('')
        description_parts.append(html_to_text(BeautifulSoup(additional, 'html.parser')))

    description = '\n'.join(p for p in description_parts if p is not None).strip()

    if not description:
        raise ScrapeError("Lever returned an empty job description.")

    return normalize_result(
        'lever',
        title=title,
        company=org.replace('-', ' ').title(),
        description=description,
        raw_html=desc_html,
        cleaned_url=data.get('hostedUrl') or url,
        location=location,
        employment_type=commitment,
    )
