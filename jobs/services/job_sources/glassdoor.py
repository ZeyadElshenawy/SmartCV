import asyncio
import logging
import random
from typing import List, Optional
from urllib.parse import quote_plus, urljoin, urlparse, parse_qsl, urlencode

from bs4 import BeautifulSoup

from .base import (
    CHROMIUM_LAUNCH_ARGS,
    JobRecord,
    ProgressReporter,
    debug_dump,
    extract_salary,
    make_stealth_context,
)

logger = logging.getLogger("jobs.scraping.glassdoor")

GLASSDOOR_BASE = "https://www.glassdoor.com"
NAV_TIMEOUT = 30
CARD_WAIT = 12_000

LIST_SELECTORS = [
    "ul[aria-label='Jobs List'] li",
    "li[data-test='jobListing']",
    "li[class*='JobsList_jobListItem']",
    "div[data-test='jobListing']",
    "div[class*='JobsList_jobListItem']",
    "article[data-test='jobListing']",
]

TITLE_SELECTORS = [
    "a[data-test='job-title']",
    "a[data-test='job-link']",
    "a[class*='JobCard_jobTitle']",
    "a[class*='jobLink']",
    "h2 a",
    "h3 a",
]

COMPANY_SELECTORS = [
    "div[data-test='employer-name']",
    "span[data-test='employer-name']",
    "[class*='EmployerProfile_employerInfo']",
    "[class*='EmployerProfile_employerName']",
    "div.employer-name",
]

LOCATION_SELECTORS = [
    "div[data-test='emp-location']",
    "div[data-test='location']",
    "[class*='JobCard_location']",
    "[class*='location']",
]

SALARY_SELECTORS = [
    "div[data-test='detailSalary']",
    "[class*='JobCard_salaryEstimate']",
    "[class*='salaryEstimate']",
    "div[data-test='estimated-salary']",
]


def _first(card, selectors):
    for sel in selectors:
        el = card.select_one(sel)
        if el:
            return el
    return None


_GLASSDOOR_TRACKING_PARAMS = {
    "src", "srs", "ao", "s", "guid", "pos", "t", "vt", "ea", "uido",
    "cb", "jobListingId",
}


def _clean_glassdoor_url(href: str) -> str:
    if not href:
        return ""
    abs_url = urljoin(GLASSDOOR_BASE, href)
    parsed = urlparse(abs_url)
    kept = [
        (k, v) for (k, v) in parse_qsl(parsed.query, keep_blank_values=False)
        if k not in _GLASSDOOR_TRACKING_PARAMS
    ]
    new_query = urlencode(kept)
    cleaned = parsed._replace(query=new_query, fragment="").geturl()
    return cleaned


def build_glassdoor_url(
    keyword: str,
    location: str,
    loc_id: Optional[int] = None,
    loc_type: Optional[str] = None,
) -> str:
    parts = [
        f"sc.keyword={quote_plus(keyword)}",
        f"locKeyword={quote_plus(location)}",
    ]
    if loc_id is not None and loc_type:
        parts.append(f"locId={loc_id}")
        parts.append(f"locT={loc_type}")
    return f"{GLASSDOOR_BASE}/Job/jobs.htm?" + "&".join(parts)


async def _resolve_glassdoor_location(page, location: str):
    if not location:
        return None, None
    endpoints = [
        f"{GLASSDOOR_BASE}/findPopularLocationAjax.htm?maxLocationsToReturn=10&term={quote_plus(location)}",
        f"{GLASSDOOR_BASE}/searchsuggest/typeahead?numSuggestions=10&source=jobs&input={quote_plus(location)}",
    ]
    for ep in endpoints:
        try:
            resp = await page.request.get(ep, timeout=10_000)
            if resp.status != 200:
                continue
            try:
                data = await resp.json()
            except Exception:
                continue
            items = data if isinstance(data, list) else data.get("locations") or data.get("results") or []
            for item in items:
                lid = item.get("locationId") or item.get("id") or item.get("locId")
                ltype = item.get("locationType") or item.get("locType") or item.get("locT")
                if lid and ltype:
                    try:
                        lid_int = int(lid)
                    except (TypeError, ValueError):
                        continue
                    logger.info(
                        "Glassdoor: resolved %r -> locId=%s locT=%s (%s)",
                        location, lid_int, ltype, item.get("label") or item.get("longName") or "",
                    )
                    return lid_int, ltype
        except Exception as exc:
            logger.debug("Glassdoor location autocomplete %s failed: %s", ep, exc)
            continue
    logger.warning(
        "Glassdoor: could not resolve location %r — results will not be filtered.",
        location,
    )
    return None, None


