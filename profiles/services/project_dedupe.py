"""Project dedupe — match enriched projects against typed projects.

When a user pulls GitHub/Scholar/Kaggle signals and we enrich them into
project-shaped artifacts, some of those will overlap with projects the user
already typed into their master profile. We don't want to surface "polonium-
isolation" twice in the resume editor — once from the user's typed entry,
once from a Scholar paper with the same title.

This module makes one batched LLM call to decide, per (typed, enriched) pair,
which of four actions applies:

  - "merge": the two represent the same project; the editor should show one
    entry that unions tech stacks and concatenates bullets. The user picks
    which name + URL to keep on the review screen.
  - "keep_existing": same project; the typed version wins (drop enriched).
  - "keep_new": same project; the enriched version wins (replace typed).
  - "add_new": no typed counterpart; surface as a new project the user can
    opt into.

We're aggressive: the LLM uses semantic match (title + URL + bullet content),
not just URL/title equality. The user gets to review the verdicts before
anything is persisted (Phase 2's review UI does that).
"""
from __future__ import annotations

import json
import logging

from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import DedupeBatch, DedupeDecision

logger = logging.getLogger(__name__)


def dedupe_projects(typed_projects: list[dict], enriched_projects: list[dict]) -> list[dict]:
    """Run the dedupe LLM call. Returns a list of DedupeDecision-shaped dicts.

    `typed_projects`: user-authored projects from `UserProfile.data_content['projects']`.
    `enriched_projects`: output from `project_enricher.enrich_profile`.

    Decisions cover EVERY enriched project. If an enriched project has no
    typed match, the decision is `add_new` with `typed_index=-1`. If a typed
    project has no enriched match, no decision is emitted for it (the typed
    project stays untouched by definition).
    """
    if not enriched_projects:
        return []
    if not typed_projects:
        # No comparison possible — every enriched project is automatically new.
        return [
            {
                'enriched_index': i,
                'typed_index': -1,
                'action': 'add_new',
                'confidence': 1.0,
                'reason': 'No typed projects to compare against.',
            }
            for i in range(len(enriched_projects))
        ]

    # Build slim views so we don't blow the token budget on noise.
    typed_view = [_slim_typed(i, p) for i, p in enumerate(typed_projects)]
    enriched_view = [_slim_enriched(i, p) for i, p in enumerate(enriched_projects)]

    prompt = f"""For each ENRICHED project below, decide whether it represents the
same real-world project as one of the TYPED projects, and if so which dedupe
action applies. Emit ONE decision per ENRICHED project — never skip one.

ACTIONS:
  - "merge": same project; we'll keep both signals (union tech, concat bullets,
    prefer the typed name + URL). Use when the typed entry is correct but
    sparse, and the enriched entry adds substantive detail.
  - "keep_existing": same project; the typed version is better, drop enriched.
    Use when the user's bullets are clearly more accurate or specific.
  - "keep_new": same project; the enriched version is better, replace typed.
    Use rarely — only when the typed entry is a clear placeholder (e.g.
    name only, no description) and the enriched entry has substantive content.
  - "add_new": NO typed counterpart; surface enriched as a new project.
    `typed_index` MUST be -1 in this case.

MATCHING RULES (be aggressive but not reckless):
  - Same URL → same project (merge or keep_existing).
  - Title match (case-insensitive, ignoring punctuation) → likely same project.
  - Semantic match (e.g. "Polonium isolation" vs "Discovery of polonium via
    radiochemical separation") → consider same project; check bullets to
    confirm.
  - GitHub repo named after a typed project → same project.
  - Different sources, similar topic, different scope → DIFFERENT projects.
    Default to add_new when in doubt about uniqueness.

CONFIDENCE: 0.9+ for URL/title exact match; 0.7-0.9 for semantic match;
0.5-0.7 for tentative match; below 0.5 means treat as add_new.

REASON: one short sentence citing the specific evidence (URL match, title
match, bullet overlap, etc.). No filler.

TYPED PROJECTS (user-authored):
{json.dumps(typed_view, indent=2)}

ENRICHED PROJECTS (from external signals):
{json.dumps(enriched_view, indent=2)}

Output one decision per enriched project, in order. Use enriched_index =
the project's index in the ENRICHED list above; typed_index = the matched
typed project's index, or -1 for add_new.
"""
    try:
        structured = get_structured_llm(
            DedupeBatch,
            temperature=0.2,
            max_tokens=4096,
            task='project_enricher',  # Same per-task key as enrichment
        )
        result = structured.invoke(prompt)
        decisions = [d.model_dump() for d in result.decisions]
        # Defensive: ensure every enriched project has a decision. If the LLM
        # missed any, emit a conservative add_new.
        seen = {d['enriched_index'] for d in decisions if d.get('enriched_index') is not None}
        for i in range(len(enriched_projects)):
            if i not in seen:
                decisions.append({
                    'enriched_index': i,
                    'typed_index': -1,
                    'action': 'add_new',
                    'confidence': 0.5,
                    'reason': 'No decision emitted by LLM; defaulting to add_new.',
                })
        decisions.sort(key=lambda d: d['enriched_index'])
        logger.info(
            "project_dedupe: %d decisions across %d typed × %d enriched",
            len(decisions), len(typed_projects), len(enriched_projects),
        )
        return decisions
    except Exception:
        logger.exception("project_dedupe: LLM call failed; falling back to URL match")
        return _url_match_fallback(typed_projects, enriched_projects)


