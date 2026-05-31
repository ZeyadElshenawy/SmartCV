"""Threaded scrape runner.

A `ScrapeJob` row is created (typically by the dashboard's "Scan now"
endpoint), then `start_in_thread(scrape_job_id)` spawns a daemon worker that:

1. Owns its own asyncio loop (Windows ProactorEventLoop required for
   Playwright) and its own DB connection (close_old_connections at start +
   end).
2. Walks the configured sources × locations, calling each async scraper.
3. Persists each `JobRecord` as a `JobListing` row (sha1 dedup per job).
4. Emits progress writes to the `ScrapeJob` row so the front-end can poll.
5. On success, calls `score_listings_for_user` to convert the top-K
   `JobListing`s into `RecommendedJob` rows.

The runner also catches per-source/per-location failures and logs them on
the `ScrapeJob` so partial successes don't blow up the whole scan.
"""

import asyncio
import logging
import os
import sys
import threading
import traceback

# The worker thread runs an asyncio loop and calls the ORM from inside it
# (via progress callbacks). Django's async-safety guard would otherwise raise
# SynchronousOnlyOperation. The worker owns its own connection (calls
# close_old_connections at start + end), so disabling the guard here is safe.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import close_old_connections, transaction
from django.utils import timezone as djtz

from jobs.models import JobListing, ScrapeJob
from .base import JobRecord, ProgressReporter
from .glassdoor import scrape_glassdoor
from .indeed import scrape_indeed
from .linkedin import scrape_linkedin
from .linkedin_selenium import (
    credentials_configured as linkedin_credentials_configured,
    scrape_linkedin_selenium,
)


logger = logging.getLogger("jobs.scraping.runner")


SOURCE_FUNCS = {
    "linkedin": scrape_linkedin,
    "indeed": scrape_indeed,
    "glassdoor": scrape_glassdoor,
}

SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "glassdoor": "Glassdoor",
}


def start_in_thread(scrape_job_id) -> threading.Thread:
    t = threading.Thread(target=run, args=(scrape_job_id,), daemon=True)
    t.start()
    return t


def _refresh_cancel(scrape_job_id) -> bool:
    try:
        return ScrapeJob.objects.filter(id=scrape_job_id, cancel_requested=True).exists()
    except Exception:
        return False


def _save_listing(scrape_job: ScrapeJob, rec: JobRecord):
    h = JobListing.make_hash(rec.source, rec.url, rec.title, rec.company, rec.location)
    JobListing.objects.update_or_create(
        scrape_job=scrape_job,
        unique_hash=h,
        defaults=dict(
            source=rec.source,
            title=rec.title[:512],
            company=rec.company[:512],
            company_url=rec.company_url[:2000],
            location=rec.location[:512],
            country=rec.country[:128],
            posted=rec.posted[:128],
            salary=rec.salary[:255],
            url=rec.url[:2000],
            description=rec.description,
            raw_text=rec.raw_text,
        ),
    )


def _update_progress(
    scrape_job_id, *, completed=None, total=None, current_step=None,
    message=None, status=None, error=None,
):
    fields = {}
    if completed is not None:
        fields["completed_steps"] = completed
    if total is not None:
        fields["total_steps"] = total
    if current_step is not None:
        fields["current_step"] = current_step[:255]
    if message is not None:
        fields["message"] = message[:255]
    if status is not None:
        fields["status"] = status
    if error is not None:
        fields["error"] = error
    job = ScrapeJob.objects.get(id=scrape_job_id)
    for k, v in fields.items():
        setattr(job, k, v)
    if total is None:
        total = job.total_steps
    if total:
        job.progress_pct = min(100, int(round(100 * job.completed_steps / max(total, 1))))
    if status in {ScrapeJob.STATUS_DONE, ScrapeJob.STATUS_ERROR, ScrapeJob.STATUS_CANCELLED}:
        job.finished_at = djtz.now()
        if status == ScrapeJob.STATUS_DONE:
            job.progress_pct = 100
    job.save()


