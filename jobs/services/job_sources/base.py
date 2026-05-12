from dataclasses import dataclass, asdict
from typing import Callable, Optional
import re


SALARY_REGEX = re.compile(
    r"(?:[\$£€]|USD|EUR|GBP)\s?\d[\d,]*\.?\d*\s*(k|K|m|M)?"
    r"(?:\s*[-–to]+\s*\$?\d[\d,]*\.?\d*\s*(k|K|m|M)?)?"
    r"(?:\s*/\s*(hr|hour|year|yr|month|mo))?",
    re.IGNORECASE,
)


def extract_salary(text: str) -> str:
    if not text:
        return ""
    m = SALARY_REGEX.search(text)
    return m.group(0).strip() if m else ""


# LinkedIn URL parameter mappings
LINKEDIN_DATE_MAP = {
    "any": "",
    "24h": "r86400",
    "week": "r604800",
    "month": "r2592000",
}

LINKEDIN_EXP_MAP = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid_senior": "4",
    "director": "5",
    "executive": "6",
}

LINKEDIN_WT_MAP = {
    "onsite": "1",
    "remote": "2",
    "hybrid": "3",
}

INDEED_DATE_DAYS = {
    "any": None,
    "24h": 1,
    "week": 7,
    "month": 30,
}


@dataclass
class JobRecord:
    source: str
    title: str = ""
    company: str = ""
    company_url: str = ""
    location: str = ""
    country: str = ""
    posted: str = ""
    salary: str = ""
    url: str = ""
    description: str = ""
    raw_text: str = ""

    def as_dict(self):
        return asdict(self)


@dataclass
class ProgressReporter:
    """Lightweight progress callback wrapper. Runner injects a real callback;
    scrapers call it after each card so progress updates flow to the DB."""
    on_step: Optional[Callable[[int, str], None]] = None
    on_total: Optional[Callable[[int], None]] = None
    on_cancel_check: Optional[Callable[[], bool]] = None

    def step(self, delta: int = 1, message: str = ""):
        if self.on_step:
            self.on_step(delta, message)

    def set_total(self, total: int):
        if self.on_total:
            self.on_total(total)

    def cancelled(self) -> bool:
        return bool(self.on_cancel_check and self.on_cancel_check())


CHROMIUM_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--start-maximized",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1366, "height": 900}

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# Hides the most obvious automation tells. Doesn't defeat Cloudflare/PerimeterX
# but stops the navigator.webdriver === true and missing-plugins giveaways.
STEALTH_INIT_SCRIPT = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
"""


async def make_stealth_context(browser, state_source: str | None = None):
    """Create a Playwright context with stealth tweaks pre-applied.

    If `state_source` is given and a saved storage_state file exists for it
    (saved by `python manage.py login_<source>`), the context is created with
    those cookies/localStorage so the scrape runs as a logged-in user. Falls
    back to anonymous on any load error.
    """
    kwargs = dict(
        user_agent=DEFAULT_USER_AGENT,
        locale="en-US",
        viewport=DEFAULT_VIEWPORT,
        extra_http_headers=DEFAULT_HEADERS,
    )
    if state_source:
        try:
            from .auth import has_saved_state, state_path
            if has_saved_state(state_source):
                kwargs["storage_state"] = str(state_path(state_source))
        except Exception:
            pass
    context = await browser.new_context(**kwargs)
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    return context


def debug_dump(source: str, location: str, html: str) -> str | None:
    """Write the rendered HTML to a debug file so failed scrapes are inspectable."""
    try:
        from django.conf import settings
        import re as _re
        from datetime import datetime
        from pathlib import Path
        root = Path(settings.JOB_SCRAPER_DEBUG_DUMPS_DIR)
        root.mkdir(parents=True, exist_ok=True)
        slug = _re.sub(r"[^a-z0-9]+", "_", (location or "any").lower())[:40]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = root / f"{source}_{slug}_{ts}.html"
        path.write_text(html, encoding="utf-8", errors="ignore")
        return str(path)
    except Exception:
        return None