def auto_apply_enriched_projects(profile) -> dict:
    """Run enrichment + dedupe + apply with no user overrides; persist.

    Used in the (default) hands-off onboarding path: the user connects
    external accounts and the system silently merges enriched projects
    into the master profile without surfacing a confirm form. The LLM's
    verdict per pair is treated as authoritative — merge / keep_existing
    / keep_new / add_new all apply automatically.

    Idempotent across visits: enrich_profile uses a hash-based cache, so
    re-running with unchanged signals is free; dedupe + apply are pure
    functions over that cached input.

    Returns a small summary dict suitable for surfacing as a status
    banner: counts per action, plus the final project pool size.
    """
    from profiles.services.project_enricher import enrich_profile

    enriched = enrich_profile(profile)
    typed = (profile.data_content or {}).get('projects') or []
    decisions = dedupe_projects(typed, enriched)
    final = apply_decisions(typed, enriched, decisions)

    counts = {'merge': 0, 'keep_existing': 0, 'keep_new': 0, 'add_new': 0}
    for d in decisions:
        action = d.get('action', '')
        if action in counts:
            counts[action] += 1

    data = profile.data_content or {}
    data['projects'] = final
    data['dedupe_decisions'] = decisions
    data['enriched_projects_cache'] = enriched
    profile.data_content = data
    profile.save(update_fields=['data_content', 'updated_at'])

    return {
        'enriched_count': len(enriched),
        'final_count': len(final),
        'merged': counts['merge'],
        'kept_existing': counts['keep_existing'],
        'kept_new': counts['keep_new'],
        'added_new': counts['add_new'],
    }


def apply_decisions(
    typed_projects: list[dict],
    enriched_projects: list[dict],
    decisions: list[dict],
    overrides: dict[int, str] | None = None,
) -> list[dict]:
    """Apply the dedupe decisions to produce the final project pool.

    `overrides`: optional `{enriched_index: action_string}` map letting a
    user override the LLM's verdict via the review UI in Phase 2. Same
    action vocabulary as the LLM.

    Returns the merged project list. Typed projects that had no enriched
    match come first (preserving the user's order); merge / keep_new /
    add_new entries follow.
    """
    overrides = overrides or {}
    final = [dict(p) for p in (typed_projects or [])]
    drop_typed: set[int] = set()
    appended_or_replaced: set[int] = set()

    for d in decisions:
        e_idx = d.get('enriched_index', -1)
        t_idx = d.get('typed_index', -1)
        action = overrides.get(e_idx, d.get('action', 'add_new'))
        if e_idx < 0 or e_idx >= len(enriched_projects):
            continue
        enriched = enriched_projects[e_idx]
        if action == 'add_new':
            final.append(_enriched_to_project(enriched))
            appended_or_replaced.add(e_idx)
        elif action == 'merge':
            if 0 <= t_idx < len(final):
                final[t_idx] = _merge(final[t_idx], enriched)
                appended_or_replaced.add(e_idx)
        elif action == 'keep_existing':
            # Typed wins — drop enriched. Nothing to add.
            appended_or_replaced.add(e_idx)
        elif action == 'keep_new':
            if 0 <= t_idx < len(final):
                drop_typed.add(t_idx)
                final.append(_enriched_to_project(enriched))
                appended_or_replaced.add(e_idx)
        # Unknown action → skip; conservative.

    # Apply drops AFTER all decisions (so indices don't shift mid-iteration).
    final = [p for i, p in enumerate(final) if i not in drop_typed]
    return final


# --- Helpers -----------------------------------------------------------------

