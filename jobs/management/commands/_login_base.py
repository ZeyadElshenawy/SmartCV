"""Shared interactive-login flow used by login_linkedin / login_indeed / login_glassdoor.

Strategy: launch a *persistent* browser context backed by a real user-data
directory, preferably using the system-installed Chrome (channel='chrome')
rather than Playwright's bundled Chromium. Glassdoor / Cloudflare aggressively
fingerprint the bundled Chromium and silently swallow form submits even when
visible — using real Chrome with a persistent profile defeats this. The
session is then saved as a Playwright storage_state JSON for the scrapers.
"""

import asyncio
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

from jobs.services.job_sources.auth import STATE_DIR, state_path
from jobs.services.job_sources.base import (
    DEFAULT_HEADERS,
    DEFAULT_USER_AGENT,
    STEALTH_INIT_SCRIPT,
)


# Args that help us look like a real browser. We deliberately omit
# --no-sandbox (a known automation tell) and --disable-dev-shm-usage.
PERSISTENT_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--start-maximized",
    "--no-default-browser-check",
    "--no-first-run",
]

PERSISTENT_IGNORE_ARGS = [
    "--enable-automation",          # removes the "automated software" infobar
    "--use-mock-keychain",
]


def _userdata_dir(source: str) -> Path:
    p = STATE_DIR() / f"{source}_userdata"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def run_login(
    source: str,
    login_url: str,
    success_url_substring: str,
    post_login_check: str = "",
    fresh_profile: bool = False,
    allow_bundled: bool = False,
):
    from playwright.async_api import async_playwright
    import shutil

    out = state_path(source)
    udd = _userdata_dir(source)

    if fresh_profile and udd.exists():
        print(f"  --fresh: wiping persistent profile at {udd}")
        try:
            shutil.rmtree(udd)
        except Exception as exc:
            print(f"  Failed to wipe profile ({exc}); continuing with existing profile.")
        udd = _userdata_dir(source)

    print("\n  Opening a real Chrome window with a persistent profile.")
    print(f"  Profile dir : {udd}")
    print(f"  Will save session to: {out}")
    print("  >>> Log in normally. The window closes automatically once you're past the login page.\n")

    async with async_playwright() as pw:
        # Try system Chrome first; fall back to bundled Chromium only if
        # explicitly allowed. When using a *real* Chrome/Edge channel, do NOT
        # override user_agent / headers / viewport and do NOT inject the
        # stealth script — those create a fingerprint mismatch that Cloudflare
        # responds to by silently disabling form submits.
        last_err = None
        context = None
        used_real_browser = False
        channels: list[str | None] = ["chrome", "msedge"]
        if allow_bundled:
            channels.append(None)
        for channel in channels:
            try:
                if channel:
                    context = await pw.chromium.launch_persistent_context(
                        user_data_dir=str(udd),
                        headless=False,
                        channel=channel,
                        args=PERSISTENT_LAUNCH_ARGS,
                        ignore_default_args=PERSISTENT_IGNORE_ARGS,
                        locale="en-US",
                        viewport=None,
                        no_viewport=True,
                    )
                    used_real_browser = True
                    print(f"  Using system {channel} (no UA/header overrides)")
                else:
                    context = await pw.chromium.launch_persistent_context(
                        user_data_dir=str(udd),
                        headless=False,
                        channel=None,
                        args=PERSISTENT_LAUNCH_ARGS,
                        ignore_default_args=PERSISTENT_IGNORE_ARGS,
                        user_agent=DEFAULT_USER_AGENT,
                        locale="en-US",
                        viewport=None,
                        no_viewport=True,
                        extra_http_headers=DEFAULT_HEADERS,
                    )
                    used_real_browser = False
                    print("  Using bundled Chromium (system Chrome/Edge not found) — Cloudflare will likely still block")
                break
            except Exception as exc:
                last_err = exc
                continue
        if context is None:
            print(f"  Could not launch system Chrome or Edge. Last error: {last_err}")
            print("  Install Google Chrome (https://www.google.com/chrome/) and retry,")
            print("  or pass --allow-bundled to fall back to bundled Chromium (likely blocked).")
            return False

        try:
            if not used_real_browser:
                await context.add_init_script(STEALTH_INIT_SCRIPT)

            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(login_url)

            print("  Waiting for login to complete (10 min max)...")
            for tick in range(60 * 10):
                try:
                    cur = page.url
                    in_login = success_url_substring in cur
                    if not in_login:
                        if post_login_check:
                            try:
                                if await page.query_selector(post_login_check):
                                    break
                            except Exception:
                                pass
                        else:
                            break
                except Exception:
                    pass
                if tick and tick % 30 == 0:
                    print(f"  ... still waiting (current URL: {page.url})")
                await asyncio.sleep(1)
            else:
                print("  Timed out after 10 minutes. Aborting (no session saved).")
                return False

            await asyncio.sleep(3)
            await context.storage_state(path=str(out))
            size = out.stat().st_size if out.exists() else 0
            print(f"\n  Saved session to {out}  ({size:,} bytes)")
            print("  Future scrapes will reuse this session automatically.\n")
            return True
        finally:
            try:
                await context.close()
            except Exception:
                pass


class InteractiveLoginCommand(BaseCommand):
    """Subclass + set source / login_url / success_url_substring."""

    source: str = ""
    login_url: str = ""
    success_url_substring: str = "/login"
    post_login_check: str = ""

    def add_arguments(self, parser):
        parser.add_argument(
            "--fresh",
            action="store_true",
            help=(
                "Wipe the persistent profile before launching. Use when "
                "Cloudflare/the target site has fingerprinted the existing profile."
            ),
        )
        parser.add_argument(
            "--allow-bundled",
            action="store_true",
            help=(
                "Allow falling back to Playwright's bundled Chromium if system "
                "Chrome/Edge isn't found. Off by default — bundled Chromium is "
                "reliably blocked by Cloudflare/Glassdoor."
            ),
        )

    def handle(self, *args, **options):
        if sys.platform == "win32":
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                pass
        ok = asyncio.run(run_login(
            self.source,
            self.login_url,
            self.success_url_substring,
            self.post_login_check,
            fresh_profile=options.get("fresh", False),
            allow_bundled=options.get("allow_bundled", False),
        ))
        if not ok:
            sys.exit(1)
