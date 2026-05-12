import asyncio
import logging
import random
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import (
    CHROMIUM_LAUNCH_ARGS,
    INDEED_DATE_DAYS,
    JobRecord,
    ProgressReporter,
    debug_dump,
    extract_salary,
    make_stealth_context,
)

CARD_SELECTORS = [
    "div.job_seen_beacon",
    "div[data-testid='slider_item']",
    "li[data-testid='jobListItem']",
    "div.cardOutline",
    "td.resultContent",
]

logger = logging.getLogger("jobs.scraping.indeed")

INDEED_DEFAULT_BASE = "https://www.indeed.com"
PAGE_SIZE = 10
NAV_TIMEOUT = 30
CARD_WAIT = 12_000

INDEED_COUNTRY_HOSTS = {
    "egypt": "https://eg.indeed.com",
    "eg": "https://eg.indeed.com",
    "cairo": "https://eg.indeed.com",
    "alexandria": "https://eg.indeed.com",
    "uk": "https://uk.indeed.com",
    "united kingdom": "https://uk.indeed.com",
    "london": "https://uk.indeed.com",
    "germany": "https://de.indeed.com",
    "berlin": "https://de.indeed.com",
    "munich": "https://de.indeed.com",
    "france": "https://fr.indeed.com",
    "paris": "https://fr.indeed.com",
    "spain": "https://es.indeed.com",
    "madrid": "https://es.indeed.com",
    "italy": "https://it.indeed.com",
    "netherlands": "https://nl.indeed.com",
    "amsterdam": "https://nl.indeed.com",
    "belgium": "https://be.indeed.com",
    "brussels": "https://be.indeed.com",
    "poland": "https://pl.indeed.com",
    "warsaw": "https://pl.indeed.com",
    "uae": "https://www.indeed.ae",
    "dubai": "https://www.indeed.ae",
    "abu dhabi": "https://www.indeed.ae",
    "saudi arabia": "https://sa.indeed.com",
    "riyadh": "https://sa.indeed.com",
    "india": "https://in.indeed.com",
    "bangalore": "https://in.indeed.com",
    "mumbai": "https://in.indeed.com",
    "delhi": "https://in.indeed.com",
    "canada": "https://ca.indeed.com",
    "toronto": "https://ca.indeed.com",
    "australia": "https://au.indeed.com",
    "sydney": "https://au.indeed.com",
    "brazil": "https://br.indeed.com",
    "japan": "https://jp.indeed.com",
    "tokyo": "https://jp.indeed.com",
    "singapore": "https://sg.indeed.com",
}


def _pick_indeed_host(location: str) -> str:
    if not location:
        return INDEED_DEFAULT_BASE
    low = location.lower()
    for hint, host in INDEED_COUNTRY_HOSTS.items():
        if hint in low:
            return host
    return INDEED_DEFAULT_BASE


def build_indeed_url(keyword: str, location: str, date_posted: str, start: int) -> str:
    base = _pick_indeed_host(location)
    q = keyword.replace(" ", "+")
    l = location.replace(" ", "+")
    parts = [f"q={q}", f"l={l}", f"start={start}"]
    days = INDEED_DATE_DAYS.get(date_posted)
    if days is not None:
        parts.append(f"fromage={days}")
    return f"{base}/jobs?" + "&".join(parts)


