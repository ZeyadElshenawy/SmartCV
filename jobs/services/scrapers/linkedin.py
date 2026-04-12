"""LinkedIn job scraper.

LinkedIn job pages are mostly server-rendered when hit without a
logged-in session, so we pull the public HTML and parse the same
elements as before. URL normalization converts search/collection URLs
into canonical /jobs/view/{id}/ form first."""
import logging
import re
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from .base import fetch, html_to_text, normalize_result, ScrapeError

logger = logging.getLogger(__name__)


def matches(url: str) -> bool:
    return 'linkedin.com/jobs' in url.lower()


def _convert_url(url: str) -> str:
    """Convert collection/search URLs to canonical /jobs/view/{id}/ form."""
    if '/jobs/view/' in url:
        m = re.search(r'/jobs/view/(\d+)', url)
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    q = parse_qs(urlparse(url).query)
    if 'currentJobId' in q:
        return f"https://www.linkedin.com/jobs/view/{q['currentJobId'][0]}"
    m = re.search(r'(?:currentJobId=|/jobs/view/)(\d+)', url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    return url


def scrape(url: str) -> dict:
    cleaned = _convert_url(url)
    logger.info("LinkedIn scraper fetching %s", cleaned)
    resp = fetch(cleaned)
    soup = BeautifulSoup(resp.content, 'html.parser')

    about_section = soup.find('div', class_='show-more-less-html__markup')
    criteria_list = soup.find('ul', class_='description__job-criteria-list')
    title_elem = soup.find('h3', class_='sub-nav-cta__header')
    company_link = soup.find('a', class_='sub-nav-cta__optional-url')

    about_text = html_to_text(about_section)

    criteria: dict[str, str] = {}
    if criteria_list:
        lines = [l.strip() for l in html_to_text(criteria_list).split('\n') if l.strip()]
        for i in range(0, len(lines), 2):
            if i + 1 < len(lines):
                criteria[lines[i]] = lines[i + 1]

    full_description = about_text
    if criteria:
        full_description += "\n\nJob Criteria:\n" + '\n'.join(
            f"- {k}: {v}" for k, v in criteria.items())

    if not full_description.strip() or not about_section:
        raise ScrapeError(
            "LinkedIn returned a page without job content. This usually means "
            "the posting requires login or has been removed."
        )

    return normalize_result(
        'linkedin',
        title=(title_elem.get_text(strip=True) if title_elem else None),
        company=(company_link.get_text(strip=True) if company_link else None),
        description=full_description,
        raw_html=str(about_section) if about_section else '',
        cleaned_url=cleaned,
        company_url=(company_link.get('href') if company_link else None),
        location=criteria.get('Location'),
        employment_type=criteria.get('Employment type'),
    )
