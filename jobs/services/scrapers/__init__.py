"""
Job scraper dispatcher.

Each source-specific scraper lives in its own module and exposes a
`scrape(url)` function returning a normalized dict:

    {
        'title': str,
        'company': str,
        'description': str,        # plain text, human-readable
        'raw_html': str,           # optional, for debugging / regeneration
        'source': str,             # 'linkedin' / 'greenhouse' / ...
        'cleaned_url': str,
        # optional enrichments
        'location': str | None,
        'employment_type': str | None,
        'posted_date': str | None,
        'company_url': str | None,
    }

The `scrape_job(url)` dispatcher picks the right scraper by URL host.
Unknown hosts fall through to the generic JSON-LD scraper.
"""
from .dispatcher import scrape_job, SUPPORTED_SOURCES

__all__ = ['scrape_job', 'SUPPORTED_SOURCES']
