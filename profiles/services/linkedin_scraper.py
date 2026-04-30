"""LinkedIn profile scraper.

Originally a port of ``LinkedIn Profile Scraper in Python.ipynb``. LinkedIn
has since shipped a Server-Driven UI redesign where every CSS class is a
random hex hash regenerated per build, and the legacy section IDs
(``id="experience"``, ``id="education"``, ``navigation-index-see-all-…``)
are gone. The notebook's class-based selectors no longer match anything.

The strategy here is therefore different:

* The main profile yields **name**, **headline**, and **about** via stable
  anchors: ``<title>`` for the name, the first ``<p>`` after the name's
  ``<h2>`` for the headline, and ``<h2>About</h2>`` plus its sibling
  ``data-testid="expandable-text-box"`` ``<span>`` for the about text.
* Everything else is fetched from the per-section detail URL — e.g.
  ``<profile_url>/details/experience/`` — where each section sits inside
  a ``data-component-type="LazyColumn"`` whose ``componentkey`` ends with
  a stable suffix like ``ExperienceDetailsSection``. Items are split by
  ``<hr role="presentation">`` boundaries within that column.

These selectors are still LinkedIn-internal, but they sit on framework-level
attributes that change far less often than visual class names.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Suffixes of the LazyColumn ``componentkey`` for each section's detail page.
# The full key is shaped like
# "com.linkedin.sdui.profile.card.ref<profile-urn><Suffix>".
SECTION_LAZYCOL_SUFFIX = {
    "experience": "ExperienceDetailsSection",
    "education": "EducationDetailsSection",
    "licenses": "CertificationDetailsLevel",
    "projects": "ProjectsDetails",
    "courses": "CourseDetailsSection",
    "honors": "HonorsDetails",
}

# Heading text that LinkedIn puts at the top of each section. Used to skip
# the heading paragraph when collecting item text.
SECTION_HEADING_TEXT = {
    "experience": "Experience",
    "education": "Education",
    "licenses": "Licenses & certifications",
    "projects": "Projects",
    "courses": "Courses",
    "honors": "Honors & awards",
}

# Markers that indicate we've walked past the section content into the
# "more profiles for you" / ad bubble that LinkedIn appends to detail pages.
SECTION_END_MARKERS = (
    "Ad Options",
    "Why am I seeing this ad?",
    "More profiles for you",
)


class LinkedInScraperError(RuntimeError):
    """Raised when the scrape fails in a way the caller should surface."""


@dataclass
class ScrapeResult:
    profile_url: str
    name: str = ""
    headline: str = ""
    about: str = ""
    experience: list[dict[str, Any]] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    licenses: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    courses: list[dict[str, Any]] = field(default_factory=list)
    honors_and_awards: list[str] = field(default_factory=list)
    featured: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.profile_url,
            "name": self.name,
            "headline": self.headline,
            "about": self.about,
            "experience": self.experience,
            "education": self.education,
            "licenses": self.licenses,
            "projects": self.projects,
            "courses": self.courses,
            "honors_and_awards": self.honors_and_awards,
            "featured": self.featured,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Selenium plumbing
# ---------------------------------------------------------------------------

try:  # Optional dependency. Falls back to plain selenium below.
    import undetected_chromedriver as uc  # type: ignore
    _HAS_UC = True
except Exception:  # noqa: BLE001
    _HAS_UC = False


def _human_sleep(low: float = 1.5, high: float = 4.0) -> None:
    """Sleep a random amount in ``[low, high]`` to look less robotic."""
    sleep(random.uniform(low, high))


def _profile_dir_for(profiles_root: Path | str | None, email: str) -> Path | None:
    """Resolve the persistent Chrome user-data dir for ``email``. Returns
    ``None`` if no profiles root was configured."""
    if not profiles_root or not email:
        return None
    digest = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:16]
    path = Path(profiles_root) / digest
    path.mkdir(parents=True, exist_ok=True)
    return path


def _common_chrome_args(headless: bool, user_data_dir: Path | None) -> list[str]:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    if user_data_dir is not None:
        args.append(f"--user-data-dir={user_data_dir}")
    if headless:
        args += [
            "--headless=new",
            "--window-size=1920,1080",
            "--no-sandbox",
            "--disable-gpu",
        ]
    else:
        args.append("--start-maximized")
    return args


def _build_driver(
    headless: bool = False,
    user_data_dir: Path | None = None,
    use_undetected: bool = True,
) -> webdriver.Chrome:
    """Build a Chrome driver. Prefers undetected-chromedriver if installed
    and ``use_undetected`` is True; falls back to vanilla selenium."""
    if use_undetected and _HAS_UC:
        opts = uc.ChromeOptions()
        for arg in _common_chrome_args(headless, user_data_dir):
            opts.add_argument(arg)
        # ``use_subprocess=True`` keeps the patched chromedriver alive across
        # ``driver.quit()`` and is required on Windows when reopening drivers
        # back-to-back.
        return uc.Chrome(options=opts, headless=headless, use_subprocess=True)

    options = Options()
    for arg in _common_chrome_args(headless, user_data_dir):
        options.add_argument(arg)
    if headless:
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
    except Exception:  # noqa: BLE001
        pass
    return driver


_CHALLENGE_URL_FRAGMENTS = (
    "/checkpoint",
    "/uas/",
    "/authwall",
    "challenge",
    "verification",
)
_LOGIN_FORM_FRAGMENTS = ("/login", "/uas/login")


def _is_logged_in(driver: webdriver.Chrome) -> bool:
    """Heuristic: we're authenticated if we're on a real profile / feed
    page and not on the login form / a challenge wall."""
    url = driver.current_url or ""
    if any(f in url for f in _LOGIN_FORM_FRAGMENTS):
        return False
    if any(f in url for f in _CHALLENGE_URL_FRAGMENTS):
        return False
    return "linkedin.com" in url


def _is_challenged(driver: webdriver.Chrome) -> bool:
    url = driver.current_url or ""
    return any(f in url for f in _CHALLENGE_URL_FRAGMENTS)


def _submit_login_form(
    driver: webdriver.Chrome, email: str, password: str, login_wait: float,
) -> None:
    """Fill the LinkedIn login form. No-op if we're not on the login page."""
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "username"))
        )
    except TimeoutException:
        # Already past the form, or the page redirected us elsewhere
        # (e.g., directly into a challenge). Caller will figure it out
        # from the resulting URL.
        return

    try:
        driver.find_element(By.ID, "username").send_keys(email)
        pw = driver.find_element(By.ID, "password")
        pw.send_keys(password)
        pw.submit()
    except NoSuchElementException:
        return

    sleep(login_wait)


