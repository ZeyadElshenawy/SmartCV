"""Classify a URL by the platform it belongs to.

The CV parser shoves anything URL-shaped that it doesn't recognise into
`data_content['other_urls']`. That meant a Kaggle profile URL surfaced
on the CV would never make it to the Kaggle signal tile, and connect-
accounts would prompt the user to paste a URL they'd already provided.

This module fixes that:

- `classify_url(url) -> str | None` — host-based detection, returns a
  short platform key ('kaggle', 'scholar', 'linkedin', 'github', ...).
- `extract_known_urls(other_urls)` — split a mixed list into a
  `{platform: url}` map plus the leftovers that didn't match anything.
- `promote_known_urls_into_data(data_content)` — mutate the
  `data_content` dict in place: writes the matched URLs to their
  canonical keys (`kaggle_url`, `scholar_url`, `twitter`, `blog`) when
  those keys are empty, and updates `other_urls` to only the leftovers.

LinkedIn and GitHub live on dedicated `UserProfile` model fields, not in
`data_content`. The caller (typically a view) handles the model-side
write; the classifier just exposes the hits.

We deliberately stay conservative: only declare a URL "known" when the
host strongly identifies the platform. Generic personal-website URLs
get left in `other_urls` because there's no reliable way to tell a
portfolio from a blog from a docs site by URL alone.
"""
from __future__ import annotations

import re
from typing import Optional

# Order matters: the first regex that matches wins.
# Each tuple is `(platform_key, compiled_regex)`. The platform_key is the
# normalized identifier consumers should branch on.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # LinkedIn: profile, company, or legacy /pub/ URLs (no /jobs, /feed, etc).
    ('linkedin', re.compile(
        r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|company|pub)/[^?#]+",
        re.IGNORECASE,
    )),
    # GitHub: user profile or repo URL (we treat any github.com URL as a
    # GitHub link; the aggregator pulls the username out of either shape).
    ('github', re.compile(
        r"^https?://(?:www\.)?github\.com/[^/?#]+",
        re.IGNORECASE,
    )),
    # Kaggle: any kaggle.com URL — most are profile pages.
    ('kaggle', re.compile(
        r"^https?://(?:www\.)?kaggle\.com/[^?#]+",
        re.IGNORECASE,
    )),
    # Google Scholar.
    ('scholar', re.compile(
        r"^https?://scholar\.google\.[a-z.]+/citations\?",
        re.IGNORECASE,
    )),
    # Twitter / X.
    ('twitter', re.compile(
        r"^https?://(?:www\.)?(?:twitter|x)\.com/[^/?#]+",
        re.IGNORECASE,
    )),
    # Blog platforms — Medium (incl. personal subdomains), Hashnode,
    # dev.to, Substack. All collapse to a single 'blog' key downstream.
    ('medium', re.compile(
        r"^https?://(?:[\w-]+\.)?medium\.com/[^?#]*",
        re.IGNORECASE,
    )),
    ('hashnode', re.compile(
        r"^https?://(?:[\w-]+\.hashnode\.dev/|hashnode\.com/@?[^/?#]+)",
        re.IGNORECASE,
    )),
    ('devto', re.compile(
        r"^https?://dev\.to/[^/?#]+",
        re.IGNORECASE,
    )),
    ('substack', re.compile(
        r"^https?://[\w-]+\.substack\.com/?",
        re.IGNORECASE,
    )),
]

# How each detected platform maps onto the master profile's storage keys.
# Use 'model:' prefix for fields that live on the Django model itself
# (linkedin_url, github_url) rather than inside data_content.
_PLATFORM_TO_KEY: dict[str, str] = {
    'linkedin': 'model:linkedin_url',
    'github': 'model:github_url',
    'kaggle': 'kaggle_url',
    'scholar': 'scholar_url',
    'twitter': 'twitter',
    'medium': 'blog',
    'hashnode': 'blog',
    'devto': 'blog',
    'substack': 'blog',
}


def classify_url(url: Optional[str]) -> Optional[str]:
    """Return the platform key for a recognised URL, otherwise `None`."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    for key, pattern in _PATTERNS:
        if pattern.match(url):
            return key
    return None


def extract_known_urls(
    other_urls: Optional[list[str]],
) -> tuple[dict[str, str], list[str]]:
    """Walk a mixed list of URLs once. Returns:

      (`{platform_key: url}` for the FIRST hit per platform,
       list of urls that didn't match any platform)

    Already-classified URLs after the first per-platform stay in the
    leftovers — that way nothing is lost if the user pasted multiple
    Kaggle profiles by mistake.
    """
    promotions: dict[str, str] = {}
    leftovers: list[str] = []
    for raw in other_urls or []:
        url = (raw or '').strip()
        if not url:
            continue
        platform = classify_url(url)
        if not platform or platform in promotions:
            leftovers.append(url)
            continue
        promotions[platform] = url
    return promotions, leftovers


def promote_known_urls_into_data(data_content: Optional[dict]) -> dict[str, str]:
    """In-place promotion of recognised URLs out of `other_urls`.

    Writes to canonical data_content keys when they're currently empty;
    leaves them alone otherwise (the user's typed value wins). Returns a
    `{platform: url}` summary of what was promoted — the caller can use
    it to handle model-side fields (linkedin_url / github_url) since
    those don't live in data_content.

    The returned summary INCLUDES `model:*` entries so callers handling
    the model fields can apply them; the in-place mutation only touches
    data_content keys (no `model:` prefix in stored keys).
    """
    if not isinstance(data_content, dict):
        return {}
    others = data_content.get('other_urls') or []
    if not others:
        return {}
    promotions, leftovers = extract_known_urls(others)
    applied: dict[str, str] = {}
    for platform, url in promotions.items():
        target = _PLATFORM_TO_KEY.get(platform)
        if not target:
            continue
        if target.startswith('model:'):
            # Caller handles the model field. Surface the hit so they can
            # decide whether to write it; leave the URL out of leftovers
            # either way (the user already gave us this link).
            applied[platform] = url
            continue
        if not (data_content.get(target) or '').strip():
            data_content[target] = url
            applied[platform] = url
        else:
            # Canonical key already populated — keep the URL in leftovers
            # so the user's existing value wins but the data isn't lost.
            leftovers.append(url)
    data_content['other_urls'] = leftovers
    return applied
