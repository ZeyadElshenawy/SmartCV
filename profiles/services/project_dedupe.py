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
import re

from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import DedupeBatch, DedupeDecision

logger = logging.getLogger(__name__)


def _recover_decisions_from_failed_generation(exc) -> list[dict] | None:
    """Salvage DedupeBatch decisions from Groq's tool_use_failed body.

    Groq's strict tool validator rejects the LLM response when it wraps the
    output in a bare list  [{"decisions": [...]}]  instead of the flat
    {"decisions": [...]}  our schema declares. The JSON is perfectly valid —
    it's available in error.failed_generation. Parse it directly and return
    a list of decision dicts, or None when the blob isn't usable.
    """
    body = getattr(exc, 'body', None) or {}
    err = body.get('error', {}) if isinstance(body, dict) else {}
    raw = err.get('failed_generation')
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None

    # Unwrap a bare list wrapper: [{"decisions": [...]}] → {"decisions": [...]}
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        parsed = parsed[0]

    # Expect {"decisions": [...]}
    if isinstance(parsed, dict) and isinstance(parsed.get('decisions'), list):
        raw_decisions = parsed['decisions']
    else:
        return None

    out: list[dict] = []
    for item in raw_decisions:
        if not isinstance(item, dict):
            continue
        action = item.get('action', 'add_new')
        if action not in {'merge', 'keep_existing', 'keep_new', 'add_new'}:
            action = 'add_new'
        enriched_index = item.get('enriched_index')
        if enriched_index is None:
            continue
        out.append({
            'enriched_index': int(enriched_index),
            'typed_index': int(item.get('typed_index', -1)),
            'action': action,
            'confidence': float(item.get('confidence', 0.5)),
            'reason': item.get('reason', ''),
        })
    return out if out else None


def _normalize_url(url: str) -> str:
    """Normalize a URL for equality comparison: lowercase, strip
    protocol + www + trailing slash. Shared by the deterministic
    pre-matcher and the URL-match fallback so the two never drift."""
    s = (url or '').strip().lower().rstrip('/')
    for prefix in ('https://', 'http://', 'www.'):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s


def _deterministic_url_matches(
    typed_projects: list[dict],
    enriched_projects: list[dict],
) -> tuple[list[dict], set[int]]:
    """Pre-compute (typed, enriched) pairs that match by normalized URL.

    URL equality is deterministic — delegating it to the LLM was the
    Issue 9 root cause (the LLM hallucinated a URL match and missed a
    real one, cross-assigning bullets and creating a duplicate project).
    This computes the certain matches in code; only URL-distinct pairs
    go to the LLM for semantic adjudication.

    Returns ``(decisions, matched_enriched_indices)``. Each decision is a
    full DedupeDecision-shaped dict with ``action='merge'``. The set is
    the enriched indices that matched (excluded from the LLM call).
    """
    typed_url_to_idx: dict[str, int] = {}
    for t_idx, t in enumerate(typed_projects):
        tu = _normalize_url(t.get('url', ''))
        if tu and tu not in typed_url_to_idx:
            typed_url_to_idx[tu] = t_idx

    decisions: list[dict] = []
    matched_enriched: set[int] = set()
    for e_idx, e in enumerate(enriched_projects):
        eu = _normalize_url(e.get('source_url', '') or e.get('url', ''))
        if eu and eu in typed_url_to_idx:
            decisions.append({
                'enriched_index': e_idx,
                'typed_index': typed_url_to_idx[eu],
                'action': 'merge',
                'confidence': 1.0,
                'reason': 'Deterministic URL match (pre-matched, not LLM-adjudicated).',
            })
            matched_enriched.add(e_idx)
    return decisions, matched_enriched


