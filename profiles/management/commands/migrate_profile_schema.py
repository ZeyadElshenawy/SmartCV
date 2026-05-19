"""One-time migration: convert UserProfile.data_content rows from
pre-PR-3b shape (description + highlights + achievements + invented
keys) to PR-3b shape (description canonical, list[str]).

Idempotent. Includes ``--scan-unknowns`` to surface LLM-invented keys
NOT in the alias registry — those would fail ``extra="forbid"`` after
the schema change activates, so they must be classified beforehand:

  • add to ``_BULLET_ALIAS_KEYS`` if the key is bullet-content
    semantically (the validator then folds it)
  • promote to a canonical schema field (if it's a real distinct field)
  • silently drop during migration (if it's noise)
  • intentionally accept (per-field config) if downstream readers
    depend on it but it's metadata, not bullets

Usage::

    python manage.py migrate_profile_schema --dry-run --scan-unknowns
    python manage.py migrate_profile_schema --dry-run
    python manage.py migrate_profile_schema
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from profiles.models import UserProfile
from profiles.services.schemas import _BULLET_ALIAS_KEYS


# Canonical fields on the PR-3b Experience model. Used by
# --scan-unknowns to flag anything that isn't canonical AND isn't a
# known alias. ``source`` and ``employment_type`` were promoted from
# extra='allow' during PR 3b after the initial scan surfaced them.
EXPERIENCE_CANONICAL_KEYS = {
    'title', 'company', 'start_date', 'end_date',
    'industry', 'location',
    'source', 'employment_type',  # PR 3b: promoted from extras
    'description',
}

# Canonical fields on the PR-3b Project model. ``source``, ``source_id``,
# ``pushed_at``, ``date`` were promoted from extra='allow' during PR 3b
# after the initial scan surfaced them.
PROJECT_CANONICAL_KEYS = {
    'name', 'role', 'url', 'technologies',
    'source', 'source_id', 'pushed_at', 'date',  # PR 3b: promoted
    'description',
}


def _migrate_item(item: dict, alias_keys: tuple) -> bool:
    """Migrate one experience or project entry in place.

    Mirrors PR 3a's logic applied to profile-side data. Folds all keys
    in ``alias_keys`` into ``description``; coerces ``description`` to
    ``list[str]``; drops the folded keys. Returns True iff the entry
    was changed.

    Does NOT touch unknown (non-alias, non-canonical) keys. The
    --scan-unknowns flag surfaces those separately so the operator can
    decide their fate before running this for real.
    """
    if not isinstance(item, dict):
        return False

    changed = False

    desc = item.get('description', [])
    if isinstance(desc, str):
        item_present = 'description' in item
        new_desc = [desc] if desc.strip() else []
        if new_desc != desc or item_present:
            changed = True
        desc = new_desc
    elif desc is None:
        if 'description' in item:
            changed = True
        desc = []
    elif not isinstance(desc, list):
        desc = []
        changed = True

    for key in alias_keys:
        if key not in item:
            continue
        value = item.pop(key)
        changed = True
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                desc.append(value)
        elif isinstance(value, list):
            for h in value:
                if isinstance(h, str) and h.strip():
                    desc.append(h)
                elif isinstance(h, dict):
                    nested = (
                        h.get('description')
                        or h.get('text')
                        or h.get('content')
                        or h.get('body')
                    )
                    if isinstance(nested, list):
                        desc.extend(
                            s for s in nested
                            if isinstance(s, str) and s.strip()
                        )
                    elif isinstance(nested, str) and nested.strip():
                        desc.append(nested)
        # other value shapes: silent skip

    item['description'] = desc
    return changed


def _migrate_data_content(content: dict) -> tuple[dict, bool]:
    """Migrate experiences + projects within one profile's data_content."""
    if not isinstance(content, dict):
        return content, False

    changed = False
    alias_keys = _BULLET_ALIAS_KEYS

    for section in ('experiences', 'experience'):
        for item in content.get(section, []) or []:
            if _migrate_item(item, alias_keys):
                changed = True

    for proj in content.get('projects', []) or []:
        if _migrate_item(proj, alias_keys):
            changed = True

    return content, changed


def _scan_unknown_keys(content: dict) -> dict[str, int]:
    """Walk profile data, return ``{scoped_key: count}`` of every key
    that isn't a canonical field AND isn't a known alias.

    After the PR-3b schema change, these keys will fail
    ``extra="forbid"`` validation. Classify them BEFORE the schema
    change ships: add to registry / promote to field / drop / accept.
    """
    if not isinstance(content, dict):
        return {}

    alias_set = set(_BULLET_ALIAS_KEYS)
    unknowns: dict[str, int] = {}

    for section in ('experiences', 'experience'):
        for exp in content.get(section, []) or []:
            if not isinstance(exp, dict):
                continue
            for key in exp.keys():
                if (key not in EXPERIENCE_CANONICAL_KEYS
                        and key not in alias_set):
                    scoped = f'experience.{key}'
                    unknowns[scoped] = unknowns.get(scoped, 0) + 1

    for proj in content.get('projects', []) or []:
        if not isinstance(proj, dict):
            continue
        for key in proj.keys():
            if (key not in PROJECT_CANONICAL_KEYS
                    and key not in alias_set):
                scoped = f'project.{key}'
                unknowns[scoped] = unknowns.get(scoped, 0) + 1

    return unknowns


class Command(BaseCommand):
    help = "Migrate UserProfile.data_content from pre-PR-3b to PR-3b shape"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Report what would change without writing.",
        )
        parser.add_argument(
            '--scan-unknowns', action='store_true',
            help=(
                "Report keys outside canonical fields + alias registry. "
                "Run with --dry-run before the schema change ships so "
                "extra='forbid' inventions can be classified."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        scan = options['scan_unknowns']

        total = 0
        changed = 0
        global_unknowns: dict[str, int] = {}

        for profile in UserProfile.objects.iterator(chunk_size=100):
            total += 1
            content = profile.data_content or {}

            if scan:
                local_unknowns = _scan_unknown_keys(content)
                for key, count in local_unknowns.items():
                    global_unknowns[key] = global_unknowns.get(key, 0) + count

            new_content, was_changed = _migrate_data_content(content)
            if was_changed:
                changed += 1
                if not dry_run:
                    profile.data_content = new_content
                    profile.save(update_fields=['data_content'])

        action = "would migrate" if dry_run else "migrated"
        self.stdout.write(self.style.SUCCESS(
            f"Scanned {total} profiles; {action} {changed}."
        ))

        if scan:
            if global_unknowns:
                self.stdout.write(self.style.WARNING(
                    "\nUnknown keys discovered (would fail extra='forbid'):"
                ))
                for key in sorted(
                    global_unknowns.keys(),
                    key=lambda k: -global_unknowns[k],
                ):
                    count = global_unknowns[key]
                    self.stdout.write(f"  {key}  -> {count} occurrence(s)")
                self.stdout.write(
                    "\nDecide on each: add to _BULLET_ALIAS_KEYS, "
                    "promote to canonical field, drop in migration, "
                    "or accept as a known extra. Re-run after deciding."
                )
            else:
                self.stdout.write(self.style.SUCCESS(
                    "No unknown keys — every key is canonical or in the "
                    "alias registry. Safe to apply the schema change."
                ))