def _slim_typed(idx: int, p: dict) -> dict:
    """Project view sent to the LLM. Drops noise like internal ids, keeps
    name / url / description / technologies — the fields the LLM actually
    needs to judge identity."""
    desc = p.get('description') or []
    if isinstance(desc, str):
        desc = [line.strip() for line in desc.split('\n') if line.strip()]
    return {
        'index': idx,
        'name': p.get('name', ''),
        'url': p.get('url', ''),
        'description': desc[:3],  # 3 bullets is enough to judge identity
        'technologies': (p.get('technologies') or [])[:8],
    }


def _slim_enriched(idx: int, p: dict) -> dict:
    return {
        'index': idx,
        'name': p.get('name', ''),
        'source': p.get('source', ''),
        'source_url': p.get('source_url', ''),
        'summary': p.get('summary', ''),
        'tech_stack': (p.get('tech_stack') or [])[:8],
        'bullets': (p.get('bullets') or [])[:3],
    }


def _enriched_to_project(enriched: dict) -> dict:
    """Project the enriched shape into the resume's `projects[]` shape so the
    rest of the pipeline (resume_generator, edit page) can consume it without
    a special path."""
    return {
        'name': enriched.get('name', ''),
        'description': list(enriched.get('bullets') or []),
        'url': enriched.get('source_url', ''),
        'technologies': list(enriched.get('tech_stack') or []),
        # Carry source provenance so Phase 3 entity-grounding can verify
        # external-source claims separately from CV-grounded claims.
        'source': enriched.get('source', ''),
        'source_id': enriched.get('source_id', ''),
    }


def _merge(typed: dict, enriched: dict) -> dict:
    """Union of a typed project and an enriched one. Typed name + URL win;
    tech_stack is unioned (case-insensitive); bullets are concatenated, dedup'd
    by lowercase prefix to avoid two near-duplicates."""
    merged = dict(typed)
    # Name + URL: prefer typed if present, else enriched.
    if not merged.get('name'):
        merged['name'] = enriched.get('name', '')
    if not merged.get('url'):
        merged['url'] = enriched.get('source_url', '') or enriched.get('url', '')
    # Technologies: union (case-insensitive).
    typed_tech = list(merged.get('technologies') or [])
    enriched_tech = list(enriched.get('tech_stack') or [])
    seen_lower = {t.lower() for t in typed_tech}
    for t in enriched_tech:
        if t.lower() not in seen_lower:
            typed_tech.append(t)
            seen_lower.add(t.lower())
    merged['technologies'] = typed_tech
    # Bullets: concat, dedup by lowercase first 50 chars (catches near-dup
    # phrasings without needing a real similarity metric).
    typed_desc = list(merged.get('description') or [])
    if isinstance(typed_desc, str):
        typed_desc = [line.strip() for line in typed_desc.split('\n') if line.strip()]
    enriched_bullets = list(enriched.get('bullets') or [])
    seen_prefixes = {b.lower()[:50] for b in typed_desc}
    for b in enriched_bullets:
        if b.lower()[:50] not in seen_prefixes:
            typed_desc.append(b)
            seen_prefixes.add(b.lower()[:50])
    merged['description'] = typed_desc
    # Keep source provenance only if typed has none.
    if not merged.get('source'):
        merged['source'] = enriched.get('source', '')
        merged['source_id'] = enriched.get('source_id', '')
    return merged


def _url_match_fallback(typed_projects: list[dict], enriched_projects: list[dict]) -> list[dict]:
    """Conservative URL-match dedupe used when the LLM is unavailable. Only
    matches on exact-after-normalization URL; everything else is add_new."""
    def _norm(url: str) -> str:
        s = (url or '').strip().lower().rstrip('/')
        for prefix in ('https://', 'http://', 'www.'):
            if s.startswith(prefix):
                s = s[len(prefix):]
        return s

    typed_url_to_idx: dict[str, int] = {}
    for i, p in enumerate(typed_projects):
        u = _norm(p.get('url', ''))
        if u:
            typed_url_to_idx[u] = i

    decisions: list[dict] = []
    for i, e in enumerate(enriched_projects):
        eu = _norm(e.get('source_url', ''))
        if eu and eu in typed_url_to_idx:
            decisions.append({
                'enriched_index': i,
                'typed_index': typed_url_to_idx[eu],
                'action': 'merge',
                'confidence': 1.0,
                'reason': 'Exact URL match (fallback).',
            })
        else:
            decisions.append({
                'enriched_index': i,
                'typed_index': -1,
                'action': 'add_new',
                'confidence': 0.5,
                'reason': 'No URL match (fallback).',
            })
    return decisions