def dedupe_projects(typed_projects: list[dict], enriched_projects: list[dict]) -> list[dict]:
    """Match enriched projects against typed projects. Returns a list of
    DedupeDecision-shaped dicts, one per enriched project.

    `typed_projects`: user-authored projects from `UserProfile.data_content['projects']`.
    `enriched_projects`: output from `project_enricher.enrich_profile`.

    Issue 9 fix (Path B1): URL-equal pairs are matched DETERMINISTICALLY
    before the LLM call — only URL-distinct pairs are sent to the LLM for
    semantic adjudication. URL matching is deterministic; the LLM
    previously hallucinated/missed URL matches, cross-assigning bullets
    and creating duplicate projects. The LLM now only does what code
    can't: semantic/title matching across name variants.

    Decisions cover EVERY enriched project. ``enriched_index`` always
    refers to the ORIGINAL enriched list (subset indices from the LLM
    call are remapped back). If an enriched project has no typed match,
    the decision is `add_new` with `typed_index=-1`.
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

    det_decisions, matched_enriched = _deterministic_url_matches(
        typed_projects, enriched_projects,
    )
    unmatched_idxs = [
        i for i in range(len(enriched_projects)) if i not in matched_enriched
    ]

    llm_decisions: list[dict] = []
    if unmatched_idxs:
        enriched_subset = [enriched_projects[i] for i in unmatched_idxs]
        subset_decisions = _adjudicate_unmatched(typed_projects, enriched_subset)
        # Remap subset-relative enriched_index back to the original list.
        # typed_index stays in original space (full typed list is sent to
        # the LLM). Any rare double-merge into an already-matched typed
        # slot is caught by _dedup_by_canonical_name in apply_decisions.
        for d in subset_decisions:
            se = d.get('enriched_index', -1)
            if 0 <= se < len(unmatched_idxs):
                d['enriched_index'] = unmatched_idxs[se]
            llm_decisions.append(d)

    decisions = det_decisions + llm_decisions
    # Defensive: ensure every enriched project has exactly one decision.
    seen = {d['enriched_index'] for d in decisions if d.get('enriched_index') is not None}
    for i in range(len(enriched_projects)):
        if i not in seen:
            decisions.append({
                'enriched_index': i,
                'typed_index': -1,
                'action': 'add_new',
                'confidence': 0.5,
                'reason': 'No decision emitted; defaulting to add_new.',
            })
    decisions.sort(key=lambda d: d['enriched_index'])
    logger.info(
        "project_dedupe: %d deterministic URL match(es); LLM adjudicated "
        "%d remaining pair(s); %d decisions total (%d typed, %d enriched)",
        len(det_decisions), len(unmatched_idxs), len(decisions),
        len(typed_projects), len(enriched_projects),
    )
    return decisions


def _adjudicate_unmatched(typed_projects: list[dict], enriched_projects: list[dict]) -> list[dict]:
    """LLM-adjudicate URL-distinct (typed, enriched) pairs. Returns one
    decision per enriched project, with ``enriched_index`` relative to the
    passed (subset) enriched list — the caller remaps to original indices.

    This is the original ``dedupe_projects`` LLM body (prompt + structured
    call + failed_generation recovery + URL-match fallback), unchanged
    except for being fed the URL-unmatched subset. URL matching is now
    handled deterministically upstream by ``_deterministic_url_matches``
    (Issue 9 Path B1).
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

    prompt = f"""NOTE: URL-based matches have ALREADY been computed deterministically
upstream. Every (typed, enriched) pair you see here has DIFFERENT URLs (or
one/both lack a URL). Do NOT rely on "URL match" reasoning — judge SEMANTIC
identity from names, descriptions, and tech stack. When in doubt, add_new.

For each ENRICHED project below, decide whether it represents the
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

=== OUTPUT SHAPE (CRITICAL) ===
Return ONE JSON object with a single `decisions` key:

{{
  "decisions": [
    {{
      "enriched_index": 0,
      "typed_index": 2,
      "action": "merge",
      "confidence": 0.92,
      "reason": "Same GitHub repo URL"
    }},
    {{
      "enriched_index": 1,
      "typed_index": -1,
      "action": "add_new",
      "confidence": 0.85,
      "reason": "No matching project in typed list"
    }}
  ]
}}

CRITICAL: Do NOT wrap the response in an outer array.

  WRONG:  [{{"decisions": [...]}}]
  WRONG:  [{{"name": "DedupeBatch", "parameters": {{...}}}}]
  RIGHT:  {{"decisions": [...]}}

Each entry in `decisions` is a flat object with exactly these 5 keys:
enriched_index, typed_index, action, confidence, reason.
No nested wrappers, no metadata, no extra keys.
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
    except Exception as exc:
        # Groq's tool validator rejects the call when the model wraps its
        # response in a bare list [{"decisions": [...]}] rather than the
        # flat {"decisions": [...]} the schema declares. The JSON itself is
        # perfectly valid — it lives in error.failed_generation. Salvage it
        # before falling back to the dumb URL-match path so we keep the
        # model's semantic merge/add_new/keep_existing decisions.
        recovered = _recover_decisions_from_failed_generation(exc)
        if recovered is not None:
            decisions = recovered
            seen = {d['enriched_index'] for d in decisions}
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
                "project_dedupe: recovered %d decisions from failed_generation "
                "(%d typed × %d enriched)",
                len(decisions), len(typed_projects), len(enriched_projects),
            )
            return decisions
        logger.exception("project_dedupe: LLM call failed; falling back to URL match")
        return _url_match_fallback(typed_projects, enriched_projects)


def auto_apply_enriched_projects(profile) -> dict:
    """Deprecated — thin wrapper around ``rebuild_master_profile``.

    All new code should call ``rebuild_master_profile`` directly. This wrapper
    is kept so existing tests and any call sites that haven't been updated yet
    continue to work without modification.

    The old implementation read from ``data_content['projects']`` as the typed
    baseline, which caused duplicates when enriched entries were mixed back in
    on re-runs. ``rebuild_master_profile`` fixes this by always reading
    ``data_content['projects_typed']`` as the immutable typed baseline.
    """
    import warnings
    warnings.warn(
        "auto_apply_enriched_projects is deprecated; call "
        "profiles.services.profile_rebuilder.rebuild_master_profile instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from profiles.services.profile_rebuilder import rebuild_master_profile
    return rebuild_master_profile(profile)


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

    # Issue 9 defensive dedup: collapse any duplicate-canonical-name
    # entries a decision bug might have produced (belt-and-suspenders
    # behind the deterministic URL pre-match). Tiebreak prefers the entry
    # whose URL matches the typed source for that canonical name.
    typed_url_lookup: dict[str, str] = {}
    for t in (typed_projects or []):
        if not isinstance(t, dict):
            continue
        canon = _canonical_name(t.get('name', ''))
        u = _normalize_url(t.get('url', ''))
        if canon and u and canon not in typed_url_lookup:
            typed_url_lookup[canon] = u
    final = _dedup_by_canonical_name(final, typed_url_lookup)
    return final


# --- Helpers -----------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r'[^a-z0-9]+')


def _canonical_name(name: str) -> str:
    """Canonicalize a project name for dedup: lowercase, collapse all
    non-alphanumeric runs. 'Apotheosis Traffic-Sign Detection' and
    'apotheosis-traffic-sign-detection' canonicalize identically."""
    return _NON_ALNUM_RE.sub('', (name or '').lower())


def _pick_better_entry(
    a: dict, b: dict, canon: str, typed_url_lookup: dict[str, str] | None,
) -> dict:
    """Tiebreak two same-canonical-name project entries. Priority:
    (1) URL matches the typed source URL for this name; (2) non-empty
    description/bullets; (3) first occurrence (``a``)."""
    # Layer 1: URL match against the typed source.
    if typed_url_lookup:
        expected = typed_url_lookup.get(canon)
        if expected:
            a_match = _normalize_url(a.get('url', '')) == expected
            b_match = _normalize_url(b.get('url', '')) == expected
            if a_match and not b_match:
                return a
            if b_match and not a_match:
                return b
    # Layer 2: non-empty description/bullets wins.
    a_has = bool(a.get('description') or a.get('bullets'))
    b_has = bool(b.get('description') or b.get('bullets'))
    if a_has and not b_has:
        return a
    if b_has and not a_has:
        return b
    # Layer 3: first occurrence.
    return a


def _dedup_by_canonical_name(
    projects: list[dict], typed_url_lookup: dict[str, str] | None = None,
) -> list[dict]:
    """Collapse entries sharing a canonical name. Stable: preserves the
    first-seen order of the winning entries. Entries whose name doesn't
    canonicalize (empty) pass through untouched."""
    if not projects:
        return projects

    winners: dict[str, dict] = {}
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        canon = _canonical_name(proj.get('name', ''))
        if not canon:
            continue
        existing = winners.get(canon)
        winners[canon] = (
            proj if existing is None
            else _pick_better_entry(existing, proj, canon, typed_url_lookup)
        )

    result: list[dict] = []
    emitted: set[str] = set()
    for proj in projects:
        if not isinstance(proj, dict):
            result.append(proj)
            continue
        canon = _canonical_name(proj.get('name', ''))
        if not canon:
            result.append(proj)  # un-canonicalizable — pass through
            continue
        if canon in emitted:
            continue
        result.append(winners[canon])
        emitted.add(canon)
    return result

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
    tech_stack is unioned (case-insensitive); bullets are concatenated with
    a similarity-based dedupe so semantically-equivalent phrasings collapse
    ("Built a Power BI dashboard for HR analytics…" vs "Developed an
    interactive HR analytics dashboard in Power BI…")."""
    from difflib import SequenceMatcher

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
    # Bullets: concat, then drop enriched ones whose SequenceMatcher ratio
    # against any already-kept bullet is ≥ 0.75. Same threshold + library
    # the signal_merger uses for company-name fuzzy dedup. Catches near-
    # duplicates like "Developed an HR analytics dashboard in Power BI"
    # vs "Built a Power BI dashboard for HR analytics".
    typed_desc = list(merged.get('description') or [])
    if isinstance(typed_desc, str):
        typed_desc = [line.strip() for line in typed_desc.split('\n') if line.strip()]
    enriched_bullets = list(enriched.get('bullets') or [])
    for b in enriched_bullets:
        b_norm = b.lower().strip()
        if not b_norm:
            continue
        is_dup = False
        for kept in typed_desc:
            kept_norm = kept.lower().strip() if isinstance(kept, str) else ''
            if not kept_norm:
                continue
            if SequenceMatcher(None, b_norm, kept_norm).ratio() >= 0.75:
                is_dup = True
                break
        if not is_dup:
            typed_desc.append(b)
    merged['description'] = typed_desc
    # Keep source provenance only if typed has none.
    if not merged.get('source'):
        merged['source'] = enriched.get('source', '')
        merged['source_id'] = enriched.get('source_id', '')
    return merged


def _url_match_fallback(typed_projects: list[dict], enriched_projects: list[dict]) -> list[dict]:
    """Conservative URL-match dedupe used when the LLM is unavailable. Only
    matches on exact-after-normalization URL; everything else is add_new.

    Shares ``_normalize_url`` with the deterministic pre-matcher so the
    two never drift (Issue 9)."""
    typed_url_to_idx: dict[str, int] = {}
    for i, p in enumerate(typed_projects):
        u = _normalize_url(p.get('url', ''))
        if u:
            typed_url_to_idx[u] = i

    decisions: list[dict] = []
    for i, e in enumerate(enriched_projects):
        eu = _normalize_url(e.get('source_url', ''))
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
