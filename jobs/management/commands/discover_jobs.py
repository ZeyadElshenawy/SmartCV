"""Run the job-discovery scan for one or all users with JobPreferences set.

Usage:
    python manage.py discover_jobs --user <user_id_or_email>
    python manage.py discover_jobs --all-users
    python manage.py discover_jobs --all-users --max-users 10

Designed to be invoked from cron / Windows Task Scheduler. Each user runs
sequentially (one ScrapeJob at a time) — the underlying runner spawns a
worker thread, so we wait for each ScrapeJob to terminate before starting
the next user. Without that wait, headless Playwright instances would
contend for the same Chrome user-data dir.
"""

import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from jobs.models import ScrapeJob
from jobs.services.job_sources import runner
from profiles.models import JobPreferences


SCRAPE_TIMEOUT_SECONDS = 60 * 15  # 15 min per user


class Command(BaseCommand):
    help = "Run job-discovery scrapes for one or all users with JobPreferences."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--user", help="Run for a single user (UUID or email).")
        group.add_argument("--all-users", action="store_true", help="Run for every user with JobPreferences.")
        parser.add_argument("--max-users", type=int, default=0, help="Cap on users when --all-users is set.")
        parser.add_argument(
            "--timeout",
            type=int,
            default=SCRAPE_TIMEOUT_SECONDS,
            help="Per-user scrape timeout in seconds (default: 900).",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        users = []

        if options["user"]:
            ident = options["user"]
            user = User.objects.filter(email__iexact=ident).first() or User.objects.filter(id=ident).first()
            if not user:
                raise CommandError(f"User not found: {ident}")
            users = [user]
        else:
            qs = User.objects.filter(job_preferences__isnull=False).order_by("id")
            if options.get("max_users"):
                qs = qs[: options["max_users"]]
            users = list(qs)

        if not users:
            self.stdout.write("No users to scan.")
            return

        timeout = options.get("timeout") or SCRAPE_TIMEOUT_SECONDS

        for user in users:
            prefs = JobPreferences.objects.filter(user=user).first()
            if not prefs or not prefs.keyword or not prefs.locations or not prefs.sources:
                self.stdout.write(f"  skip {user.email}: incomplete preferences")
                continue

            self.stdout.write(f"  scan {user.email} ({prefs.keyword}, {prefs.locations})")
            sj = ScrapeJob.objects.create(
                user=user,
                params_json=prefs.to_params(),
                status=ScrapeJob.STATUS_PENDING,
            )
            runner.start_in_thread(sj.id)

            # Wait for terminal state.
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                sj.refresh_from_db()
                if sj.is_terminal:
                    break
                time.sleep(2)
            else:
                # Timed out — request cancel and move on so one stuck user doesn't block the batch.
                ScrapeJob.objects.filter(id=sj.id).update(cancel_requested=True)
                self.stdout.write(self.style.WARNING(f"  timeout for {user.email} (cancel requested)"))
                continue

            self.stdout.write(f"  -> {sj.status}: {sj.message}")

        self.stdout.write(self.style.SUCCESS("done"))