def _wait_for_user_to_solve(driver: webdriver.Chrome, timeout: float) -> bool:
    """Poll the visible browser until either the user lands on a logged-in
    page or the timeout expires. Returns True iff login completed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _is_logged_in(driver):
                return True
        except WebDriverException:
            # User closed the window manually.
            return False
        sleep(2)
    return False


def _ensure_logged_in(
    profile_url: str,
    email: str,
    password: str,
    *,
    profiles_root: Path | None,
    use_undetected: bool,
    headless: bool,
    login_wait: float,
    page_wait: float,
    challenge_timeout: float,
) -> webdriver.Chrome:
    """Return a Chrome driver that's authenticated and parked on
    ``profile_url``. Three phases:

    1. **Headless w/ saved profile** — if cookies from a prior run still
       work we never see a login form.
    2. **Headless login** — submit the form; if LinkedIn accepts it
       quietly, great.
    3. **Visible challenge** — if we land on /checkpoint, we close the
       headless driver, reopen visibly so the user can solve the
       captcha / 2FA, poll until we're back to a logged-in URL, then
       close and reopen headless using the same persistent profile.
    """
    user_data_dir = _profile_dir_for(profiles_root, email)

    driver = _build_driver(
        headless=headless, user_data_dir=user_data_dir, use_undetected=use_undetected,
    )
    try:
        driver.get(profile_url)
        _human_sleep(page_wait * 0.5, page_wait)
        if _is_logged_in(driver):
            return driver

        # Phase 2: try the login form headlessly.
        driver.get("https://www.linkedin.com/login")
        _submit_login_form(driver, email, password, login_wait)
        if _is_logged_in(driver):
            driver.get(profile_url)
            _human_sleep(page_wait * 0.5, page_wait)
            return driver

        if not _is_challenged(driver) and "/login" in (driver.current_url or ""):
            raise LinkedInScraperError(
                "LinkedIn rejected the email/password combination."
            )

        # Phase 3: visible browser to let the user solve the challenge.
    except Exception:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
        raise

    # Close headless and reopen visibly with the same user-data dir.
    try:
        driver.quit()
    except Exception:  # noqa: BLE001
        pass

    visible = _build_driver(
        headless=False, user_data_dir=user_data_dir, use_undetected=use_undetected,
    )
    try:
        visible.get("https://www.linkedin.com/login")
        # The user-data-dir may already remember the email — submitting
        # the form is still useful in case the prior attempt cleared it.
        _submit_login_form(visible, email, password, login_wait)
        solved = _wait_for_user_to_solve(visible, timeout=challenge_timeout)
    finally:
        try:
            visible.quit()
        except Exception:  # noqa: BLE001
            pass

    if not solved:
        raise LinkedInScraperError(
            "LinkedIn challenge was not solved within "
            f"{int(challenge_timeout)}s. The browser window closed without "
            "completing login. Please try again."
        )

    # Phase 4: cookies are now persisted; reopen headless and continue.
    driver = _build_driver(
        headless=headless, user_data_dir=user_data_dir, use_undetected=use_undetected,
    )
    try:
        driver.get(profile_url)
        _human_sleep(page_wait * 0.5, page_wait)
        if not _is_logged_in(driver):
            raise LinkedInScraperError(
                "Login appeared to succeed but the profile page still "
                "redirected away from a logged-in view."
            )
        return driver
    except Exception:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
        raise


def _click_show_more(driver: webdriver.Chrome) -> None:
    """Try the legacy 'see more' button. New SDUI profile uses
    ``data-testid="expandable-text-box"`` and exposes the full text already,
    but it's still cheap to attempt the legacy click."""
    try:
        driver.find_element(By.CLASS_NAME, "inline-show-more-text__button").click()
        sleep(1)
    except NoSuchElementException:
        pass


