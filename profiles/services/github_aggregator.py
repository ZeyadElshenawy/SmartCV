"""GitHub signal aggregator.

Pulls public GitHub data for a profile (top repos, language mix, stars,
recent activity) and returns a serializable snapshot. Used to enrich the
master profile with evidence the CV doesn't capture — e.g., a thin CV but
12 active repos in Python.

Unauthenticated against the public GitHub REST API (60 req/hour per IP).
For a single user clicking "refresh", that's far more than enough.

The fetched snapshot is cached on UserProfile.data_content['github_signals']
by the calling view so we don't re-hit the API on every page view.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, TypedDict

import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
USER_AGENT = "SmartCV/1.0 (github-aggregator)"
DEFAULT_TIMEOUT = 8  # seconds per request — fail fast, not slow
# Optional. When set, raises the per-token rate limit from 60/hr to 5000/hr.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Languages we don't surface as "skills" because they're too generic
# or are formatting/data declarations rather than programming languages.
_LANGUAGE_BLOCKLIST = {"jupyter notebook", "html", "css", "scss", "shell",
                       "dockerfile", "tex", "makefile", "vim script",
                       "batchfile", "powershell"}


class RepoSnapshot(TypedDict):
    name: str
    full_name: str
    description: Optional[str]
    url: str
    stars: int
    forks: int
    language: Optional[str]
    pushed_at: Optional[str]


class GithubSnapshot(TypedDict):
    username: str
    profile_url: str
    name: Optional[str]
    bio: Optional[str]
    public_repos: int
    followers: int
    following: int
    account_created: Optional[str]  # ISO date
    total_stars: int
    top_repos: list[RepoSnapshot]
    language_breakdown: list[tuple[str, int]]  # (language, repo_count) sorted desc
    recent_commit_count: int  # last 90 days, approximate (capped by events API)
    fetched_at: str  # ISO timestamp
    error: Optional[str]


def parse_github_username(value: str) -> Optional[str]:
    """Extract a username from a github URL, @handle, or bare username.

    Returns None if no plausible username can be extracted.

    >>> parse_github_username("https://github.com/octocat")
    'octocat'
    >>> parse_github_username("github.com/octocat/some-repo")
    'octocat'
    >>> parse_github_username("@octocat")
    'octocat'
    >>> parse_github_username("octocat")
    'octocat'
    >>> parse_github_username("https://example.com/octocat")
    >>> parse_github_username("")
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip().lstrip('@').rstrip('/')
    if not s:
        return None
    # If it looks like a URL, only accept github.com hosts
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9-]*)(?:/.*)?$", s, re.IGNORECASE)
    if m:
        return m.group(1)
    if "://" in s or "/" in s:
        # Unknown host — refuse rather than guess
        return None
    # Bare token — must look like a valid GitHub username (alnum + hyphens, no leading hyphen)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", s):
        return s
    return None


def _get(session: requests.Session, path: str, **params) -> Optional[dict | list]:
    """GET a GitHub API endpoint, return JSON or None on any error."""
    try:
        r = session.get(f"{GITHUB_API}{path}", params=params, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("GitHub API request failed: %s %s", path, e)
        return None
    if r.status_code == 404:
        return None
    if r.status_code == 403:
        remaining = r.headers.get('X-RateLimit-Remaining', '?')
        reset = r.headers.get('X-RateLimit-Reset')
        reset_str = (
            datetime.fromtimestamp(int(reset), tz=timezone.utc).isoformat()
            if reset and reset.isdigit() else '?'
        )
        logger.warning(
            "GitHub API 403 (rate limit?): %s — remaining=%s, reset_at=%s",
            path, remaining, reset_str,
        )
        return None
    if not r.ok:
        logger.warning("GitHub API %s for %s: %s", r.status_code, path, r.text[:200])
        return None
    try:
        return r.json()
    except ValueError:
        return None


def fetch_github_snapshot(username_or_url: str, top_n: int = 6) -> GithubSnapshot:
    """Fetch a public GitHub snapshot for the given username or profile URL.

    Returns a snapshot dict. On error, returns a snapshot with `error` set
    and other fields zeroed/empty so callers can still render a meaningful
    "couldn't fetch" state.
    """
    username = parse_github_username(username_or_url)
    now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')
    if not username:
        return GithubSnapshot(
            username="", profile_url="", name=None, bio=None,
            public_repos=0, followers=0, following=0, account_created=None,
            total_stars=0, top_repos=[], language_breakdown=[],
            recent_commit_count=0, fetched_at=now_iso,
            error="Could not parse a GitHub username from that input.",
        )

    session = requests.Session()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    session.headers.update(headers)

    user = _get(session, f"/users/{username}")
    if not user:
        return GithubSnapshot(
            username=username, profile_url=f"https://github.com/{username}",
            name=None, bio=None, public_repos=0, followers=0, following=0,
            account_created=None, total_stars=0, top_repos=[],
            language_breakdown=[], recent_commit_count=0, fetched_at=now_iso,
            error="GitHub user not found or API unreachable.",
        )

    # Public repos, sorted by recent activity. Cap at 100 (single-page).
    repos = _get(session, f"/users/{username}/repos",
                 sort="updated", per_page=100, type="owner") or []

    # Filter forks if you want only original work (uncomment to enable):
    # repos = [r for r in repos if not r.get('fork')]

    total_stars = sum(int(r.get('stargazers_count') or 0) for r in repos)

    # Top N by stars (then by recency as tiebreaker)
    top = sorted(
        repos,
        key=lambda r: (int(r.get('stargazers_count') or 0), r.get('pushed_at') or ''),
        reverse=True,
    )[:top_n]
    top_repos: list[RepoSnapshot] = [
        RepoSnapshot(
            name=r.get('name', ''),
            full_name=r.get('full_name', ''),
            description=r.get('description'),
            url=r.get('html_url', ''),
            stars=int(r.get('stargazers_count') or 0),
            forks=int(r.get('forks_count') or 0),
            language=r.get('language'),
            pushed_at=r.get('pushed_at'),
        )
        for r in top
    ]

    # Language breakdown by repo count (excluding blocklisted ones)
    lang_counts: dict[str, int] = {}
    for r in repos:
        lang = (r.get('language') or '').strip()
        if lang and lang.lower() not in _LANGUAGE_BLOCKLIST:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    language_breakdown = sorted(lang_counts.items(), key=lambda x: -x[1])[:8]

    # Recent activity — public events API gives last ~300 events / 90 days
    events = _get(session, f"/users/{username}/events/public", per_page=100) or []
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    recent_commits = 0
    for ev in events:
        if ev.get('type') != 'PushEvent':
            continue
        created_at = ev.get('created_at')
        if not created_at:
            continue
        try:
            ts = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        except ValueError:
            continue
        if ts >= cutoff:
            payload = ev.get('payload') or {}
            recent_commits += int(payload.get('size') or 0)

    return GithubSnapshot(
        username=username,
        profile_url=f"https://github.com/{username}",
        name=user.get('name'),
        bio=user.get('bio'),
        public_repos=int(user.get('public_repos') or 0),
        followers=int(user.get('followers') or 0),
        following=int(user.get('following') or 0),
        account_created=(user.get('created_at') or '')[:10] or None,
        total_stars=total_stars,
        top_repos=top_repos,
        language_breakdown=language_breakdown,
        recent_commit_count=recent_commits,
        fetched_at=now_iso,
        error=None,
    )