def _parse_card(card, country: str) -> Optional[JobRecord]:
    title_el = _first(card, TITLE_SELECTORS)
    title = title_el.get_text(strip=True) if title_el else ""
    href = (title_el.get("href") or "").strip() if title_el else ""
    if title_el is not None:
        for attr in ("data-job-link", "data-job-url", "data-canonical"):
            val = (title_el.get(attr) or "").strip()
            if val:
                href = val
                break
    url = _clean_glassdoor_url(href)
    if url and "/job-listing/" not in url:
        url = ""

    company_el = _first(card, COMPANY_SELECTORS)
    company = company_el.get_text(strip=True) if company_el else ""

    location_el = _first(card, LOCATION_SELECTORS)
    location = location_el.get_text(strip=True) if location_el else ""

    salary_el = _first(card, SALARY_SELECTORS)
    salary = salary_el.get_text(strip=True) if salary_el else ""
    if not salary:
        salary = extract_salary(card.get_text(separator=" ", strip=True))

    if not (title or url):
        return None

    return JobRecord(
        source="Glassdoor",
        title=title,
        company=company,
        location=location,
        country=country,
        salary=salary,
        url=url,
        raw_text=card.get_text(separator=" ", strip=True),
    )


async def _try_dismiss_modal(page):
    """Glassdoor pops a sign-up modal aggressively."""
    selectors = [
        "button[data-test='modal-close']",
        "button[alt='Close']",
        "span.SVGInline.modal_closeIcon",
        "button[aria-label='Close']",
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue


async def scrape_glassdoor(
    keyword: str,
    location: str,
    *,
    max_jobs: int = 30,
    reporter: Optional[ProgressReporter] = None,
) -> List[JobRecord]:
    from playwright.async_api import async_playwright

    results: List[JobRecord] = []

    from .auth import has_saved_state
    if not has_saved_state("glassdoor"):
        logger.warning(
            "Glassdoor: no saved session — Cloudflare will block anonymous "
            "scrapes. Skipping. Run `python manage.py login_glassdoor` to "
            "save a session."
        )
        return results

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=CHROMIUM_LAUNCH_ARGS)
        try:
            context = await make_stealth_context(browser, state_source="glassdoor")
            logger.info("Glassdoor: using saved session state")
            page = await context.new_page()

            loc_id, loc_type = None, None
            if location:
                try:
                    await asyncio.wait_for(
                        page.goto(GLASSDOOR_BASE, wait_until="domcontentloaded"),
                        timeout=NAV_TIMEOUT,
                    )
                    loc_id, loc_type = await _resolve_glassdoor_location(page, location)
                except Exception as exc:
                    logger.warning("Glassdoor location warmup failed: %s", exc)

            url = build_glassdoor_url(keyword, location, loc_id, loc_type)
            logger.info("Glassdoor URL: %s", url)

            try:
                await asyncio.wait_for(page.goto(url, wait_until="domcontentloaded"), timeout=NAV_TIMEOUT)
            except Exception as exc:
                logger.warning("Glassdoor nav failed: %s", exc)
                return results

            await asyncio.sleep(random.uniform(2, 3))
            await _try_dismiss_modal(page)

            attempts = 0
            seen = 0
            cancelled = False
            html = ""
            while attempts < 10 and seen < max_jobs:
                if reporter and reporter.cancelled():
                    cancelled = True
                    break
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    cancelled = True
                    break
                await asyncio.sleep(random.uniform(1.5, 2.5))
                if reporter and reporter.cancelled():
                    cancelled = True
                    break
                await _try_dismiss_modal(page)
                try:
                    btn = await page.query_selector(
                        "button[data-test='load-more'], button[data-test='showMoreJobs']"
                    )
                    if btn and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(random.uniform(1.5, 2.5))
                except Exception:
                    pass
                if reporter and reporter.cancelled():
                    cancelled = True
                    break
                try:
                    html = await page.content()
                except Exception as exc:
                    logger.info("Glassdoor: page closed mid-scrape (%s)", exc)
                    cancelled = True
                    break
                soup = BeautifulSoup(html, "lxml")
                cards = []
                for sel in LIST_SELECTORS:
                    cards = soup.select(sel)
                    if cards:
                        break
                seen = len(cards)
                attempts += 1

            if cancelled:
                return results

            try:
                html = await page.content()
            except Exception as exc:
                logger.info("Glassdoor: page closed before final read (%s)", exc)
                return results
            try:
                await page.close()
            except Exception:
                pass

            soup = BeautifulSoup(html, "lxml")
            cards = []
            for sel in LIST_SELECTORS:
                cards = soup.select(sel)
                if cards:
                    logger.info("Glassdoor matched %d cards via %s", len(cards), sel)
                    break

            if not cards:
                dump = debug_dump("glassdoor", location, html)
                logger.warning(
                    "Glassdoor: no cards matched (likely anti-bot block). HTML dumped to %s",
                    dump,
                )
                return results

            for c in cards[:max_jobs]:
                if reporter and reporter.cancelled():
                    break
                rec = _parse_card(c, country=location)
                if rec:
                    results.append(rec)
                    if reporter:
                        reporter.step(1, f"Glassdoor: {rec.title[:60]}")

            if cards and not results:
                dump = debug_dump("glassdoor_cards_unparsed", location, html)
                logger.warning(
                    "Glassdoor: matched %d cards but parsed 0 — selectors stale. Dump: %s",
                    len(cards), dump,
                )
        finally:
            await browser.close()

    if reporter:
        reporter.set_total(len(results))
    return results
