"""Unified master-profile rebuild pipeline.

Design: immutable source buckets + computed merge view.

    data_content['projects_typed']     ← CV parser + manual edits ONLY.
    data_content['projects_enriched']  ← Enricher LLM ONLY.
    data_content['projects']           ← Always derived; never hand-edited.

Call ``rebuild_master_profile(profile)`` after ANY mutation:
  - CV upload / form save (projects section)
  - Signal refresh (GitHub, LinkedIn, Scholar, Kaggle)
  - Explicit force-re-merge from the review page

Idempotent: unchanged inputs → identical output, zero extra LLM calls.
The enricher is SHA256-hash-gated; the signal merger is canonical-key dedup.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def rebuild_master_profile(
    profile,
    *,
    force_enrich: bool = False,
    overrides: Optional[dict] = None,
    save: bool = True,
) -> dict:
    """Rebuild the master profile's projects and signal-merged sections.

    Stages
    ------
    1. Enrich  — convert raw ``*_signals`` into ``EnrichedProject`` artifacts.
                 Hash-gated: identical signals → cache hit, zero LLM tokens.
    2. Dedupe  — LLM matches ``projects_enriched`` vs ``projects_typed`` ONLY.
                 Never reads ``projects`` so previously-merged entries can't
                 contaminate the typed baseline and cause duplicates.
    3. Apply   — produce ``data_content['projects']`` as the computed view.
    4. Merge   — append LinkedIn/GitHub entries to skills/experiences/certs/
                 education/volunteer_experience via canonical-key dedup (no LLM).

    Parameters
    ----------
    profile:
        ``UserProfile`` instance. Mutated in place.
    force_enrich:
        Bypass the enrichment cache (e.g. after a model upgrade or explicit
        user-requested re-pull from the review page).
    overrides:
        Optional ``{enriched_index: action}`` map from the review UI, letting
        the user override the LLM's dedupe verdict per enriched project.
        Persisted to ``data_content['project_overrides']`` so re-runs respect
        them without the user having to re-submit.
    save:
        If ``True`` (default), calls ``profile.save()`` once at the end.
        Pass ``False`` when the caller manages the save transaction (e.g.
        ``_refresh_signal`` which needs to save additional URL fields in the
        same ``update_fields`` call).

    Returns
    -------
    dict
        Summary suitable for a status banner::

            {
                'enriched_count': int,
                'final_count':    int,
                'added_new':      int,
                'merged':         int,
                'kept_existing':  int,
                'kept_new':       int,
            }
    """
    from profiles.services.project_enricher import enrich_profile
    from profiles.services.project_dedupe import dedupe_projects, apply_decisions
    from profiles.services.signal_merger import merge_signals_into_profile

    data = profile.data_content or {}

    # Persist any user overrides so they survive future re-runs.
    if overrides is not None:
        data['project_overrides'] = overrides
    stored_overrides = data.get('project_overrides') or {}

    # ── Stage 1: Enrich ──────────────────────────────────────────────────────
    # Reads *_signals → produces list[EnrichedProject dict].
    # The enricher manages its own SHA256 cache; force_enrich bypasses it.
    enriched = enrich_profile(profile, force=force_enrich)
    data['projects_enriched'] = enriched

    # ── Stage 2: Dedupe ──────────────────────────────────────────────────────
    # ALWAYS reads projects_typed (never data_content['projects']) so enriched
    # entries added in a previous run can't re-enter the typed baseline.
    typed = data.get('projects_typed') or []
    decisions = dedupe_projects(typed, enriched)
    data['dedupe_decisions'] = decisions

    # ── Stage 3: Apply ───────────────────────────────────────────────────────
    final = apply_decisions(typed, enriched, decisions, overrides=stored_overrides)
    # Backfill GitHub pushed_at from the cached signal so GitHub projects
    # can be sorted by their actual last-pushed date; without this they'd
    # all be undateable and sink to the bottom.
    from profiles.services.project_sort import (
        backfill_github_dates, sort_projects_newest_first,
    )
    from profiles.services.project_polish import polish_projects
    backfill_github_dates(final, data)
    # Final cleanup pass: strip GitHub-metadata filler ("with N stars on
    # GitHub"), drop AI flourishes, cap at 3 bullets, remove null/empty
    # fields. Idempotent. See profiles/services/project_polish.py.
    final = polish_projects(final)
    final = sort_projects_newest_first(final)
    data['projects'] = final

    # ── Stage 4: Signal merge (non-project sections) ─────────────────────────
    # Must run after we've written the updated data dict above so the merger
    # reads the current state.
    profile.data_content = data
    merge_signals_into_profile(profile)
    data = profile.data_content  # pick up any mutations from the merger

    profile.data_content = data

    if save:
        profile.save(update_fields=['data_content', 'updated_at'])

    counts = {'merge': 0, 'keep_existing': 0, 'keep_new': 0, 'add_new': 0}
    for d in decisions:
        action = d.get('action', '')
        if action in counts:
            counts[action] += 1

    summary = {
        'enriched_count': len(enriched),
        'final_count': len(final),
        'added_new': counts['add_new'],
        'merged': counts['merge'],
        'kept_existing': counts['keep_existing'],
        'kept_new': counts['keep_new'],
    }
    logger.info("rebuild_master_profile: %s", summary)
    return summary
