"""Kaggle profile aggregator.

Pulls public Kaggle profile signals (tier, competitions, datasets, notebooks,
discussion counts, medal totals) by parsing the rendered DOM of the Kaggle profile.

Caveats
- Kaggle is now a Cloudflare-fronted React app that actively blocks plain
  HTTP requests with reCAPTCHA challenges. We use undetected_chromedriver
  (same driver used for the LinkedIn scraper) to render the page in a real
  browser when plain HTTP is blocked.
- We first attempt a plain requests call (fast path). If Cloudflare serves
  a challenge page instead of the real app, we fall through to the browser.
- We extract data from the visual DOM structure using BeautifulSoup, as Kaggle
  no longer embeds a clean `__NEXT_DATA__` JSON blob.
- We treat any unrecoverable failure as a soft error (returns snapshot with
  `error` set, never raises).
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from time import sleep
from typing import Any, Optional, TypedDict

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 10
_UC_PAGE_WAIT = 6   # seconds to let Cloudflare JS challenge resolve in the browser


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

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
    """Extract a Kaggle username from a URL or bare token."""
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


# ---------------------------------------------------------------------------
# Browser-based fetch (undetected_chromedriver)
# ---------------------------------------------------------------------------

try:
    import undetected_chromedriver as uc  # type: ignore
    _HAS_UC = True
except Exception:  # noqa: BLE001
    _HAS_UC = False


def _detect_chrome_major() -> int | None:
    """Major version of the locally installed Chrome on Windows."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return None
    for hive, path in (
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
    ):
        try:
            with winreg.OpenKey(hive, path) as k:
                ver, _ = winreg.QueryValueEx(k, "version")
                return int(str(ver).split(".")[0])
        except OSError:
            continue
        except (TypeError, ValueError):
            return None
    return None


def _fetch_html_via_browser(url: str, wait: float = _UC_PAGE_WAIT) -> str | None:
    """Render *url* in a headless undetected Chrome and return the page HTML.

    Returns None on any driver error so callers can fall back gracefully.
    """
    if not _HAS_UC:
        return None

    driver = None
    try:
        opts = uc.ChromeOptions()
        for arg in [
            "--headless=new",
            "--window-size=1920,1080",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]:
            opts.add_argument(arg)

        kw: dict[str, Any] = {"options": opts, "headless": True, "use_subprocess": True}
        ver = _detect_chrome_major()
        if ver is not None:
            kw["version_main"] = ver

        driver = uc.Chrome(**kw)
        driver.get(url)
        # Allow the Cloudflare JS challenge to run and the real page to hydrate.
        sleep(wait)
        return driver.page_source
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kaggle browser fetch failed: %s", exc)
        return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# HTML → JSON helpers
# ---------------------------------------------------------------------------

def _is_challenge_page(html: str) -> bool:
    """Return True if the response is a Cloudflare / reCAPTCHA challenge."""
    low = html[:3000].lower()
    return (
        "recaptchachallengepageui" in low
        or "challenge-platform" in low
        or "__cf_chl" in low
        or ("just a moment" in low and "cloudflare" in low)
    )