def _scroll_to_bottom(driver: webdriver.Chrome, page_wait: float, passes: int = 6) -> None:
    last_height = driver.execute_script("return document.body.scrollHeight")
    for _ in range(passes):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        sleep(page_wait / 2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def _normalize_profile_base(profile_url: str) -> str:
    return profile_url.rstrip("/") + "/"


def _slugify_url(url: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", url).strip("_")
    return slug[:80] or "profile"


# ---------------------------------------------------------------------------
# Pure parsing helpers — operate on already-fetched HTML so they're testable
# against the saved dumps without Selenium.
# ---------------------------------------------------------------------------

def parse_name(soup: BeautifulSoup) -> str:
    """Pull the profile holder's name from ``<title>``."""
    title = soup.find("title")
    if title is None:
        return ""
    text = title.get_text(strip=True)
    # Format: "<Name> | LinkedIn"
    return text.split("|")[0].strip()


def parse_headline(soup: BeautifulSoup, name: str) -> str:
    """The headline is the first non-empty ``<p>`` whose text is not the
    name, sitting after the first ``<h2>`` (which contains the name)."""
    if not name:
        return ""
    h2 = next(
        (h for h in soup.find_all("h2") if h.get_text(strip=True) == name),
        None,
    )
    if h2 is None:
        return ""
    for el in h2.find_all_next(["p", "h2"]):
        if el.name == "h2":
            # We've left the header block.
            break
        text = el.get_text(" ", strip=True)
        if text and text != name:
            return text
    return ""


def parse_about(soup: BeautifulSoup) -> str:
    """Find ``<h2>About</h2>`` and return the text of the first
    ``data-testid="expandable-text-box"`` span that follows."""
    h2 = next(
        (h for h in soup.find_all("h2") if h.get_text(strip=True) == "About"),
        None,
    )
    if h2 is None:
        return ""
    span = h2.find_next(attrs={"data-testid": "expandable-text-box"})
    if span is None:
        return ""
    return span.get_text(" ", strip=True)


def _unwrap_safety_url(url: str) -> str:
    """LinkedIn wraps external links as
    ``https://www.linkedin.com/safety/go/?url=<encoded>&urlhash=…``.
    Pull out the real destination if it's wrapped, otherwise return as-is.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if parsed.netloc.endswith("linkedin.com") and parsed.path.startswith("/safety/go"):
        target = parse_qs(parsed.query).get("url", [""])[0]
        if target:
            return target
    return url


def _find_section_lazycol(soup: BeautifulSoup, section_key: str) -> Tag | None:
    suffix = SECTION_LAZYCOL_SUFFIX[section_key]
    for div in soup.find_all("div", attrs={"data-component-type": "LazyColumn"}):
        ck = div.get("componentkey", "")
        if ck.endswith(suffix):
            return div
    return None


def _truncate_at_end_marker(html: str) -> str:
    """Trim the LazyColumn HTML at the first ad / "more profiles" marker."""
    cut = len(html)
    for marker in SECTION_END_MARKERS:
        idx = html.find(marker)
        if idx != -1 and idx < cut:
            cut = idx
    return html[:cut]


# Match any <hr> tag — BeautifulSoup may emit either
# ``<hr role="presentation" class="…">`` or ``<hr class="…" role="presentation"/>``
# depending on parser settings, and LinkedIn only uses ``<hr>`` as a visual
# separator inside these LazyColumns, so attribute order doesn't matter.
_HR_SPLIT_RE = re.compile(r"<hr\b[^>]*>", re.IGNORECASE)


def _split_items(container_html: str) -> list[BeautifulSoup]:
    """Split a section's HTML into per-item soup fragments using
    ``<hr role="presentation">`` boundaries."""
    parts = _HR_SPLIT_RE.split(container_html)
    return [BeautifulSoup(p, "lxml") for p in parts if p and p.strip()]


def _item_texts(item_soup: BeautifulSoup, drop_first_if: str | None = None) -> list[str]:
    """Collect the visible text of an item, in document order.

    LinkedIn's SDUI tree has a lot of nested ``<p>`` and ``<span>`` wrappers
    that all hold the same string. We keep only the deepest text-bearing
    nodes and dedupe consecutive duplicates.

    ``drop_first_if`` is the section heading text — drop the first collected
    text fragment if it equals that heading. (For the first segment of each
    section the heading sits at the top and would otherwise pollute item 1.)
    """
    texts: list[str] = []
    seen_recent: str | None = None

    candidates: Iterable[Tag] = item_soup.find_all(["p", "span"])
    for el in candidates:
        # Skip ``<span>`` whose text is the same as its enclosing ``<p>`` —
        # avoids triple-counting the same string.
        if el.name == "span":
            parent = el.parent
            if parent is not None and parent.name == "p":
                continue
            # data-testid expandable-text-box gives us description text.
            if not (el.has_attr("data-testid") and el["data-testid"] == "expandable-text-box"):
                # Non-expandable spans are usually icons / a11y labels —
                # skip them unless they hold non-trivial text.
                t = el.get_text(" ", strip=True)
                if not t or len(t) < 3:
                    continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        # Some labels carry trailing icon text like "more"; drop the lone
        # ellipsis-trail "… more" that LinkedIn appends to truncated text.
        if text in {"…", "… more", "more", "see more"}:
            continue
        if text == seen_recent:
            continue
        # Drop UI noise.
        if text in {"Show credential", "Skills:", "Associated with"}:
            continue
        texts.append(text)
        seen_recent = text

    if drop_first_if and texts and texts[0] == drop_first_if:
        texts = texts[1:]
    return texts


def _section_items(soup: BeautifulSoup, section_key: str) -> list[list[str]]:
    """Return text-fragment lists for each item in the section, or [] if
    the section isn't on this page."""
    container = _find_section_lazycol(soup, section_key)
    if container is None:
        return []
    html = _truncate_at_end_marker(str(container))
    fragments = _split_items(html)
    heading = SECTION_HEADING_TEXT[section_key]
    items: list[list[str]] = []
    for i, frag in enumerate(fragments):
        # The first fragment contains the heading + first item; later
        # fragments contain a single item each.
        drop = heading if i == 0 else None
        texts = _item_texts(frag, drop_first_if=drop)
        if texts:
            items.append(texts)
    return items


# ---- Per-section field assembly ------------------------------------------

_DURATION_RE = re.compile(r"^\s*\d+\s+(?:yr|mo)", re.IGNORECASE)
_DATE_RANGE_RE = re.compile(
    r"^\s*(?:"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}"
    r"|\d{4}\s*[–\-]\s*(?:\d{4}|Present)"
    r")",
    re.IGNORECASE,
)


def _looks_like_date_line(text: str) -> bool:
    return bool(_DATE_RANGE_RE.match(text))
_EMPLOYMENT_TYPES = (
    "Full-time", "Part-time", "Self-employed", "Freelance",
    "Contract", "Internship", "Apprenticeship", "Seasonal",
)


def _is_company_summary_line(text: str) -> bool:
    """Detect the second-line "Full-time · 4 yrs 10 mos" / "3 yrs 4 mos"
    summary line used when one company has multiple positions."""
    t = text.strip()
    if " · " in t:
        head, tail = t.split(" · ", 1)
        if head.strip() in _EMPLOYMENT_TYPES and _DURATION_RE.match(tail):
            return True
    return bool(_DURATION_RE.match(t))


def _split_company_employment(line: str) -> tuple[str, str]:
    if " · " in line:
        company, etype = line.split(" · ", 1)
        return company.strip(), etype.strip()
    return line.strip(), ""


def _looks_like_skills(text: str) -> bool:
    t = text.lower()
    if t.startswith("skills:"):
        return True
    return ("skill" in t) and (" and +" in t or t.endswith("skills"))


def _consume_description(texts: list[str], start: int) -> tuple[list[str], str, int]:
    """Eat description and skills lines starting at ``start``, stopping
    before the next role's designation. Returns
    ``(desc_parts, skills, new_index)``.
    """
    desc_parts: list[str] = []
    skills = ""
    i = start
    while i < len(texts):
        if _looks_like_skills(texts[i]):
            skills = texts[i]
            i += 1
            continue
        if i + 1 < len(texts) and _looks_like_date_line(texts[i + 1]):
            break  # texts[i] is the next role's designation
        desc_parts.append(texts[i])
        i += 1
    return desc_parts, skills, i


def _parse_position_blocks(texts: list[str]) -> list[dict[str, str]]:
    """Walk the text fragments of a multi-position company section, returning
    one ``{designation, duration, location, description, skills}`` dict per
    role. Position boundaries are detected by date-range patterns: a line
    looks like a duration if it starts with a Month YYYY or YYYY – YYYY.
    """
    designations: list[dict[str, str]] = []
    i = 0
    while i < len(texts):
        designation = texts[i]
        i += 1

        duration = ""
        if i < len(texts) and _looks_like_date_line(texts[i]):
            duration = texts[i]
            i += 1

        location = ""
        next_is_date = i + 1 < len(texts) and _looks_like_date_line(texts[i + 1])
        if (
            i < len(texts)
            and not _looks_like_date_line(texts[i])
            and not _looks_like_skills(texts[i])
            and not next_is_date  # don't grab the next role's designation
        ):
            location = texts[i]
            i += 1

        desc_parts, skills, i = _consume_description(texts, i)

        designations.append({
            "designation": designation.strip(),
            "duration": duration.strip(),
            "location": location.strip(),
            "description": "\n\n".join(p.strip() for p in desc_parts).strip(),
            "skills": skills.strip(),
        })
    return designations


def parse_experience(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Each item is one company. The new SDUI experience page uses two
    layouts:

    * **Single position at company** — text order is
      ``[designation, "Company · Type", duration, location, description?, skills?]``.
    * **Multiple positions at company** — text order is
      ``[Company, "Type · total tenure"]`` followed by repeating blocks of
      ``[designation, duration, location, description?]``.

    We detect the layout by looking at ``texts[1]``: if it matches a
    "<duration> at" pattern (with or without an employment-type prefix),
    we're in the multi-position layout.
    """
    items = _section_items(soup, "experience")
    out: list[dict[str, Any]] = []
    for texts in items:
        if not texts:
            continue

        if len(texts) >= 2 and _is_company_summary_line(texts[1]):
            # Multi-position company.
            company = texts[0].strip()
            etype, total_tenure = _split_company_employment(texts[1])
            # ``_split_company_employment("4 yrs 10 mos")`` returns
            # ``("4 yrs 10 mos","")``; promote it to total_tenure.
            if etype and not total_tenure:
                total_tenure = etype
                etype = ""
            designations = _parse_position_blocks(texts[2:])
            out.append({
                "company_name": company,
                "employment_type": etype,
                "duration": total_tenure,
                "designations": designations,
            })
        else:
            # Single-position company.
            designation = texts[0].strip()
            company, etype = _split_company_employment(texts[1] if len(texts) > 1 else "")
            duration = texts[2].strip() if len(texts) > 2 else ""
            location = texts[3].strip() if len(texts) > 3 else ""
            description = ""
            skills = ""
            for t in texts[4:]:
                if _looks_like_skills(t):
                    skills = t
                elif not description:
                    description = t
            out.append({
                "company_name": company,
                "employment_type": etype,
                "duration": duration,
                "designations": [{
                    "designation": designation,
                    "duration": duration,
                    "location": location,
                    "description": description.strip(),
                    "skills": skills.strip(),
                }],
            })
    return out


def parse_education(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Each item: [college, degree, duration, description?]."""
    items = _section_items(soup, "education")
    out: list[dict[str, Any]] = []
    for texts in items:
        out.append({
            "college": texts[0].strip() if len(texts) > 0 else "",
            "degree": texts[1].strip() if len(texts) > 1 else "",
            "duration": texts[2].strip() if len(texts) > 2 else "",
            "description": texts[3].strip() if len(texts) > 3 else "",
        })
    return out


def _credential_urls(soup: BeautifulSoup) -> dict[str, str]:
    """Map ``cert name -> credential URL`` for every "Show credential for X"
    anchor in the licenses page. Some certificates have no credential URL —
    those simply don't appear in the map.
    """
    out: dict[str, str] = {}
    for a in soup.find_all("a", attrs={"aria-label": True}):
        label = a["aria-label"]
        prefix = "Show credential for "
        if not label.startswith(prefix):
            continue
        cert_name = label[len(prefix):].strip()
        url = _unwrap_safety_url(a.get("href", ""))
        if cert_name and url:
            out[cert_name] = url
    return out


def parse_licenses(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Each item: [name, institute, "Issued <date>", skills?]."""
    items = _section_items(soup, "licenses")
    cred_urls = _credential_urls(soup)
    out: list[dict[str, Any]] = []
    for texts in items:
        if not texts:
            continue
        name = texts[0].strip()
        institute = texts[1] if len(texts) > 1 else ""
        issued = ""
        skills = ""
        for t in texts[2:]:
            if t.lower().startswith("issued"):
                issued = t.replace("Issued", "", 1).strip()
            elif "skill" in t.lower():
                skills = t
        out.append({
            "name": name,
            "institute": institute.strip(),
            "issued_date": issued.strip(),
            "skills": skills.strip(),
            "credential_url": cred_urls.get(name, ""),
        })
    return out


def parse_projects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Each item: [project_name, duration, description?]."""
    items = _section_items(soup, "projects")
    out: list[dict[str, Any]] = []
    for texts in items:
        out.append({
            "project_name": texts[0].strip() if len(texts) > 0 else "",
            "duration": texts[1].strip() if len(texts) > 1 else "",
            "description": texts[2].strip() if len(texts) > 2 else "",
        })
    return out


def parse_courses(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Each item: [course_name, ("Associated with <school>")?]."""
    items = _section_items(soup, "courses")
    out: list[dict[str, Any]] = []
    for texts in items:
        if not texts:
            continue
        name = texts[0]
        associated = ""
        for t in texts[1:]:
            if t.lower().startswith("associated with"):
                associated = t.replace("Associated with", "", 1).strip()
                break
        out.append({
            "course_name": name.strip(),
            "associated_with": associated,
        })
    return out


_FEATURED_KIND_PREFIXES = (
    "Link", "Document", "Image", "Post", "Video", "Article",
    "Newsletter", "Media",
)


def _split_kind_and_title(text: str) -> tuple[str, str]:
    """The visible link text starts with the kind word, e.g.
    ``"Link Agentic AI - … Udemy LangGraph v1, Ollama, …"``. Pull the
    kind off the front and return the rest as the (verbose) title.
    """
    for kind in _FEATURED_KIND_PREFIXES:
        if text.startswith(kind + " "):
            return kind.lower(), text[len(kind) + 1:].strip()
        if text == kind:
            return kind.lower(), ""
    return "", text


def _featured_anchors_in_region(soup: BeautifulSoup) -> list[Tag]:
    """Walk DOM forward from the Featured h2 until either the Activity h2
    or one of ``SECTION_END_MARKERS`` and collect every ``<a>`` element.
    Falls back to scanning the whole soup if no Featured heading exists
    (e.g., the dedicated ``/details/featured/`` page where the heading
    text is "Featured" inside an ``<h2>`` we can find anyway).
    """
    featured_h2 = next(
        (h for h in soup.find_all("h2") if h.get_text(strip=True) == "Featured"),
        None,
    )
    activity_h2 = next(
        (h for h in soup.find_all("h2") if h.get_text(strip=True) == "Activity"),
        None,
    )
    if featured_h2 is None:
        return list(soup.find_all("a"))

    anchors: list[Tag] = []
    for el in featured_h2.find_all_next():
        if el is activity_h2:
            break
        text = getattr(el, "get_text", lambda *_a, **_k: "")(" ", strip=True)
        if any(marker in text for marker in SECTION_END_MARKERS):
            break
        if el.name == "a":
            anchors.append(el)
    return anchors


def _looks_like_featured_url(href: str) -> bool:
    """Featured items link out via the safety redirector OR straight to
    LinkedIn-hosted media (uploaded documents/images) OR to an external
    domain. We *exclude* internal LinkedIn navigation links."""
    if not href:
        return False
    if "/safety/go/" in href:
        return True
    parsed = urlparse(href)
    if parsed.netloc.endswith("licdn.com"):
        return True  # LinkedIn-hosted document/image
    if parsed.netloc and not parsed.netloc.endswith("linkedin.com"):
        return True  # raw external URL (rare but possible)
    return False


def parse_featured(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Pull the user's featured links, documents, and posts.

    Each Featured card is an ``<a>`` whose visible text starts with the
    kind word (``Link``, ``Document``, ``Image``, ``Post``, …) followed
    by the title and source. The href is either LinkedIn's safety
    redirector wrapping the real URL or a direct LinkedIn-hosted asset
    URL (for uploaded documents and images).

    Works on both the main profile (top ~5) and
    ``/details/featured/`` (full list).
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in _featured_anchors_in_region(soup):
        href = a.get("href", "")
        if not _looks_like_featured_url(href):
            continue
        url = _unwrap_safety_url(href)
        if not url or url in seen:
            continue
        text = a.get_text(" ", strip=True)
        kind, title = _split_kind_and_title(text)
        if not title and not kind:
            continue
        seen.add(url)
        out.append({
            "kind": kind or "link",
            "title": title or text,
            "url": url,
        })
    return out


def parse_honors(soup: BeautifulSoup) -> list[str]:
    """Each item: [honor_name]. Honors have no extra fields on the new
    LinkedIn detail page — just the title."""
    items = _section_items(soup, "honors")
    return [texts[0].strip() for texts in items if texts]


# ---------------------------------------------------------------------------
# Selenium-driven entry points
# ---------------------------------------------------------------------------

DETAIL_PATHS = [
    ("03_details_experience", "details/experience/"),
    ("04_details_education", "details/education/"),
    ("05_details_certifications", "details/certifications/"),
    ("06_details_projects", "details/projects/"),
    ("07_details_courses", "details/courses/"),
    ("08_details_honors", "details/honors/"),
    ("09_details_skills", "details/skills/"),
    ("10_details_featured", "details/featured/"),
]


def dump_raw_pages(
    profile_url: str,
    email: str,
    password: str,
    dump_dir: Path,
    *,
    login_wait: float = 5.0,
    page_wait: float = 4.0,
    headless: bool = False,
    include_details: bool = True,
    profiles_root: Path | None = None,
    use_undetected: bool = True,
    challenge_timeout: float = 300.0,
) -> dict[str, Any]:
    """Log in, save the logged-in profile and each sub-page's HTML to disk."""
    dump_dir = Path(dump_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify_url(profile_url)
    run_dir = dump_dir / f"{timestamp}_{slug}"
    run_dir.mkdir(parents=True, exist_ok=True)

    saved: list[dict[str, str]] = []

    def _save(name: str, html: str) -> None:
        path = run_dir / f"{name}.html"
        path.write_text(html, encoding="utf-8")
        saved.append({"name": name, "path": str(path), "bytes": str(len(html))})

    try:
        driver = _ensure_logged_in(
            profile_url=profile_url,
            email=email,
            password=password,
            profiles_root=profiles_root,
            use_undetected=use_undetected,
            headless=headless,
            login_wait=login_wait,
            page_wait=page_wait,
            challenge_timeout=challenge_timeout,
        )
    except LinkedInScraperError:
        raise
    except WebDriverException as exc:
        raise LinkedInScraperError(f"Selenium error: {exc}") from exc

    try:
        _scroll_to_bottom(driver, page_wait)
        _save("01_profile_initial", driver.page_source)

        _click_show_more(driver)
        _human_sleep(0.5, 1.5)
        _save("02_profile_after_show_more", driver.page_source)

        if include_details:
            base = _normalize_profile_base(profile_url)
            for name, suffix in DETAIL_PATHS:
                target = base + suffix
                try:
                    driver.get(target)
                    _human_sleep(page_wait * 0.5, page_wait)
                    _scroll_to_bottom(driver, page_wait)
                    _save(name, driver.page_source)
                except WebDriverException as exc:
                    saved.append({
                        "name": name, "path": "", "bytes": "0",
                        "note": f"failed to load {target!r}: {exc}",
                    })

    except WebDriverException as exc:
        raise LinkedInScraperError(f"Selenium error: {exc}") from exc
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass

    return {"run_dir": str(run_dir), "files": saved}


def _fetch_soup(driver: webdriver.Chrome, url: str, page_wait: float) -> BeautifulSoup:
    driver.get(url)
    sleep(page_wait)
    _scroll_to_bottom(driver, page_wait)
    return BeautifulSoup(driver.page_source, "lxml")


_DETAIL_SECTIONS = [
    ("experience", "details/experience/", parse_experience, "experience"),
    ("education", "details/education/", parse_education, "education"),
    ("licenses", "details/certifications/", parse_licenses, "licenses"),
    ("projects", "details/projects/", parse_projects, "projects"),
    ("courses", "details/courses/", parse_courses, "courses"),
    ("honors", "details/honors/", parse_honors, "honors_and_awards"),
    ("featured", "details/featured/", parse_featured, "featured"),
]


def _merge_featured(result: ScrapeResult, value: list[dict[str, str]]) -> None:
    """Dedup-merge new featured items into ``result.featured`` by URL."""
    seen = {item["url"] for item in result.featured}
    for item in value:
        if item["url"] not in seen:
            result.featured.append(item)
            seen.add(item["url"])


def _apply_section_result(
    result: ScrapeResult, attr: str, value: Any, label: str,
) -> None:
    if attr == "featured":
        _merge_featured(result, value)
        return
    setattr(result, attr, value)
    if not value:
        result.warnings.append(
            f"No {label} entries parsed (section may be empty or "
            f"the LazyColumn anchor changed)."
        )


def _scrape_main_profile(driver: webdriver.Chrome, page_wait: float) -> BeautifulSoup:
    """``driver`` is already parked on ``profile_url`` (``_ensure_logged_in``
    navigated there). Just flush lazy-loading and click 'see more'."""
    _scroll_to_bottom(driver, page_wait)
    _click_show_more(driver)
    _human_sleep(0.5, 1.5)
    return BeautifulSoup(driver.page_source, "lxml")


def _scrape_detail_sections(
    driver: webdriver.Chrome,
    result: ScrapeResult,
    profile_url: str,
    page_wait: float,
) -> None:
    base = _normalize_profile_base(profile_url)
    for label, path, parser, attr in _DETAIL_SECTIONS:
        try:
            section_soup = _fetch_soup(driver, base + path, page_wait)
            _apply_section_result(result, attr, parser(section_soup), label)
        except WebDriverException as exc:
            result.warnings.append(f"Failed to load {path}: {exc}")


def scrape_profile(
    profile_url: str,
    email: str,
    password: str,
    *,
    login_wait: float = 5.0,
    page_wait: float = 4.0,
    headless: bool = False,
    profiles_root: Path | None = None,
    use_undetected: bool = True,
    challenge_timeout: float = 300.0,
) -> ScrapeResult:
    """Drive Chrome through the new SDUI profile flow.

    Login phases (handled by ``_ensure_logged_in``):

    1. Headless w/ persistent profile dir — cookies skip the form entirely.
    2. Headless login form — submit creds, hope LinkedIn waves us through.
    3. Visible browser — opens iff a checkpoint/challenge is detected.
       The user solves it; we poll until they're logged in, then close
       the visible window and reopen headless using the same cookies.
    """
    if not profile_url.startswith("http"):
        raise LinkedInScraperError("profile_url must be an absolute https:// URL")

    result = ScrapeResult(profile_url=profile_url)

    try:
        driver = _ensure_logged_in(
            profile_url=profile_url,
            email=email,
            password=password,
            profiles_root=profiles_root,
            use_undetected=use_undetected,
            headless=headless,
            login_wait=login_wait,
            page_wait=page_wait,
            challenge_timeout=challenge_timeout,
        )
    except LinkedInScraperError:
        raise
    except WebDriverException as exc:
        raise LinkedInScraperError(f"Selenium error: {exc}") from exc

    try:
        soup = _scrape_main_profile(driver, page_wait)
        result.name = parse_name(soup)
        result.headline = parse_headline(soup, result.name)
        result.about = parse_about(soup)
        result.featured = parse_featured(soup)

        _scrape_detail_sections(driver, result, profile_url, page_wait)

    except WebDriverException as exc:
        raise LinkedInScraperError(f"Selenium error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error during scrape")
        raise LinkedInScraperError(f"Unexpected error: {exc}") from exc
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass

    return result


# ---------------------------------------------------------------------------
# Convenience: parse all sections from a directory of dumped HTML files. Used
# by the dev validation script and unit-style checks.
# ---------------------------------------------------------------------------

DUMP_FILE_TO_SECTION = {
    "03_details_experience.html": ("experience", parse_experience),
    "04_details_education.html": ("education", parse_education),
    "05_details_certifications.html": ("licenses", parse_licenses),
    "06_details_projects.html": ("projects", parse_projects),
    "07_details_courses.html": ("courses", parse_courses),
    "08_details_honors.html": ("honors_and_awards", parse_honors),
    "10_details_featured.html": ("featured", parse_featured),
}


def parse_dump_dir(dump_dir: Path | str, profile_url: str = "") -> ScrapeResult:
    """Parse a previously-saved dump directory into a ``ScrapeResult``."""
    dump_dir = Path(dump_dir)
    result = ScrapeResult(profile_url=profile_url)

    main = dump_dir / "02_profile_after_show_more.html"
    if not main.exists():
        main = dump_dir / "01_profile_initial.html"
    if main.exists():
        soup = BeautifulSoup(main.read_text(encoding="utf-8"), "lxml")
        result.name = parse_name(soup)
        result.headline = parse_headline(soup, result.name)
        result.about = parse_about(soup)
        result.featured = parse_featured(soup)

    for fname, (attr, parser) in DUMP_FILE_TO_SECTION.items():
        path = dump_dir / fname
        if not path.exists():
            continue
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
        value = parser(soup)
        if attr == "featured":
            _merge_featured(result, value)
        else:
            setattr(result, attr, value)
    return result
