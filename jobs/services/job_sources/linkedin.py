import asyncio
import logging
import random
from typing import List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .base import (
    CHROMIUM_LAUNCH_ARGS,
    JobRecord,
    LINKEDIN_DATE_MAP,
    LINKEDIN_EXP_MAP,
    LINKEDIN_WT_MAP,
    ProgressReporter,
    extract_salary,
    make_stealth_context,
)

logger = logging.getLogger("jobs.scraping.linkedin")

LINKEDIN_BASE = "https://www.linkedin.com"
DETAIL_CONCURRENCY = 3  # guest endpoint rate-limits aggressively
MAX_SCROLL_ATTEMPTS = 20
SCROLL_PAUSE = (1.5, 2.5)
DETAIL_TIMEOUT = 25
NAV_TIMEOUT = 30


def build_linkedin_url(
    keyword: str,
    location: str,
    exp_codes: List[str],
    wt_codes: List[str],
    date_posted: str,
) -> str:
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


async def _human_scroll(page, attempts: int, reporter: Optional[ProgressReporter]):
    last_height = await page.evaluate("document.body.scrollHeight")
    for _ in range(attempts):
        if reporter and reporter.cancelled():
            return
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(random.uniform(*SCROLL_PAUSE))
        try:
            btn = await page.query_selector("button.infinite-scroller__show-more-button")
            if btn and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(random.uniform(*SCROLL_PAUSE))
        except Exception:
            pass
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


async def _fetch_detail(context, url: str) -> tuple[str, str]:
    job_desc, company_desc = "", ""
    if not url:
        return job_desc, company_desc
    page = await context.new_page()
    try:
        await asyncio.wait_for(page.goto(url, wait_until="domcontentloaded"), timeout=NAV_TIMEOUT)
        try:
            await page.wait_for_selector(
                "div.description__text, div.show-more-less-html__markup",
                timeout=DETAIL_TIMEOUT * 1000,
            )
        except Exception:
            pass
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")
        desc_el = soup.find("div", class_="description__text")
        if desc_el:
            job_desc = desc_el.get_text(separator="\n", strip=True)
        comp_el = soup.find("div", class_="show-more-less-html__markup")
        if comp_el:
            company_desc = comp_el.get_text(separator="\n", strip=True)
    except Exception as exc:
        logger.warning("LinkedIn detail fetch failed for %s: %s", url, exc)
    finally:
        await page.close()
    return job_desc, company_desc


def _parse_card(card_soup, country: str) -> Optional[JobRecord]:
    a = card_soup.find("a", class_="base-card__full-link")
    job_url = (a.get("href") or "").strip() if a else ""
    if job_url:
        job_url = job_url.split("?", 1)[0]

    title = ""
    if a:
        sr = a.find("span", class_="sr-only")
        if sr:
            title = sr.get_text(strip=True)
    if not title:
        h3 = card_soup.find("h3", class_="base-search-card__title")
        if h3:
            title = h3.get_text(strip=True)

    company = ""
    company_url = ""
    sub = card_soup.find("h4", class_="base-search-card__subtitle")
    if sub:
        a_c = sub.find("a")
        if a_c:
            company = a_c.get_text(strip=True)
            company_url = (a_c.get("href") or "").strip().split("?", 1)[0]
        else:
            company = sub.get_text(strip=True)

    loc = card_soup.find("span", class_="job-search-card__location")
    location = loc.get_text(strip=True) if loc else ""

    posted = ""
    t = card_soup.find("time", class_="job-search-card__listdate") or card_soup.find("time")
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


async def scrape_linkedin(
    keyword: str,
    location: str,
    *,
    experience_levels: Optional[List[str]] = None,
    workplace_types: Optional[List[str]] = None,
    date_posted: str = "any",
    max_jobs: int = 50,
    fetch_details: bool = True,
    reporter: Optional[ProgressReporter] = None,
) -> List[JobRecord]:
    """Scrape LinkedIn job search results page (uses saved session if present)."""
    from playwright.async_api import async_playwright

    exp_codes = [LINKEDIN_EXP_MAP[e] for e in (experience_levels or []) if e in LINKEDIN_EXP_MAP]
    wt_codes = [LINKEDIN_WT_MAP[w] for w in (workplace_types or []) if w in LINKEDIN_WT_MAP]
    url = build_linkedin_url(keyword, location, exp_codes, wt_codes, date_posted)
    logger.info("LinkedIn URL: %s", url)

    results: List[JobRecord] = []
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=CHROMIUM_LAUNCH_ARGS)
        try:
            context = await make_stealth_context(browser)
            page = await context.new_page()
            try:
                await asyncio.wait_for(page.goto(url, wait_until="domcontentloaded"), timeout=NAV_TIMEOUT)
            except Exception as exc:
                logger.warning("LinkedIn listing nav failed: %s", exc)
                return results

            await _human_scroll(page, MAX_SCROLL_ATTEMPTS, reporter)
            html = await page.content()
            await page.close()

            soup = BeautifulSoup(html, "lxml")
            cards = soup.find_all("div", class_="base-card")
            logger.info("LinkedIn found %d cards (limit %d)", len(cards), max_jobs)
            cards = cards[:max_jobs]

            for c in cards:
                rec = _parse_card(c, country=location)
                if rec:
                    results.append(rec)

            if reporter:
                reporter.set_total(len(results))

            if fetch_details and results:
                async def fill(rec: JobRecord):
                    if reporter and reporter.cancelled():
                        return
                    async with semaphore:
                        if reporter and reporter.cancelled():
                            return
                        desc, comp_desc = await _fetch_detail(context, rec.url)
                        rec.description = desc
                        rec.raw_text = "\n\n".join(filter(None, [desc, comp_desc]))
                        if not rec.salary:
                            rec.salary = extract_salary(rec.raw_text)
                    if reporter:
                        reporter.step(1, f"LinkedIn: {rec.title[:60]}")

                await asyncio.gather(*(fill(r) for r in results))
            else:
                if reporter:
                    for r in results:
                        reporter.step(1, f"LinkedIn: {r.title[:60]}")
        finally:
            await browser.close()

    return results
