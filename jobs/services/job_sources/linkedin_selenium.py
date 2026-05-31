"""LinkedIn job-search scraper — credential-based Selenium path.

Mirrors the public interface of ``jobs/services/job_sources/linkedin.py``
(Playwright + saved session) so ``runner.py`` can swap between them
transparently. This module is the preferred LinkedIn path when
``LINKEDIN_EMAIL`` and ``LINKEDIN_PASSWORD`` are configured — same auth
pattern the user's profile scraper already uses
(``profiles/services/linkedin_scraper.py``), reused here for job-search.

Synchronous. Selenium's API is sync; we don't need asyncio for this path.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from django.conf import settings

from .base import (
    JobRecord,
    LINKEDIN_DATE_MAP,
    LINKEDIN_EXP_MAP,
    LINKEDIN_WT_MAP,
    ProgressReporter,
    extract_salary,
)

logger = logging.getLogger("jobs.scraping.linkedin_selenium")

LINKEDIN_BASE = "https://www.linkedin.com"
MAX_SCROLL_ATTEMPTS = 12
SCROLL_PAUSE_SECONDS = 2.0


def build_linkedin_url(
    keyword: str,
    location: str,
    exp_codes: List[str],
    wt_codes: List[str],
    date_posted: str,
) -> str:
    """Same URL builder as the Playwright path. Kept local so we don't
    have to refactor the existing module just to share one function."""
    url = (
        f"{LINKEDIN_BASE}/jobs/search/"
        f"?keywords={quote_plus(keyword)}&location={quote_plus(location)}"
    )
    if exp_codes:
        url += f"&f_E={','.join(exp_codes)}"
    if wt_codes:
        url += f"&f_WT={','.join(wt_codes)}"
    date_param = LINKEDIN_DATE_MAP.get(date_posted, "")
    if date_param:
        url += f"&f_TPR={date_param}"
    url += "&position=1&pageNum=0"
    return url


def _scrape_settings() -> dict:
    """Pull LinkedIn config from Django settings. Mirrors
    ``profiles.services.linkedin_aggregator._scrape_settings`` so the
    same env vars work for both flows."""
    return {
        'enabled': bool(getattr(settings, 'LINKEDIN_SCRAPING_ENABLED', False)),
        'email': getattr(settings, 'LINKEDIN_EMAIL', '') or '',
        'password': getattr(settings, 'LINKEDIN_PASSWORD', '') or '',
        'headless': bool(getattr(settings, 'LINKEDIN_HEADLESS', True)),
        'use_undetected': bool(getattr(settings, 'LINKEDIN_USE_UNDETECTED', True)),
        'login_wait': float(getattr(settings, 'LINKEDIN_LOGIN_WAIT', 5.0) or 5.0),
        'page_wait': float(getattr(settings, 'LINKEDIN_PAGE_WAIT', 4.0) or 4.0),
        'challenge_timeout': float(getattr(settings, 'LINKEDIN_CHALLENGE_TIMEOUT', 300.0) or 300.0),
        'profiles_dir': getattr(settings, 'LINKEDIN_PROFILES_DIR', None),
        'imap_user': getattr(settings, 'LINKEDIN_IMAP_USER', '') or '',
        'imap_password': getattr(settings, 'LINKEDIN_IMAP_PASSWORD', '') or '',
        'imap_host': getattr(settings, 'LINKEDIN_IMAP_HOST', '') or '',
        'imap_port': int(getattr(settings, 'LINKEDIN_IMAP_PORT', 993) or 993),
        'imap_timeout': float(getattr(settings, 'LINKEDIN_IMAP_TIMEOUT', 120.0) or 120.0),
    }


def credentials_configured() -> bool:
    """Cheap pre-check the runner / view layer can use to decide whether
    this Selenium path is even available before paying for a driver launch."""
    cfg = _scrape_settings()
    return bool(cfg['enabled'] and cfg['email'] and cfg['password'])


# ---------------------------------------------------------------------------
# Card parsing — tries authenticated selectors first, falls back to public.
# ---------------------------------------------------------------------------

# Authenticated-view selectors (logged-in LinkedIn jobs UI, 2025-2026).
_AUTH_CARD_SELECTORS = [
    "li.scaffold-layout__list-item",
    "li.jobs-search-results__list-item",
    "div.job-card-container",
]
# Public/guest-view selectors — what the existing Playwright scraper uses.
_PUBLIC_CARD_SELECTORS = ["div.base-card"]


def _find_cards(soup: BeautifulSoup) -> tuple[list, str]:
    """Return (list_of_card_tags, mode) where mode is 'auth' or 'public'."""
    for sel in _AUTH_CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            return cards, 'auth'
    for sel in _PUBLIC_CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            return cards, 'public'
    return [], 'none'


def _text_or(node, default=""):
    return node.get_text(strip=True) if node else default


def _href_or(node, default=""):
    if node and node.get("href"):
        return node.get("href").split("?", 1)[0]
    return default


def _parse_authenticated_card(card, country: str) -> Optional[JobRecord]:
    """Parse a logged-in LinkedIn job card. Selectors here changed in
    LinkedIn's 2024-2026 layout; we try a few that have appeared in
    different rollouts."""
    # Job URL + title.
    a = (
        card.select_one("a.job-card-list__title")
        or card.select_one("a.job-card-container__link")
        or card.select_one("a.job-card-job-posting-card-wrapper__card-link")
        or card.select_one("a[href*='/jobs/view/']")
    )
    job_url = _href_or(a)
    if job_url and job_url.startswith("/"):
        job_url = LINKEDIN_BASE + job_url

    title = ""
    if a:
        # LinkedIn often nests a span with the visible title or sr-only text.
        sr = a.select_one("span[aria-hidden='true'], span.sr-only, strong")
        title = _text_or(sr) or _text_or(a)
    if not title:
        t = card.select_one(".job-card-list__title, .job-card-container__title")
        title = _text_or(t)

    # Company.
    company_el = (
        card.select_one(".job-card-container__primary-description")
        or card.select_one(".artdeco-entity-lockup__subtitle")
        or card.select_one(".job-card-container__company-name")
    )
    company = _text_or(company_el)

    # Location.
    loc_el = (
        card.select_one(".job-card-container__metadata-wrapper li")
        or card.select_one(".artdeco-entity-lockup__caption")
        or card.select_one(".job-card-container__metadata-item")
    )
    location = _text_or(loc_el)

    # Posted timestamp.
    posted_el = card.select_one("time")
    posted = _text_or(posted_el)

    if not (title or job_url):
        return None
    return JobRecord(
        source="LinkedIn",
        title=title,
        company=company,
        location=location,
        country=country,
        posted=posted,
        url=job_url,
    )


def _parse_public_card(card, country: str) -> Optional[JobRecord]:
    """Identical to ``jobs/services/job_sources/linkedin.py:_parse_card``
    — kept here so this module is self-contained."""
    a = card.find("a", class_="base-card__full-link")
    job_url = (a.get("href") or "").strip() if a else ""
    if job_url:
        job_url = job_url.split("?", 1)[0]

    title = ""
    if a:
        sr = a.find("span", class_="sr-only")
        if sr:
            title = sr.get_text(strip=True)
    if not title:
        h3 = card.find("h3", class_="base-search-card__title")
        if h3:
            title = h3.get_text(strip=True)

    company = ""
    company_url = ""
    sub = card.find("h4", class_="base-search-card__subtitle")
    if sub:
        a_c = sub.find("a")
        if a_c:
            company = a_c.get_text(strip=True)
            company_url = (a_c.get("href") or "").strip().split("?", 1)[0]
        else:
            company = sub.get_text(strip=True)

    loc = card.find("span", class_="job-search-card__location")
    location = loc.get_text(strip=True) if loc else ""

    posted = ""
    t = card.find("time", class_="job-search-card__listdate") or card.find("time")
    if t:
        posted = t.get_text(strip=True)

    if not (title or job_url):
        return None
    return JobRecord(
        source="LinkedIn",
        title=title,
        company=company,
        company_url=company_url,
        location=location,
        country=country,
        posted=posted,
        url=job_url,
    )


# ---------------------------------------------------------------------------
# Main entry point — runner.py calls this when LinkedIn credentials are set.
# ---------------------------------------------------------------------------

def scrape_linkedin_selenium(
    keyword: str,
    location: str,
    *,
    experience_levels: Optional[List[str]] = None,
    workplace_types: Optional[List[str]] = None,
    date_posted: str = "any",
    max_jobs: int = 50,
    reporter: Optional[ProgressReporter] = None,
) -> List[JobRecord]:
    """Scrape LinkedIn job search using stored email+password credentials.

    Mirrors the signature of the Playwright ``scrape_linkedin`` so
    ``runner.py`` can route to either based on whether credentials are
    available. Synchronous — Selenium drivers don't need asyncio.

    Returns a list of ``JobRecord`` (max len ``max_jobs``). On any
    authentication / page-load failure, logs and returns an empty list
    (the runner already catches Exceptions, but returning [] keeps the
    contract cleaner for callers).
    """
    cfg = _scrape_settings()
    if not cfg['enabled']:
        logger.warning("LINKEDIN_SCRAPING_ENABLED is False; skipping LinkedIn scrape.")
        return []
    if not cfg['email'] or not cfg['password']:
        logger.warning(
            "LINKEDIN_EMAIL / LINKEDIN_PASSWORD not set; cannot run Selenium LinkedIn scrape."
        )
        return []

    # Import lazily so a missing selenium install doesn't break runner import.
    try:
        from profiles.services.linkedin_scraper import (
            LinkedInScraperError,
            ensure_logged_in,
        )
    except ImportError as exc:
        logger.warning("LinkedIn scraper deps not installed: %s", exc)
        return []

    exp_codes = [LINKEDIN_EXP_MAP[e] for e in (experience_levels or []) if e in LINKEDIN_EXP_MAP]
    wt_codes = [LINKEDIN_WT_MAP[w] for w in (workplace_types or []) if w in LINKEDIN_WT_MAP]
    search_url = build_linkedin_url(keyword, location, exp_codes, wt_codes, date_posted)
    logger.info("LinkedIn (Selenium) URL: %s", search_url)

    imap_creds = None
    if cfg['imap_user'] and cfg['imap_password']:
        imap_creds = {
            'user': cfg['imap_user'],
            'password': cfg['imap_password'],
            'host': cfg['imap_host'],
            'port': cfg['imap_port'],
            'timeout': cfg['imap_timeout'],
        }

    driver = None
    try:
        # Pre-login cancel checkpoint — most expensive operation hasn't
        # started yet. If the user clicked Cancel before Chrome launched,
        # bail before paying for ensure_logged_in.
        if reporter and reporter.cancelled():
            logger.info("LinkedIn scrape cancelled before login")
            return []

        # Land on the search URL directly — ensure_logged_in handles the
        # case where LinkedIn intercepts with /login or /checkpoint.
        try:
            driver = ensure_logged_in(
                profile_url=search_url,
                email=cfg['email'],
                password=cfg['password'],
                profiles_root=cfg['profiles_dir'],
                use_undetected=cfg['use_undetected'],
                headless=cfg['headless'],
                login_wait=cfg['login_wait'],
                page_wait=cfg['page_wait'],
                challenge_timeout=cfg['challenge_timeout'],
                imap_creds=imap_creds,
            )
        except LinkedInScraperError as exc:
            logger.warning("LinkedIn login failed: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected Selenium error during LinkedIn login")
            return []

        # Post-login cancel checkpoint — driver is up, login worked, but
        # if the user cancelled during login (typical: 10-60s in headless,
        # longer with captcha) we should stop before scraping.
        if reporter and reporter.cancelled():
            logger.info("LinkedIn scrape cancelled after login (pre-scroll)")
            return []

        # If the driver landed somewhere other than the search URL after
        # auth (LinkedIn sometimes parks you on /feed first), navigate
        # explicitly.
        try:
            current = driver.current_url or ""
            if "/jobs/" not in current:
                driver.get(search_url)
                time.sleep(cfg['page_wait'])
        except Exception:  # noqa: BLE001
            pass

        # Scroll to materialise additional cards. LinkedIn lazy-loads.
        # Emit one heartbeat progress tick every 2 scroll attempts so the
        # bar visibly advances during this 24-90s phase instead of freezing.
        scroll_heartbeat_total = MAX_SCROLL_ATTEMPTS // 2
        if reporter:
            reporter.set_total(scroll_heartbeat_total)
        for attempt in range(MAX_SCROLL_ATTEMPTS):
            if reporter and reporter.cancelled():
                logger.info("LinkedIn scrape cancelled at scroll attempt %d", attempt)
                break
            try:
                driver.execute_script(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            except Exception:  # noqa: BLE001
                pass
            time.sleep(SCROLL_PAUSE_SECONDS)
            # Heartbeat: emit a progress tick every 2 scroll attempts.
            if reporter and attempt % 2 == 1:
                reporter.step(1, f"Scrolling LinkedIn results… ({attempt + 1}/{MAX_SCROLL_ATTEMPTS})")
            # Best-effort: click any "show more" pagination button if present.
            try:
                from selenium.webdriver.common.by import By
                btns = driver.find_elements(
                    By.CSS_SELECTOR,
                    "button.infinite-scroller__show-more-button, "
                    "button[aria-label='See more jobs']",
                )
                if btns and btns[0].is_displayed():
                    btns[0].click()
                    time.sleep(SCROLL_PAUSE_SECONDS)
            except Exception:  # noqa: BLE001
                pass

        try:
            html = driver.page_source
        except Exception as exc:
            logger.warning("Failed to read page_source: %s", exc)
            return []

        soup = BeautifulSoup(html, "lxml")
        cards, mode = _find_cards(soup)
        logger.info("LinkedIn (Selenium) found %d cards (mode=%s, limit=%d)",
                    len(cards), mode, max_jobs)

        if not cards:
            # Last-resort: try to find anchors that point at /jobs/view/.
            anchors = soup.select("a[href*='/jobs/view/']")
            logger.info("LinkedIn fallback: %d job-view anchors", len(anchors))
            # Best-effort: synthesize JobRecord from anchor href + text.
            seen_urls: set[str] = set()
            results: list[JobRecord] = []
            for a in anchors[:max_jobs]:
                href = (a.get("href") or "").split("?", 1)[0]
                if href.startswith("/"):
                    href = LINKEDIN_BASE + href
                if not href or href in seen_urls:
                    continue
                seen_urls.add(href)
                title = a.get_text(strip=True) or "LinkedIn job"
                results.append(JobRecord(
                    source="LinkedIn",
                    title=title[:200],
                    url=href,
                    country=location,
                ))
            if reporter:
                reporter.set_total(len(results))
                for r in results:
                    reporter.step(1, f"LinkedIn: {r.title[:60]}")
            return results

        cards = cards[:max_jobs]
        parser = _parse_authenticated_card if mode == 'auth' else _parse_public_card

        # Fix: call set_total BEFORE the step loop so the percentage
        # math is correct from the first tick instead of snapping at the end.
        if reporter:
            reporter.set_total(len(cards))

        results = []
        for c in cards:
            if reporter and reporter.cancelled():
                logger.info("LinkedIn scrape cancelled mid-parse (results=%d)", len(results))
                break
            try:
                rec = parser(c, country=location)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Card parse failed: %s", exc)
                continue
            if not rec:
                continue
            # Try to lift a description snippet directly out of the card —
            # avoids a per-job detail fetch which doubles auth surface area.
            snippet_el = c.select_one(
                ".job-card-list__insight-text, "
                ".job-card-container__snippet, "
                ".job-search-card__description"
            )
            if snippet_el:
                snippet = snippet_el.get_text(separator=" ", strip=True)
                rec.description = snippet
                rec.raw_text = snippet
                if not rec.salary:
                    rec.salary = extract_salary(snippet)
            results.append(rec)
            if reporter:
                reporter.step(1, f"LinkedIn: {rec.title[:60]}")

        return results

    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass
