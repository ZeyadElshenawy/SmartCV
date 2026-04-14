"""Google Scholar profile aggregator.

Scrapes a public Google Scholar profile page (scholar.google.com/citations
?user={id}) for: name, affiliation, total citations, h-index, i10-index,
and top publications by citation count.

Caveats
- Google may show CAPTCHA or block the request. We treat any non-2xx, any
  parse-failure, and any "unusual traffic" interstitial as a soft failure
  (returns a snapshot with `error` set, never raises).
- 8s timeout per request — fail fast.
- One synchronous fetch per click — no background polling.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional, TypedDict

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 8


class ScholarPublication(TypedDict):
    title: str
    venue: Optional[str]
    year: Optional[str]
    citations: int


class ScholarSnapshot(TypedDict):
    user_id: str
    profile_url: str
    name: Optional[str]
    affiliation: Optional[str]
    total_citations: int
    h_index: int
    i10_index: int
    top_publications: list[ScholarPublication]
    fetched_at: str
    error: Optional[str]


def parse_scholar_user_id(value: str) -> Optional[str]:
    """Extract a Scholar user_id (`user=...` query param) from a URL or bare id.

    >>> parse_scholar_user_id("https://scholar.google.com/citations?user=ABC123XY&hl=en")
    'ABC123XY'
    >>> parse_scholar_user_id("scholar.google.com/citations?user=ABC123XY")
    'ABC123XY'
    >>> parse_scholar_user_id("ABC123XY")
    'ABC123XY'
    >>> parse_scholar_user_id("https://example.com/?user=NOPE")
    >>> parse_scholar_user_id("")
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()

    m = re.search(r"scholar\.google\.[a-z.]+/citations\?[^#]*\buser=([A-Za-z0-9_\-]+)", s, re.IGNORECASE)
    if m:
        return m.group(1)

    if "://" in s or "/" in s:
        return None

    if re.fullmatch(r"[A-Za-z0-9_\-]{6,16}", s):
        return s
    return None


def fetch_scholar_snapshot(value: str) -> ScholarSnapshot:
    """Fetch and parse a public Google Scholar profile.

    Returns a snapshot dict. On any failure (network, CAPTCHA, parse error)
    returns a snapshot with `error` set so callers can render a graceful
    fallback state.
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')
    user_id = parse_scholar_user_id(value)
    if not user_id:
        return ScholarSnapshot(
            user_id='', profile_url='', name=None, affiliation=None,
            total_citations=0, h_index=0, i10_index=0,
            top_publications=[], fetched_at=now_iso,
            error="Could not parse a Google Scholar user id from that input.",
        )

    profile_url = f"https://scholar.google.com/citations?user={user_id}&hl=en"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    try:
        r = requests.get(profile_url, headers=headers, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("Scholar fetch failed for %s: %s", user_id, e)
        return _empty_snapshot(user_id, profile_url, now_iso, "Scholar request failed.")

    if not r.ok:
        return _empty_snapshot(user_id, profile_url, now_iso, f"Scholar returned {r.status_code}.")

    # CAPTCHA / unusual-traffic interstitial detection
    if "/sorry/" in r.url or "unusual traffic" in r.text.lower():
        return _empty_snapshot(user_id, profile_url, now_iso,
                                "Scholar served a CAPTCHA — try again later.")

    try:
        soup = BeautifulSoup(r.text, "html.parser")
        name_el = soup.select_one("#gsc_prf_in")
        affil_el = soup.select_one("#gsc_prf_i .gsc_prf_il") or soup.select_one("#gsc_prf_i div")

        # Stats table: 3 rows × 2 cols (citations, h-index, i10) — first col "All"
        cells = soup.select("#gsc_rsb_st td.gsc_rsb_std")
        total_citations = _parse_int(cells[0].text) if len(cells) > 0 else 0
        h_index = _parse_int(cells[2].text) if len(cells) > 2 else 0
        i10_index = _parse_int(cells[4].text) if len(cells) > 4 else 0

        pubs: list[ScholarPublication] = []
        for row in soup.select("tr.gsc_a_tr")[:5]:
            title_el = row.select_one("a.gsc_a_at")
            venue_els = row.select(".gs_gray")
            year_el = row.select_one(".gsc_a_y span")
            cites_el = row.select_one("a.gsc_a_ac")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            venue = venue_els[1].get_text(strip=True) if len(venue_els) > 1 else None
            year = year_el.get_text(strip=True) if year_el else None
            cites = _parse_int(cites_el.get_text(strip=True)) if cites_el else 0
            pubs.append(ScholarPublication(
                title=title, venue=venue, year=year, citations=cites,
            ))

        return ScholarSnapshot(
            user_id=user_id,
            profile_url=profile_url,
            name=name_el.get_text(strip=True) if name_el else None,
            affiliation=affil_el.get_text(strip=True) if affil_el else None,
            total_citations=total_citations,
            h_index=h_index,
            i10_index=i10_index,
            top_publications=pubs,
            fetched_at=now_iso,
            error=None,
        )
    except Exception as e:
        logger.exception("Scholar parse failed for %s: %s", user_id, e)
        return _empty_snapshot(user_id, profile_url, now_iso, "Couldn't parse the Scholar page.")


def _empty_snapshot(user_id: str, url: str, ts: str, error: str) -> ScholarSnapshot:
    return ScholarSnapshot(
        user_id=user_id, profile_url=url, name=None, affiliation=None,
        total_citations=0, h_index=0, i10_index=0,
        top_publications=[], fetched_at=ts, error=error,
    )


def _parse_int(text: str) -> int:
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0
