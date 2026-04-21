"""Discover outreach targets for a given job, without authenticated LinkedIn access.

Two strategies, both unauthenticated, both honest about hit rate:
  * find_hiring_team(job_url) — reuses the existing LinkedIn job scraper to look
    for a "Meet the hiring team" block in the public job-page HTML. Hit rate is
    low (LinkedIn renders this server-side only for some jobs).
  * find_peers_via_google(company, role_keywords, n) — a single-shot Google SERP
    scrape with `site:linkedin.com/in` to surface employees by role. Soft-fails
    to a "search this yourself" link when blocked.
"""

import logging
import re
from dataclasses import asdict, dataclass
from typing import List, Optional
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


@dataclass
class Target:
    handle: str            # e.g. "satyanadella"
    name: str              # e.g. "Satya Nadella"
    role: str              # e.g. "CEO at Microsoft"
    source: str            # "hiring_team" | "google"

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_handle(linkedin_url: str) -> Optional[str]:
    """Pull the vanity slug out of a /in/<handle>/ URL. Returns None on miss."""
    if not linkedin_url:
        return None
    match = re.search(r'/in/([^/?#]+)', linkedin_url)
    if not match:
        return None
    return match.group(1).lower()


def find_hiring_team(job_url: str) -> List[Target]:
    """Return targets from the public 'Meet the hiring team' block, if present.

    Hits the same anonymous endpoint as `linkedin_scraper.scrape_linkedin_job`.
    Often returns []; that's expected — most public job pages strip this block.
    """
    try:
        response = requests.get(job_url, headers=_HEADERS, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("find_hiring_team fetch failed: %s", exc)
        return []

    soup = BeautifulSoup(response.content, 'html.parser')
    targets: list[Target] = []

    # LinkedIn's public job page exposes the hiring team via either a
    # 'hirer-card__hirer-information' block or a generic /in/<handle> link
    # inside a section labelled "Meet the hiring team".
    candidates = soup.select(
        '.hirer-card__hirer-information a[href*="/in/"], '
        '[data-test-modal-id="hirer-modal"] a[href*="/in/"]'
    )
    seen: set[str] = set()
    for anchor in candidates:
        handle = _extract_handle(anchor.get('href', ''))
        if not handle or handle in seen:
            continue
        seen.add(handle)
        name = anchor.get_text(strip=True) or handle
        role_node = anchor.find_parent().find_next_sibling() if anchor.find_parent() else None
        role = role_node.get_text(strip=True) if role_node else ''
        targets.append(Target(handle=handle, name=name, role=role, source='hiring_team'))

    return targets


def find_peers_via_google(company: str, role_keywords: List[str], n: int = 10) -> List[Target]:
    """Search Google for `site:linkedin.com/in "<company>" "<keyword>"` profiles.

    Single attempt, no rotation, no paid SERP API. If Google 429s or returns no
    parseable results we return [] — the UI then surfaces a 'search yourself'
    link to the user. v1 scope.
    """
    if not company:
        return []

    keywords = ' '.join(f'"{kw}"' for kw in role_keywords if kw)
    query = f'site:linkedin.com/in "{company}" {keywords}'.strip()
    url = f'https://www.google.com/search?q={quote_plus(query)}&num={max(n, 10)}'

    try:
        response = requests.get(url, headers=_HEADERS, timeout=8)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("find_peers_via_google blocked: %s", exc)
        return []

    soup = BeautifulSoup(response.content, 'html.parser')
    targets: list[Target] = []
    seen: set[str] = set()

    for anchor in soup.select('a[href*="linkedin.com/in/"]'):
        href = anchor.get('href', '')
        # Google wraps result links in /url?q=<actual>&...
        if href.startswith('/url?'):
            match = re.search(r'q=([^&]+)', href)
            if match:
                href = match.group(1)
        if 'linkedin.com/in/' not in href:
            continue
        handle = _extract_handle(href)
        if not handle or handle in seen:
            continue
        seen.add(handle)

        # Pull the title text and split into "Name - Role | Company" if possible
        text = anchor.get_text(' ', strip=True) or handle
        name, _, role = text.partition(' - ')
        targets.append(Target(
            handle=handle,
            name=(name or handle).strip()[:128],
            role=role.strip()[:128],
            source='google',
        ))
        if len(targets) >= n:
            break

    return targets


def google_search_url(company: str, role_keywords: List[str]) -> str:
    """Fallback the UI can show users when the scrape returns nothing."""
    keywords = ' '.join(f'"{kw}"' for kw in role_keywords if kw)
    query = f'site:linkedin.com/in "{company}" {keywords}'.strip()
    return f'https://www.google.com/search?q={quote_plus(query)}'
