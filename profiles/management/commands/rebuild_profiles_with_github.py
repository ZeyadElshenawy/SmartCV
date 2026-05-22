"""Re-run rebuild_master_profile on profiles with GitHub signals to
repair Issue 9's corrupted projects (the URL-match dedupe bug, fixed
by the deterministic pre-match in project_dedupe).

Before the fix, the dedupe LLM could hallucinate/miss URL matches,
producing duplicate-canonical-name project entries and cross-assigned
bullets in data_content['projects']. Re-running rebuild regenerates
'projects' from the intact 'projects_typed' + 'projects_enriched'
buckets using the fixed deterministic dedupe.

--dry-run uses a cheap corruption signature (duplicate canonical
project names) to flag candidates WITHOUT making LLM calls. The real
run executes the full rebuild (which re-runs dedupe; one LLM call per
profile for any URL-distinct pairs, enrichment is cache-gated).

Usage::

    python manage.py rebuild_profiles_with_github --dry-run
    python manage.py rebuild_profiles_with_github
    python manage.py rebuild_profiles_with_github --user-id <uuid>
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from profiles.models import UserProfile
from profiles.services.project_dedupe import _canonical_name


def _has_github_signals(data_content: dict) -> bool:
    gh = (data_content or {}).get('github_signals')
    return bool(gh) and isinstance(gh, dict) and not gh.get('error')


def _duplicate_canonical_names(projects: list) -> list[str]:
    """Return canonical names that appear more than once in projects —
    the cheap corruption signature for Issue 9 (no LLM needed)."""
    seen: dict[str, int] = {}
    for p in projects or []:
        if not isinstance(p, dict):
            continue
        canon = _canonical_name(p.get('name', ''))
        if canon:
            seen[canon] = seen.get(canon, 0) + 1
    return [c for c, n in seen.items() if n > 1]


class Command(BaseCommand):
    help = "Re-run profile rebuild on profiles with GitHub signals (Issue 9 repair)"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--user-id', type=str, default=None,
            help="Rebuild only the specified user's profile.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        user_id = options['user_id']

        qs = UserProfile.objects.all()
        if user_id:
            qs = qs.filter(user_id=user_id)

        total = 0
        gh_profiles = 0
        flagged = 0
        rebuilt = 0

        # Import here so a missing optional dep in rebuild's chain doesn't
        # break --dry-run (which never calls it).
        if not dry_run:
            from profiles.services.profile_rebuilder import rebuild_master_profile

        for profile in qs.iterator(chunk_size=50):
            total += 1
            dc = profile.data_content or {}
            if not _has_github_signals(dc):
                continue
            gh_profiles += 1

            before = dc.get('projects') or []
            dupes = _duplicate_canonical_names(before)

            if dry_run:
                if dupes:
                    flagged += 1
                    self.stdout.write(
                        f"  would repair user={profile.user_id}: "
                        f"{len(before)} projects, duplicate canonical name(s): {dupes}"
                    )
                continue

            # Real run: full rebuild regenerates projects via fixed dedupe.
            rebuild_master_profile(profile, save=True)
            profile.refresh_from_db()
            after = (profile.data_content or {}).get('projects') or []
            after_dupes = _duplicate_canonical_names(after)
            if len(after) != len(before) or dupes != after_dupes:
                rebuilt += 1
                self.stdout.write(
                    f"  rebuilt user={profile.user_id}: "
                    f"{len(before)} -> {len(after)} projects "
                    f"(dupes {dupes or 'none'} -> {after_dupes or 'none'})"
                )

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nScanned {total} profiles ({gh_profiles} with GitHub signals); "
                f"would repair {flagged} with duplicate-canonical-name corruption."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nScanned {total} profiles ({gh_profiles} with GitHub signals); "
                f"rebuilt {rebuilt} with project changes."
            ))
