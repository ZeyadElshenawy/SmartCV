"""Generic JSON-LD job scraper.

Most modern career sites embed schema.org JobPosting data in <script
type="application/ld+json"> tags. This scraper parses that structured
data and works across many sites (Workday, SmartRecruiters, Ashby,
SmartRecruiters, Personio, most corporate career pages that care
about SEO) without site-specific logic.

This is the fallback when no host-specific scraper matches.
"""
import json
import logging
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from .base import fetch, html_to_text, normalize_result, ScrapeError

logger = logging.getLogger(__name__)


def matches(url: str) -> bool:
    """Generic scraper is the fallback — always returns True when asked."""
    return True


def _find_job_posting_ld(soup: BeautifulSoup) -> dict | None:
    """Walk <script type='application/ld+json'> blocks and find JobPosting."""
    for tag in soup.find_all('script', type='application/ld+json'):
        raw = tag.string or tag.get_text() or ''
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue

        # JSON-LD can be a single object, a list, or a @graph container
        candidates = []
        if isinstance(data, list):
            candidates.extend(data)
        elif isinstance(data, dict):
            if '@graph' in data and isinstance(data['@graph'], list):
                candidates.extend(data['@graph'])
            else:
                candidates.append(data)

        for c in candidates:
            if not isinstance(c, dict):
                continue
            types = c.get('@type')
            if isinstance(types, list):
                if 'JobPosting' in types:
                    return c
            elif types == 'JobPosting':
                return c
    return None


def _extract_company(ld: dict) -> str:
    hiring = ld.get('hiringOrganization')
    if isinstance(hiring, dict):
        return hiring.get('name') or 'Unknown Company'
    if isinstance(hiring, str):
        return hiring
    return 'Unknown Company'


def _extract_location(ld: dict) -> str | None:
    loc = ld.get('jobLocation')
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        addr = loc.get('address') or {}
        if isinstance(addr, dict):
            parts = [addr.get('addressLocality'), addr.get('addressRegion'), addr.get('addressCountry')]
            return ', '.join(p for p in parts if p) or None
    return None


def scrape(url: str) -> dict:
    logger.info("Generic JSON-LD scraper fetching %s", url)
    resp = fetch(url)
    soup = BeautifulSoup(resp.content, 'html.parser')

    ld = _find_job_posting_ld(soup)
    if not ld:
        host = urlparse(url).netloc
        raise ScrapeError(
            f"Couldn't find structured job data on {host}. This site may not "
            "be supported yet — please paste the job description manually."
        )

    title = ld.get('title') or 'Unknown Title'
    company = _extract_company(ld)
    location = _extract_location(ld)
    employment_type = ld.get('employmentType')
    posted_date = ld.get('datePosted')

    # Description is typically HTML
    description_raw = ld.get('description') or ''
    description_text = html_to_text(BeautifulSoup(description_raw, 'html.parser')) if description_raw else ''

    if not description_text:
        raise ScrapeError(
            "Found a job posting but its description was empty. "
            "Please paste the description manually."
        )

    return normalize_result(
        'generic',
        title=title,
        company=company,
        description=description_text,
        raw_html=description_raw if isinstance(description_raw, str) else '',
        cleaned_url=url,
        location=location,
        employment_type=(employment_type if isinstance(employment_type, str)
                         else (employment_type[0] if isinstance(employment_type, list) else None)),
        posted_date=posted_date if isinstance(posted_date, str) else None,
    )
