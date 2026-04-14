"""LinkedIn link normalizer.

Be honest: LinkedIn does not expose public profile data without auth, and
their ToS prohibits scraping. This module therefore does not try to fetch
"signals" the way the GitHub aggregator does. Its job is to:

  - parse a LinkedIn URL or `in/handle` into a canonical handle
  - return a typed snapshot {username, profile_url, error?} suitable for
    storage in profile.data_content['linkedin_signals']

The UI surfaces the stored link so users have a single place to declare
their professional accounts; no real aggregation happens here.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, TypedDict


class LinkedinSnapshot(TypedDict):
    username: str
    profile_url: str
    fetched_at: str
    error: Optional[str]


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

    # Full URL form
    m = re.match(
        r"^(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9][A-Za-z0-9\-_.]{2,99})(?:/.*)?$",
        s, re.IGNORECASE,
    )
    if m:
        return m.group(1)

    # `in/handle` short form
    m = re.match(r"^in/([A-Za-z0-9][A-Za-z0-9\-_.]{2,99})$", s, re.IGNORECASE)
    if m:
        return m.group(1)

    # Foreign URL — refuse
    if "://" in s or "/" in s:
        return None

    # Bare handle
    if _HANDLE_RE.match(s):
        return s
    return None


def make_linkedin_snapshot(value: str) -> LinkedinSnapshot:
    """Build a stored snapshot from a user-supplied URL or handle.

    No network call. Returns a snapshot with `error` set when the input
    can't be parsed as a LinkedIn handle.
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')
    handle = parse_linkedin_handle(value)
    if not handle:
        return LinkedinSnapshot(
            username='', profile_url='',
            fetched_at=now_iso,
            error="Couldn't parse a LinkedIn handle from that input.",
        )
    return LinkedinSnapshot(
        username=handle,
        profile_url=f"https://www.linkedin.com/in/{handle}/",
        fetched_at=now_iso,
        error=None,
    )
