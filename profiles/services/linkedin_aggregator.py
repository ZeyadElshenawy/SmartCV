"""LinkedIn profile aggregator.

Two modes:

- **Link-only** (default): parse the input into a canonical /in/{handle},
  return a minimal snapshot. The link is still useful — every résumé header
  renders it as a clickable contact line.

- **Scraped** (opt-in via settings.LINKEDIN_SCRAPING_ENABLED): drive a headless
  Chrome through LinkedIn's login + profile flow and return a rich snapshot
  with experience, education, certifications, projects, courses, honors and
  featured items. This is heavy, requires Chrome on the host, and trips
  LinkedIn's ToS — the operator opts in deliberately by setting the env flag
  and the LINKEDIN_EMAIL / LINKEDIN_PASSWORD env vars.

The snapshot shape is the same in both modes, so callers (UI card, project
enricher, has-signal predicate) can reason about it uniformly. In link-only
mode the rich list fields are simply empty.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

logger = logging.getLogger(__name__)


class LinkedinSnapshot(TypedDict, total=False):
    username: str
    profile_url: str
    fetched_at: str
    error: Optional[str]
    # Rich fields populated only when scraping succeeds.
    name: str
    headline: str
    about: str
    experience: list[dict[str, Any]]
    education: list[dict[str, Any]]
    licenses: list[dict[str, Any]]
    projects: list[dict[str, Any]]
    courses: list[dict[str, Any]]
    honors_and_awards: list[str]
    featured: list[dict[str, Any]]
    warnings: list[str]
    scraped: bool


_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_.]{2,99}$")


def parse_linkedin_handle(value: str) -> Optional[str]:
    """Extract a LinkedIn /in/{handle} from a URL, /in/handle, or bare handle.

    >>> parse_linkedin_handle("https://www.linkedin.com/in/jane-doe-123")
    'jane-doe-123'
    >>> parse_linkedin_handle("linkedin.com/in/jane-doe-123/")
    'jane-doe-123'
    >>> parse_linkedin_handle("in/jane-doe-123")
    'jane-doe-123'
    >>> parse_linkedin_handle("jane-doe-123")
    'jane-doe-123'
    >>> parse_linkedin_handle("https://example.com/in/jane")
    >>> parse_linkedin_handle("")
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip().rstrip('/')
    if not s:
        return None

    m = re.match(
        r"^(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9][A-Za-z0-9\-_.]{2,99})(?:/.*)?$",
        s, re.IGNORECASE,
    )
    if m:
        return m.group(1)

    m = re.match(r"^in/([A-Za-z0-9][A-Za-z0-9\-_.]{2,99})$", s, re.IGNORECASE)
    if m:
        return m.group(1)

    if "://" in s or "/" in s:
        return None

    if _HANDLE_RE.match(s):
        return s
    return None


def _link_only_snapshot(handle: str, *, error: Optional[str] = None) -> LinkedinSnapshot:
    """Build the minimal link-only snapshot. Used in disabled mode and as a
    base before adding scraped fields on top."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')
    if not handle:
        return LinkedinSnapshot(
            username='', profile_url='',
            fetched_at=now_iso,
            error=error or "Couldn't parse a LinkedIn handle from that input.",
            scraped=False,
        )
    return LinkedinSnapshot(
        username=handle,
        profile_url=f"https://www.linkedin.com/in/{handle}/",
        fetched_at=now_iso,
        error=error,
        scraped=False,
    )


def _scrape_settings():
    """Read scraping config out of Django settings. Imported lazily so the
    module is safe to import without DJANGO_SETTINGS_MODULE configured (e.g.
    during the doctest for parse_linkedin_handle)."""
    from django.conf import settings
    return {
        'enabled': bool(getattr(settings, 'LINKEDIN_SCRAPING_ENABLED', False)),
        'email': getattr(settings, 'LINKEDIN_EMAIL', '') or '',
        'password': getattr(settings, 'LINKEDIN_PASSWORD', '') or '',
        'headless': bool(getattr(settings, 'LINKEDIN_HEADLESS', True)),
        'use_undetected': bool(getattr(settings, 'LINKEDIN_USE_UNDETECTED', True)),
        'login_wait': float(getattr(settings, 'LINKEDIN_LOGIN_WAIT', 5.0)),
        'page_wait': float(getattr(settings, 'LINKEDIN_PAGE_WAIT', 4.0)),
        'challenge_timeout': float(getattr(settings, 'LINKEDIN_CHALLENGE_TIMEOUT', 300.0)),
        'profiles_dir': getattr(settings, 'LINKEDIN_PROFILES_DIR', None),
    }


def _scraped_snapshot(handle: str) -> LinkedinSnapshot:
    """Drive Selenium through the profile and merge the result with the
    link-only base. On any scraper failure return a snapshot with `error`
    set so the UI can render a useful message — but keep username +
    profile_url so the résumé contact line still works."""
    base = _link_only_snapshot(handle)
    if base.get('error'):
        return base

    cfg = _scrape_settings()
    if not cfg['email'] or not cfg['password']:
        base['error'] = (
            "LinkedIn scraping is enabled but LINKEDIN_EMAIL / LINKEDIN_PASSWORD "
            "are not set in the environment. Stored the link only."
        )
        return base

    try:
        from .linkedin_scraper import (
            LinkedInScraperError,
            scrape_profile,
        )
    except ImportError as exc:
        logger.warning("LinkedIn scraper deps not installed: %s", exc)
        base['error'] = (
            "LinkedIn scraping is enabled but the scraper dependencies "
            "(selenium, lxml, undetected-chromedriver) are not installed. "
            "Stored the link only."
        )
        return base

    try:
        result = scrape_profile(
            profile_url=base['profile_url'],
            email=cfg['email'],
            password=cfg['password'],
            login_wait=cfg['login_wait'],
            page_wait=cfg['page_wait'],
            headless=cfg['headless'],
            profiles_root=cfg['profiles_dir'],
            use_undetected=cfg['use_undetected'],
            challenge_timeout=cfg['challenge_timeout'],
        )
    except LinkedInScraperError as exc:
        logger.info("LinkedIn scrape failed for %s: %s", handle, exc)
        base['error'] = f"LinkedIn scrape failed: {exc}"
        return base
    except Exception as exc:  # noqa: BLE001 — Selenium can blow up in unexpected ways
        logger.exception("Unexpected LinkedIn scrape error for %s", handle)
        base['error'] = f"Unexpected scraper error: {exc}"
        return base

    base.update({
        'name': result.name,
        'headline': result.headline,
        'about': result.about,
        'experience': result.experience,
        'education': result.education,
        'licenses': result.licenses,
        'projects': result.projects,
        'courses': result.courses,
        'honors_and_awards': result.honors_and_awards,
        'featured': result.featured,
        'warnings': result.warnings,
        'scraped': True,
        'error': None,
    })
    return base


def make_linkedin_snapshot(value: str) -> LinkedinSnapshot:
    """Build a stored snapshot from a user-supplied URL or handle.

    Returns a link-only snapshot when scraping is disabled or credentials
    are missing. When scraping is enabled and creds are present, runs the
    full Selenium flow and merges the result on top of the link-only base.
    """
    handle = parse_linkedin_handle(value)
    if not handle:
        return _link_only_snapshot('')

    cfg = _scrape_settings()
    if not cfg['enabled']:
        return _link_only_snapshot(handle)

    return _scraped_snapshot(handle)