def _parse_kaggle_dom(html: str, username: str, url: str, now_iso: str) -> KaggleSnapshot:
    """Extract stats by parsing the actual DOM structure of the Kaggle profile."""
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. Display name
    title = soup.title.string if soup.title else ''
    display_name = title.split('|')[0].strip() if '|' in title else username
    if display_name.lower() == username.lower():
        display_name = username
        
    # Check if page is empty or 404
    if '404' in title or soup.find(string=re.compile('404 - Page not found', re.IGNORECASE)):
        return _empty_snapshot(username, url, now_iso, "Profile not found (404).")

    # 2. Extract counts from the sidebar/navbar
    def get_count(label: str) -> int:
        sb = soup.find(string=re.compile(fr'^{label} \([0-9,]+\)$'))
        if sb:
            m = re.search(r'\(([\d,]+)\)', sb)
            if m:
                return int(m.group(1).replace(',', ''))
        return 0

    counts = {
        'competitions': get_count('Competitions'),
        'datasets': get_count('Datasets'),
        'notebooks': get_count('Code'),
        'discussion': get_count('Discussion'),
    }
    
    followers = get_count('Followers')
    
    # 3. Initialize structure
    cats: dict[str, KaggleCategory] = {
        'competitions': {'count': counts['competitions'], 'tier': 'Novice', 'medals': {'gold': 0, 'silver': 0, 'bronze': 0}},
        'datasets': {'count': counts['datasets'], 'tier': 'Novice', 'medals': {'gold': 0, 'silver': 0, 'bronze': 0}},
        'notebooks': {'count': counts['notebooks'], 'tier': 'Novice', 'medals': {'gold': 0, 'silver': 0, 'bronze': 0}},
        'discussion': {'count': counts['discussion'], 'tier': 'Novice', 'medals': {'gold': 0, 'silver': 0, 'bronze': 0}},
    }
    
    label_map = {
        'Competitions': 'competitions', 
        'Datasets': 'datasets', 
        'Notebooks': 'notebooks', 
        'Discussions': 'discussion'
    }
    
    # 4. Extract tiers and medals from 'Kaggle Achievements' block
    achievements = soup.find(string=re.compile('Kaggle Achievements'))
    if achievements and achievements.parent and achievements.parent.parent:
        grid = achievements.parent.parent.find_next_sibling('div')
        if grid:
            for h3 in grid.find_all('h3'):
                divs = h3.find_all('div')
                if len(divs) >= 2:
                    ui_label = divs[0].text.strip()
                    tier = divs[1].text.strip().capitalize()
                    key = label_map.get(ui_label)
                    if key:
                        cats[key]['tier'] = tier
                        
                        # Find medals in the parent card
                        card = h3.parent.parent
                        if card:
                            for img in card.find_all('img'):
                                title_attr = img.get('title', '').lower()
                                if 'medal' in title_attr:
                                    val_node = img.find_next_sibling('span')
                                    val = int(val_node.text.replace(',', '')) if val_node else 0
                                    if 'gold' in title_attr: cats[key]['medals']['gold'] = val
                                    elif 'silver' in title_attr: cats[key]['medals']['silver'] = val
                                    elif 'bronze' in title_attr: cats[key]['medals']['bronze'] = val

    # 5. Overall tier: pick highest from categories
    tier_weights = {'Novice': 0, 'Contributor': 1, 'Expert': 2, 'Master': 3, 'Grandmaster': 4}
    best_tier = 'Novice'
    for c in cats.values():
        t = c['tier'] or 'Novice'
        if tier_weights.get(t, 0) > tier_weights.get(best_tier, 0):
            best_tier = t

    return KaggleSnapshot(
        username=username,
        profile_url=url,
        display_name=display_name,
        overall_tier=best_tier,
        competitions=cats['competitions'],
        datasets=cats['datasets'],
        notebooks=cats['notebooks'],
        discussion=cats['discussion'],
        followers=followers,
        fetched_at=now_iso,
        error=None,
    )


def _empty_snapshot(username: str, url: str, ts: str, error: str) -> KaggleSnapshot:
    empty_cat = KaggleCategory(count=0, tier=None, medals=KaggleMedals(gold=0, silver=0, bronze=0))
    return KaggleSnapshot(
        username=username, profile_url=url, display_name=None, overall_tier=None,
        competitions=empty_cat, datasets=empty_cat, notebooks=empty_cat, discussion=empty_cat,
        followers=0, fetched_at=ts, error=error,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_kaggle_snapshot(value: str) -> KaggleSnapshot:
    now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')
    username = parse_kaggle_username(value)
    if not username:
        return _empty_snapshot('', '', now_iso,
                               "Could not parse a Kaggle username from that input.")

    profile_url = f"https://www.kaggle.com/{username}"

    # ------------------------------------------------------------------
    # Strategy 1: plain requests (fast; works if Cloudflare lets it pass)
    # ------------------------------------------------------------------
    html: str | None = None
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        r = requests.get(profile_url, headers=headers, timeout=DEFAULT_TIMEOUT)
        if r.ok and not _is_challenge_page(r.text):
            html = r.text
    except requests.RequestException:
        pass  # Fall through to browser strategy.

    # ------------------------------------------------------------------
    # Strategy 2: real browser via undetected_chromedriver
    # ------------------------------------------------------------------
    if html is None:
        logger.info(
            "Kaggle: plain HTTP returned a challenge or failed; "
            "trying browser for %s", username,
        )
        html = _fetch_html_via_browser(profile_url)
        if html is None:
            return _empty_snapshot(
                username, profile_url, now_iso,
                "Kaggle is blocking plain HTTP and the browser driver failed to start."
            )
        if _is_challenge_page(html):
            return _empty_snapshot(
                username, profile_url, now_iso,
                "Kaggle is showing a bot-verification page even in the browser. "
                "Try again in a moment."
            )

    try:
        return _parse_kaggle_dom(html, username, profile_url, now_iso)
    except Exception as exc:
        logger.warning("Kaggle parse failed for %s: %s", username, exc)
        return _empty_snapshot(username, profile_url, now_iso,
                               "Couldn't parse the Kaggle page structure.")
