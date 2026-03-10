import re
import requests
import logging
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def convert_linkedin_url(url: str) -> str:
    """
    Convert LinkedIn job collection URL to direct job view URL.
    Returns https://www.linkedin.com/jobs/view/{job_id}
    """
    parsed = urlparse(url)

    if '/jobs/view/' in url:
        match = re.search(r'/jobs/view/(\d+)', url)
        if match:
            job_id = match.group(1)
            return f"https://www.linkedin.com/jobs/view/{job_id}"

    query_params = parse_qs(parsed.query)
    if 'currentJobId' in query_params:
        job_id = query_params['currentJobId'][0]
        return f"https://www.linkedin.com/jobs/view/{job_id}"

    job_id_match = re.search(r'(?:currentJobId=|/jobs/view/)(\d+)', url)
    if job_id_match:
        job_id = job_id_match.group(1)
        return f"https://www.linkedin.com/jobs/view/{job_id}"

    return url


def extract_job_id(url: str) -> str | None:
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    if 'currentJobId' in query_params:
        return query_params['currentJobId'][0]

    match = re.search(r'(\d{8,})', url)
    if match:
        return match.group(1)
    return None


def normalize_linkedin_urls(urls: list[str]) -> list[str]:
    return [convert_linkedin_url(url) for url in urls]


def is_linkedin_job_url(url: str) -> bool:
    return 'linkedin.com/jobs' in url and bool(extract_job_id(url))


def html_to_readable_text(html_content) -> str:
    if not html_content:
        return ""
        
    if isinstance(html_content, str):
        soup = BeautifulSoup(html_content, 'html.parser')
    else:
        soup = html_content

    text = soup.get_text(separator='\n', strip=True)

    lines = text.split('\n')
    cleaned_lines: list[str] = []
    empty_count = 0
    for line in lines:
        if line.strip():
            cleaned_lines.append(line)
            empty_count = 0
        else:
            empty_count += 1
            if empty_count <= 1:
                cleaned_lines.append('')

    return '\n'.join(cleaned_lines).strip()


def parse_job_criteria(job_data_html) -> dict:
    if not job_data_html:
        return {}
        
    raw_text = html_to_readable_text(job_data_html)
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    job_info: dict[str, str] = {}
    
    # LinkedIn criteria usually appear in pairs (Header -> Value)
    for i in range(0, len(lines), 2):
        if i + 1 < len(lines):
            key = lines[i]
            value = lines[i + 1]
            job_info[key] = value
            
    return job_info


def scrape_linkedin_job(url: str) -> dict:
    """
    Fetches the URL and extracts job details using the defined logic.
    Mapped to match the application's expected output format.
    """
    # 1. Clean the URL
    cleaned_url = convert_linkedin_url(url)

    # 2. Fetch HTML content
    # Note: LinkedIn often requires User-Agent headers to avoid 429/403 errors
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    try:
        logger.info("Fetching LinkedIn job from: %s", cleaned_url)
        # Reduced timeout to fail faster if stuck
        response = requests.get(cleaned_url, headers=headers, timeout=10)
        response.raise_for_status()
        html_content = response.content
        logger.info("Successfully fetched LinkedIn job page")
    except requests.Timeout:
        raise Exception("LinkedIn request timed out (10s). The page may be slow or unavailable.")
    except requests.RequestException as e:
        raise Exception(f"Failed to fetch LinkedIn URL: {str(e)}")

    # 3. Parse HTML
    soup = BeautifulSoup(html_content, 'html.parser')

    # 4. Extract Elements (based on provided logic)
    about_section = soup.find('div', class_='show-more-less-html__markup')
    job_data_elem = soup.find('ul', class_='description__job-criteria-list')
    title_elem = soup.find('h3', class_='sub-nav-cta__header')
    company_link = soup.find('a', class_='sub-nav-cta__optional-url')

    # 5. Process Data
    about_text = html_to_readable_text(about_section)
    criteria = parse_job_criteria(job_data_elem)
    
    job_title = title_elem.get_text(strip=True) if title_elem else "Unknown Title"
    company_name = company_link.get_text(strip=True) if company_link else "Unknown Company"
    company_url = company_link.get('href') if company_link else None
    
    # Combine extracted text for the full description field
    full_description = about_text
    if criteria:
        full_description += "\n\nJob Criteria:\n"
        for k, v in criteria.items():
            full_description += f"- {k}: {v}\n"

    # 6. Construct Result Dictionary (Mapped to key names used in views.py)
    return {
        'title': job_title,
        'company': company_name,
        'description': full_description, # Main text
        'raw_html': str(about_section) if about_section else "", # Store raw HTML if needed
        'cleaned_url': cleaned_url,
        'job_id': extract_job_id(cleaned_url) or extract_job_id(url),
        'company_url': company_url,
        'criteria': criteria,
    }
