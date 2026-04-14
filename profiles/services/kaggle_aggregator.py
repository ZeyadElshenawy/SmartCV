"""Kaggle profile aggregator.

Pulls public Kaggle profile signals (tier, competitions, datasets, notebooks,
discussion counts, medal totals) by parsing the Next.js __NEXT_DATA__ JSON
blob embedded in kaggle.com/{username}.

Caveats
- Kaggle is a Cloudflare-fronted React app. Direct HTTP works for public
  profiles in the common case but can be blocked / served a challenge page.
- We treat any non-2xx, missing __NEXT_DATA__, or JSON-parse failure as a
  soft failure (returns snapshot with `error` set, never raises).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional, TypedDict

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 8


class KaggleMedals(TypedDict):
    gold: int
    silver: int
    bronze: int


class KaggleCategory(TypedDict):
    count: int
    tier: Optional[str]
    medals: KaggleMedals


class KaggleSnapshot(TypedDict):
    username: str
    profile_url: str
    display_name: Optional[str]
    overall_tier: Optional[str]
    competitions: KaggleCategory
    datasets: KaggleCategory
    notebooks: KaggleCategory
    discussion: KaggleCategory
    followers: int
    fetched_at: str
    error: Optional[str]


_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,29}$")


def parse_kaggle_username(value: str) -> Optional[str]:
    """Extract a Kaggle username from a URL or bare token.

    >>> parse_kaggle_username("https://www.kaggle.com/octocat")
    'octocat'
    >>> parse_kaggle_username("kaggle.com/octocat/")
    'octocat'
    >>> parse_kaggle_username("octocat")
    'octocat'
    >>> parse_kaggle_username("https://example.com/octocat")
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip().rstrip('/')
    if not s:
        return None

    m = re.match(
        r"^(?:https?://)?(?:www\.)?kaggle\.com/([A-Za-z0-9][A-Za-z0-9_-]{2,29})(?:/.*)?$",
        s, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    if "://" in s or "/" in s:
        return None
    if _USERNAME_RE.match(s):
        return s
    return None


def fetch_kaggle_snapshot(value: str) -> KaggleSnapshot:
    now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')
    username = parse_kaggle_username(value)
    if not username:
        return _empty_snapshot('', '', now_iso,
                               "Could not parse a Kaggle username from that input.")

    profile_url = f"https://www.kaggle.com/{username}"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    try:
        r = requests.get(profile_url, headers=headers, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("Kaggle fetch failed for %s: %s", username, e)
        return _empty_snapshot(username, profile_url, now_iso, "Kaggle request failed.")

    if not r.ok:
        return _empty_snapshot(username, profile_url, now_iso,
                               f"Kaggle returned {r.status_code}.")

    try:
        soup = BeautifulSoup(r.text, "html.parser")
        next_data = soup.select_one("script#__NEXT_DATA__")
        if not next_data or not next_data.string:
            return _empty_snapshot(username, profile_url, now_iso,
                                   "Kaggle page didn't expose __NEXT_DATA__.")
        data = json.loads(next_data.string)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("Kaggle parse failed for %s: %s", username, e)
        return _empty_snapshot(username, profile_url, now_iso,
                               "Couldn't parse the Kaggle page.")

    user = _find_user_object(data) or {}
    if not user:
        return _empty_snapshot(username, profile_url, now_iso,
                               "Kaggle profile data not found in page.")

    return KaggleSnapshot(
        username=username,
        profile_url=profile_url,
        display_name=user.get('displayName') or user.get('userName'),
        overall_tier=_normalize_tier(user.get('performanceTier')),
        competitions=_extract_category(user, 'competition'),
        datasets=_extract_category(user, 'dataset'),
        notebooks=_extract_category(user, 'kernel'),
        discussion=_extract_category(user, 'discussion'),
        followers=int(user.get('followersCount') or user.get('followers') or 0),
        fetched_at=now_iso,
        error=None,
    )


def _find_user_object(data: dict) -> Optional[dict]:
    """Walk the __NEXT_DATA__ tree for the first dict that looks like a Kaggle user.

    Kaggle's page schema changes; rather than hardcoding paths, we scan for any
    dict with both `userName`/`displayName` AND a `performanceTier` field.
    """
    candidates = []

    def walk(node):
        if isinstance(node, dict):
            has_handle = ('userName' in node) or ('displayName' in node)
            if has_handle and ('performanceTier' in node or 'tier' in node):
                candidates.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    # Prefer the richest object (most keys)
    if not candidates:
        return None
    candidates.sort(key=lambda d: -len(d))
    return candidates[0]


def _extract_category(user: dict, prefix: str) -> KaggleCategory:
    """Pull {count, tier, medals} for a Kaggle category given its prefix.

    Field names roughly follow Kaggle's: e.g. competitionsCount,
    competitionsTier, competitionsMedals = {gold, silver, bronze}.
    """
    count = int(user.get(f'{prefix}sCount') or user.get(f'{prefix}Count') or 0)
    tier = _normalize_tier(user.get(f'{prefix}sTier') or user.get(f'{prefix}Tier'))
    medals_obj = user.get(f'{prefix}sMedals') or user.get(f'{prefix}Medals') or {}
    if not isinstance(medals_obj, dict):
        medals_obj = {}
    medals = KaggleMedals(
        gold=int(medals_obj.get('gold') or 0),
        silver=int(medals_obj.get('silver') or 0),
        bronze=int(medals_obj.get('bronze') or 0),
    )
    return KaggleCategory(count=count, tier=tier, medals=medals)


def _normalize_tier(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s == 'novice':
        return 'Novice'
    return s.capitalize()


def _empty_snapshot(username: str, url: str, ts: str, error: str) -> KaggleSnapshot:
    empty_cat = KaggleCategory(count=0, tier=None, medals=KaggleMedals(gold=0, silver=0, bronze=0))
    return KaggleSnapshot(
        username=username, profile_url=url, display_name=None, overall_tier=None,
        competitions=empty_cat, datasets=empty_cat, notebooks=empty_cat, discussion=empty_cat,
        followers=0, fetched_at=ts, error=error,
    )