def run(scrape_job_id):
    """Worker thread entry. Owns its own DB connection + asyncio loop."""
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass

    close_old_connections()
    try:
        scrape_job = ScrapeJob.objects.get(id=scrape_job_id)
    except ScrapeJob.DoesNotExist:
        logger.error("ScrapeJob %s not found", scrape_job_id)
        return

    user_id = scrape_job.user_id

    params = scrape_job.params_json or {}
    keyword = params.get("keyword", "")
    locations = params.get("locations") or [""]
    sources = params.get("sources") or []
    date_posted = params.get("date_posted", "any")
    exp_levels = params.get("experience_levels") or []
    workplace_types = params.get("workplace_types") or []
    max_jobs = int(params.get("max_jobs", 30))

    coarse_total = max_jobs * len(sources) * len(locations) if sources and locations else 0
    _update_progress(
        scrape_job_id,
        status=ScrapeJob.STATUS_RUNNING,
        total=coarse_total,
        completed=0,
        current_step="Starting…",
        message="Initializing browser",
    )

    completed_so_far = [0]
    section_total = [coarse_total]
    any_success = False
    any_failure = False

    try:
        for source in sources:
            if _refresh_cancel(scrape_job_id):
                _update_progress(scrape_job_id, status=ScrapeJob.STATUS_CANCELLED, message="Cancelled by user")
                return
            scraper = SOURCE_FUNCS.get(source)
            if not scraper:
                continue

            for loc in locations:
                if _refresh_cancel(scrape_job_id):
                    _update_progress(scrape_job_id, status=ScrapeJob.STATUS_CANCELLED, message="Cancelled by user")
                    return

                _update_progress(
                    scrape_job_id,
                    current_step=f"{SOURCE_LABELS.get(source, source)} · {loc or '(any)'}",
                    message=f"Searching {SOURCE_LABELS.get(source, source)} for '{keyword}' in {loc or 'any'}",
                )

                last_seen = [0]

                def on_step(delta, message):
                    completed_so_far[0] += delta
                    last_seen[0] += delta
                    _update_progress(
                        scrape_job_id,
                        completed=completed_so_far[0],
                        total=section_total[0],
                        message=(message[:255] if message else ""),
                    )

                def on_total(real_total):
                    section_total[0] = section_total[0] - max_jobs + real_total
                    _update_progress(
                        scrape_job_id,
                        total=section_total[0],
                        completed=completed_so_far[0],
                    )

                def on_cancel():
                    return _refresh_cancel(scrape_job_id)

                reporter = ProgressReporter(
                    on_step=on_step, on_total=on_total, on_cancel_check=on_cancel,
                )

                try:
                    if source == "linkedin":
                        # Prefer the credential-based Selenium path when
                        # LINKEDIN_EMAIL / LINKEDIN_PASSWORD are configured —
                        # same auth pattern the profile scraper uses, no
                        # manual CLI-login step required. Falls back to the
                        # Playwright + saved-session path otherwise.
                        if linkedin_credentials_configured():
                            logger.info(
                                "Using Selenium-credential LinkedIn scraper "
                                "(LINKEDIN_EMAIL configured)."
                            )
                            records = scrape_linkedin_selenium(
                                keyword,
                                loc,
                                experience_levels=exp_levels,
                                workplace_types=workplace_types,
                                date_posted=date_posted,
                                max_jobs=max_jobs,
                                reporter=reporter,
                            )
                        else:
                            coro = scraper(
                                keyword,
                                loc,
                                experience_levels=exp_levels,
                                workplace_types=workplace_types,
                                date_posted=date_posted,
                                max_jobs=max_jobs,
                                reporter=reporter,
                            )
                            records = asyncio.run(coro)
                    elif source == "indeed":
                        coro = scraper(
                            keyword,
                            loc,
                            date_posted=date_posted,
                            max_jobs=max_jobs,
                            reporter=reporter,
                        )
                        records = asyncio.run(coro)
                    else:
                        coro = scraper(
                            keyword,
                            loc,
                            max_jobs=max_jobs,
                            reporter=reporter,
                        )
                        records = asyncio.run(coro)
                except Exception as exc:
                    any_failure = True
                    logger.exception("Scraper %s failed for %s", source, loc)
                    _update_progress(
                        scrape_job_id,
                        message=f"{SOURCE_LABELS.get(source, source)} error: {exc}"[:255],
                    )
                    continue

                if records:
                    any_success = True
                    job_obj = ScrapeJob.objects.get(id=scrape_job_id)
                    with transaction.atomic():
                        for rec in records:
                            try:
                                _save_listing(job_obj, rec)
                            except Exception:
                                logger.exception("Failed to save listing")

        if _refresh_cancel(scrape_job_id):
            _update_progress(scrape_job_id, status=ScrapeJob.STATUS_CANCELLED, message="Cancelled by user")
            return

        listings_count = JobListing.objects.filter(scrape_job_id=scrape_job_id).count()

        # Score top-K listings against the user's profile and seed
        # RecommendedJob rows. Done before flipping to STATUS_DONE so the
        # dashboard sees populated recommendations the moment it polls.
        scored_count = 0
        try:
            from jobs.services.job_scoring import score_listings_for_user
            scored_count = score_listings_for_user(user_id, scrape_job_id)
        except Exception:
            logger.exception("Scoring step failed for ScrapeJob %s", scrape_job_id)

        _update_progress(
            scrape_job_id,
            status=ScrapeJob.STATUS_DONE,
            current_step="Finished",
            message=(
                f"Saved {listings_count} listings · {scored_count} recommendations"
                if listings_count else
                "No listings — check that your sources are logged in"
            ),
        )

        # Update JobPreferences scan timestamps.
        try:
            from profiles.models import JobPreferences
            prefs = JobPreferences.objects.filter(user_id=user_id).first()
            if prefs:
                prefs.last_scan_at = djtz.now()
                if not any_success and any_failure:
                    prefs.last_scan_failed_at = djtz.now()
                    prefs.scan_failure_count = (prefs.scan_failure_count or 0) + 1
                elif any_success:
                    prefs.last_scan_failed_at = None
                    prefs.scan_failure_count = 0
                prefs.save(update_fields=["last_scan_at", "last_scan_failed_at", "scan_failure_count"])
        except Exception:
            logger.exception("Failed to update JobPreferences scan state")
    except Exception:
        tb = traceback.format_exc()
        logger.exception("Runner crashed")
        _update_progress(
            scrape_job_id,
            status=ScrapeJob.STATUS_ERROR,
            message="Internal error",
            error=tb,
        )
    finally:
        close_old_connections()