def _parse_card(card_soup, country: str, base: str) -> Optional[JobRecord]:
    title_el = (
        card_soup.select_one("h2.jobTitle span[title]")
        or card_soup.select_one("h2.jobTitle span")
        or card_soup.select_one("h2.jobTitle a")
        or card_soup.select_one("a[data-testid='jobTitle']")
        or card_soup.select_one("h2 a")
    )
    title = title_el.get_text(strip=True) if title_el else ""

    company_el = (
        card_soup.select_one("span[data-testid='company-name']")
        or card_soup.select_one("[data-testid='company-name']")
        or card_soup.select_one("span.companyName")
        or card_soup.select_one("[data-testid='inlineHeader-companyName']")
    )
    company = company_el.get_text(strip=True) if company_el else ""

    loc_el = (
        card_soup.select_one("div[data-testid='text-location']")
        or card_soup.select_one("[data-testid='text-location']")
        or card_soup.select_one("div.companyLocation")
        or card_soup.select_one("[data-testid='job-location']")
    )
    location = loc_el.get_text(strip=True) if loc_el else ""

    link_el = (
        card_soup.select_one("a.jcs-JobTitle")
        or card_soup.select_one("h2.jobTitle a")
        or card_soup.select_one("a[data-testid='jobTitle']")
        or card_soup.select_one("a[id^='job_']")
    )
    url = ""
    if link_el and link_el.get("href"):
        href = link_el["href"]
        url = urljoin(base, href)
        jk = link_el.get("data-jk") or card_soup.get("data-jk")
        if not jk:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(href).query)
            jk = (qs.get("jk") or [""])[0]
        if jk:
            url = urljoin(base, f"/viewjob?jk={jk}")

    all_text = card_soup.get_text(separator=" ", strip=True)
    salary = extract_salary(all_text)

    if not (title or url):
        return None

    return JobRecord(
        source="Indeed",
        title=title,
        company=company,
        location=location,
        country=country,
        salary=salary,
        url=url,
        raw_text=all_text,
    )


async def scrape_indeed(
    keyword: str,
    location: str,
    *,
    date_posted: str = "any",
    max_jobs: int = 50,
    reporter: Optional[ProgressReporter] = None,
) -> List[JobRecord]:
    from playwright.async_api import async_playwright

    results: List[JobRecord] = []
    base = _pick_indeed_host(location)
    pages_needed = max(1, (max_jobs + PAGE_SIZE - 1) // PAGE_SIZE)
    logger.info("Indeed: scraping %s @ %s via %s (%d pages)", keyword, location, base, pages_needed)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=CHROMIUM_LAUNCH_ARGS)
        try:
            context = await make_stealth_context(browser, state_source="indeed")
            from .auth import has_saved_state
            if has_saved_state("indeed"):
                logger.info("Indeed: using saved session state")
            page = await context.new_page()

            for page_num in range(pages_needed):
                if reporter and reporter.cancelled():
                    break
                start = page_num * PAGE_SIZE
                url = build_indeed_url(keyword, location, date_posted, start)
                try:
                    await asyncio.wait_for(page.goto(url, wait_until="domcontentloaded"), timeout=NAV_TIMEOUT)
                except Exception as exc:
                    logger.warning("Indeed nav failed (page %d): %s", page_num, exc)
                    continue

                matched_sel = None
                for sel in CARD_SELECTORS:
                    try:
                        await page.wait_for_selector(sel, timeout=CARD_WAIT // len(CARD_SELECTORS))
                        matched_sel = sel
                        break
                    except Exception:
                        continue

                await page.evaluate("window.scrollTo(0, 600)")
                await asyncio.sleep(1.0)

                html = await page.content()
                soup = BeautifulSoup(html, "lxml")
                cards = []
                for sel in CARD_SELECTORS:
                    cards = soup.select(sel)
                    if cards:
                        matched_sel = sel
                        break

                logger.info("Indeed page %d: %d cards (selector=%s url=%s)",
                            page_num + 1, len(cards), matched_sel, url)

                if not cards:
                    dump = debug_dump("indeed", f"{location}_p{page_num}", html)
                    if dump:
                        logger.warning("Indeed: no cards on page %d. HTML dumped to %s", page_num, dump)
                    continue

                for c in cards:
                    if len(results) >= max_jobs:
                        break
                    rec = _parse_card(c, country=location, base=base)
                    if rec:
                        results.append(rec)
                        if reporter:
                            reporter.step(1, f"Indeed: {rec.title[:60]}")

                if len(results) >= max_jobs:
                    break
                await asyncio.sleep(random.uniform(2, 4))

            await page.close()
        finally:
            await browser.close()

    if reporter:
        reporter.set_total(len(results))
    return results
