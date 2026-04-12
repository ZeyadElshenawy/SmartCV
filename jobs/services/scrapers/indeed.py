"""Indeed job scraper.

Indeed aggressively anti-bots plain HTTP requests (Cloudflare challenge
page). We use Playwright (headless Chromium) to get past the challenge
and extract the visible job elements.

Playwright is an optional runtime dependency — if it's not installed
or the Chromium binary isn't present, the scraper raises ScrapeError
with instructions so the user can paste the description manually.
"""
import logging
import re
import urllib.parse
from bs4 import BeautifulSoup
from .base import html_to_text, normalize_result, ScrapeError

logger = logging.getLogger(__name__)


def matches(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    # Accept indeed.com and any country subdomain (eg.indeed.com, uk.indeed.com, …)
    return host == 'indeed.com' or host.endswith('.indeed.com')


def _extract_job_key(url: str) -> str | None:
    """Get the Indeed job key (jk / vjk) from any Indeed URL shape."""
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    jk = qs.get('vjk', [None])[0] or qs.get('jk', [None])[0]
    if jk:
        return jk
    # Fall back to a regex over the raw URL (handles fragments, weird encodings)
    m = re.search(r'(?:jk|vjk)=([a-zA-Z0-9]+)', url)
    return m.group(1) if m else None


def _canonical_url(url: str, jk: str) -> str:
    """
    Build a stable viewjob URL. Preserve the user's country subdomain
    if they pasted a localized Indeed (eg.indeed.com, uk.indeed.com, etc.),
    otherwise default to www.indeed.com.
    """
    host = urllib.parse.urlparse(url).netloc.lower()
    # eg.indeed.com, uk.indeed.com, www.indeed.com, indeed.com
    if host.endswith('indeed.com'):
        base_host = host if host != 'indeed.com' else 'www.indeed.com'
    else:
        base_host = 'www.indeed.com'
    return f"https://{base_host}/viewjob?jk={jk}"


def scrape(url: str) -> dict:
    jk = _extract_job_key(url)
    if not jk:
        raise ScrapeError(
            "Couldn't find the Indeed job key (jk/vjk) in that URL. "
            "Copy the link from Indeed's job page, not from search results."
        )

    target_url = _canonical_url(url, jk)
    logger.info("Indeed scraper launching Playwright for %s", target_url)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ScrapeError(
            "Indeed requires the Playwright library. Install it and its "
            "Chromium binary (pip install playwright && playwright install chromium), "
            "or paste the description manually."
        )

    title = company_name = company_url = location = details = description = None
    raw_html = ''

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                )
                page = context.new_page()
                page.goto(target_url, wait_until='domcontentloaded', timeout=30000)

                # Wait for either the job title OR for the page to settle
                try:
                    page.wait_for_selector('h1', timeout=10000)
                except Exception:
                    # Might be a Cloudflare challenge — check and raise
                    body_text = page.locator('body').first.inner_text(timeout=2000) or ''
                    if 'needs to review' in body_text.lower() or 'cloudflare' in body_text.lower():
                        raise ScrapeError(
                            "Indeed served a Cloudflare challenge we couldn't bypass. "
                            "Try again in a moment or paste the description manually."
                        )
                    raise ScrapeError(
                        "Indeed didn't return a job page in time. "
                        "The posting may have expired — please paste manually."
                    )

                # Title
                try:
                    title = page.locator('h1').first.inner_text(timeout=2000)
                    if title:
                        title = title.strip()
                except Exception:
                    pass

                # Company name
                for sel in (
                    'div[data-company-name="true"]',
                    'div[data-testid="inlineHeader-companyName"]',
                ):
                    try:
                        el = page.locator(sel).first
                        company_name = (el.inner_text(timeout=2000) or '').strip() or company_name
                        if company_name:
                            break
                    except Exception:
                        continue

                # Company URL (link inside the company name block)
                try:
                    link = page.locator(
                        'div[data-company-name="true"] a, '
                        'div[data-testid="inlineHeader-companyName"] a'
                    ).first
                    href = link.get_attribute('href', timeout=2000)
                    if href:
                        company_url = urllib.parse.urljoin(target_url, href)
                except Exception:
                    pass

                # Location
                for sel in (
                    'div[data-testid="job-location"]',
                    'div[data-testid="inlineHeader-companyLocation"]',
                ):
                    try:
                        el = page.locator(sel).first
                        location = (el.inner_text(timeout=2000) or '').strip() or location
                        if location:
                            break
                    except Exception:
                        continue

                # Details blob (salary, job type)
                for sel in ('#salaryInfoAndJobType', '#jobDetailsSection'):
                    try:
                        el = page.locator(sel).first
                        details = (el.inner_text(timeout=2000) or '').strip() or details
                        if details:
                            break
                    except Exception:
                        continue

                # Description
                try:
                    raw_html = page.locator('#jobDescriptionText').first.inner_html(timeout=3000) or ''
                    if raw_html:
                        description = html_to_text(BeautifulSoup(raw_html, 'html.parser'))
                except Exception:
                    pass
            finally:
                browser.close()
    except ScrapeError:
        raise
    except Exception as e:
        logger.exception("Indeed Playwright error: %s", e)
        raise ScrapeError(
            "Indeed scraping failed. The site's anti-bot protection may be "
            "blocking us — please paste the description manually."
        )

    if not description:
        raise ScrapeError(
            "Found the Indeed page but couldn't read the job description. "
            "The posting may have expired — please paste it manually."
        )

    # Merge details into description as a footer so nothing is lost
    full_description = description
    if details:
        full_description += f"\n\nDetails: {details}"

    return normalize_result(
        'indeed',
        title=title,
        company=company_name,
        description=full_description,
        raw_html=raw_html,
        cleaned_url=target_url,
        location=location,
        company_url=company_url,
    )
