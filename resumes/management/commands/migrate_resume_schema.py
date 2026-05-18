"""One-time migration: convert GeneratedResume.content rows from
pre-PR-3a shape (highlights + description dual fields) to PR-3a shape
(description canonical, List[str]).

Idempotent: rows already in the new shape are unchanged. Run --dry-run
first to see the count; then run for real.

Usage::

    python manage.py migrate_resume_schema --dry-run
    python manage.py migrate_resume_schema
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from resumes.models import GeneratedResume


def _migrate_item(item: dict) -> bool:
    """Migrate one experience or project entry in place.

    Returns True iff the entry was changed. Idempotent: an entry already
    in canonical shape (description is List[str], no highlights key) is
    left untouched and returns False.
    """
    if not isinstance(item, dict):
        return False

    changed = False

    desc = item.get('description', [])
    if desc is None:
        item['description'] = []
        desc = []
        changed = True
    elif isinstance(desc, str):
        # Empty string → empty list; non-empty string → single-element list.
        item['description'] = [desc] if desc.strip() else []
        desc = item['description']
        changed = True
    elif not isinstance(desc, list):
        item['description'] = []
        desc = []
        changed = True

    if 'highlights' in item:
        highlights = item.pop('highlights')
        changed = True
        if isinstance(highlights, list):
            for h in highlights:
                if isinstance(h, str) and h.strip():
                    desc.append(h)
        elif isinstance(highlights, str) and highlights.strip():
            desc.append(highlights)

    item['description'] = desc
    return changed


def _migrate_content(content: dict) -> tuple[dict, bool]:
    """Migrate one resume content dict. Returns (new_content, changed)."""
    if not isinstance(content, dict):
        return content, False

    overall_changed = False
    for exp in (content.get('experience') or content.get('experiences') or []):
        if _migrate_item(exp):
            overall_changed = True
    for proj in content.get('projects') or []:
        if _migrate_item(proj):
            overall_changed = True

    return content, overall_changed


class Command(BaseCommand):
    help = "Migrate GeneratedResume.content from pre-PR-3a to PR-3a shape"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Report what would change without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        total = 0
        changed = 0

        for resume in GeneratedResume.objects.iterator(chunk_size=100):
            total += 1
            new_content, was_changed = _migrate_content(resume.content or {})
            if was_changed:
                changed += 1
                if not dry_run:
                    resume.content = new_content
                    resume.save(update_fields=['content'])

        action = "would migrate" if dry_run else "migrated"
        self.stdout.write(self.style.SUCCESS(
            f"Scanned {total} resumes; {action} {changed}."
        ))
