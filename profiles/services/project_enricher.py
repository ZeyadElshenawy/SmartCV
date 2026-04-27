"""Project enrichment from external signal sources.

The aggregators (`github_aggregator`, `scholar_aggregator`, `kaggle_aggregator`)
pull raw signal data into `UserProfile.data_content['<source>_signals']`. This
module turns each repo / paper / Kaggle category into a project-shaped artifact
with resume-ready bullets, ready to be deduplicated against the user's typed
projects and surfaced in the resume editor.

Design choices:

- **One LLM call per source**, not one per item. A 12-repo enrichment is a
  single batched call, not 12 round trips.
- **Hash-based cache** on the profile's `data_content['enriched_projects_*']`
  keys so revisiting an unchanged set of signals is free. The hash covers
  only the input signals, not the output — if you re-run the enrichment you
  get the same set of `EnrichedProject` objects without burning tokens.
- **Source-specific bullet conventions**:
  - GitHub: bullets lead with the action verb + technical content (the kind
    of bullet that fits in an `Experience` or `Projects` section).
  - Scholar: bullets emphasize research outcome, venue, citation impact.
  - Kaggle: bullets emphasize medal count, ranking percentile, dataset scale.
- **Schema-bound output** via `get_structured_llm(EnrichedProjectBatch)` —
  no JSON parsing, no manual repair logic.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import EnrichedProject, EnrichedProjectBatch

logger = logging.getLogger(__name__)

# Cap how much we feed into a single LLM call. Repos > 8 / publications > 6
# typically means the user has a long tail that's not relevant to most JDs;
# we'd rather miss a long-tail repo than blow the token budget on it.
_MAX_REPOS = 8
_MAX_PUBLICATIONS = 6


def enrich_profile(profile, *, force: bool = False) -> list[dict]:
    """Enrich every available signal source on the profile.

    Returns a list of plain dicts (not Pydantic instances) — the caller
    typically wants to JSON-serialize them or merge them into a JSONB
    column, so we hand back the dump directly.

    Caches by a hash over the input signal blocks. Pass `force=True` to
    bypass the cache (e.g. after a model upgrade or prompt change).
    """
    raw = profile.data_content or {}
    github = raw.get('github_signals') or {}
    scholar = raw.get('scholar_signals') or {}
    kaggle = raw.get('kaggle_signals') or {}

    inputs_hash = _hash_inputs(github, scholar, kaggle)
    cached_hash = raw.get('enriched_projects_hash')
    cached = raw.get('enriched_projects_cache') or []

    if not force and cached_hash == inputs_hash and cached:
        logger.info(
            "project_enricher: cache hit (n=%d, hash=%s)",
            len(cached), inputs_hash[:8],
        )
        return cached

    out: list[dict] = []
    if github.get('top_repos'):
        out.extend(_to_dicts(_enrich_github(github)))
    if scholar.get('top_publications'):
        out.extend(_to_dicts(_enrich_scholar(scholar)))
    if _kaggle_has_activity(kaggle):
        out.extend(_to_dicts(_enrich_kaggle(kaggle)))

    # Persist the cache. Whoever called us decides whether to save the
    # profile — we mutate data_content here but don't call profile.save()
    # so the caller controls the transaction.
    raw['enriched_projects_cache'] = out
    raw['enriched_projects_hash'] = inputs_hash
    profile.data_content = raw

    logger.info(
        "project_enricher: enriched %d projects (gh=%d, scholar=%d, kaggle=%d)",
        len(out), len(github.get('top_repos') or []),
        len(scholar.get('top_publications') or []),
        sum(1 for k in ('competitions', 'datasets', 'notebooks') if (kaggle.get(k) or {}).get('count', 0) > 0),
    )
    return out


# --- Per-source enrichers ----------------------------------------------------

def _enrich_github(github: dict) -> list[EnrichedProject]:
    repos = (github.get('top_repos') or [])[:_MAX_REPOS]
    if not repos:
        return []
    languages = ', '.join(
        f"{lb.get('language', '?')} ({lb.get('count', 0)})"
        for lb in (github.get('language_breakdown') or [])[:5]
    )
    prompt = f"""Turn each GitHub repo below into a project-shaped entry suitable
for a resume's Projects section. Return one entry per repo, in the same order.

Each entry needs:
  - `name`: the repo's display name (use `name`, NOT `full_name`).
  - `summary`: one sentence describing what the repo does. Pull from the repo's
    description; if blank, infer from the name + language.
  - `tech_stack`: list of technologies. Always include the primary language.
    Add frameworks/tools if obvious from the description.
  - `bullets`: 2 concise resume bullets. Lead with action verbs (Built,
    Implemented, Designed). Surface concrete proof — star count, fork count,
    language used. Never invent users / metrics / outcomes the repo doesn't
    evidence. If a repo has 50 stars say "50 stars on GitHub", don't say
    "5K users" or "production-grade".
  - `source`: always "github".
  - `source_id`: the repo's `full_name`.
  - `source_url`: the repo's `html_url`.

CANDIDATE'S OVERALL GITHUB CONTEXT:
- Public repos: {github.get('public_repos', 0)}
- Total stars: {github.get('total_stars', 0)}
- Top languages: {languages or 'unknown'}
- Recent commit count (90d): {github.get('recent_commit_count', 0)}

REPOS:
{json.dumps(repos, indent=2, default=str)}
"""
    try:
        structured = get_structured_llm(
            EnrichedProjectBatch,
            temperature=0.3,
            max_tokens=4096,
            task='project_enricher',
        )
        result = structured.invoke(prompt)
        # Be defensive: if the LLM leaves source_url blank, fill from the
        # input repo by index. Same for source / source_id.
        for i, p in enumerate(result.projects):
            src_repo = repos[i] if i < len(repos) else {}
            if not p.source:
                p.source = 'github'
            if not p.source_url:
                p.source_url = src_repo.get('html_url') or ''
            if not p.source_id:
                p.source_id = src_repo.get('full_name') or src_repo.get('name') or ''
        return list(result.projects)
    except Exception:
        logger.exception("project_enricher: GitHub LLM call failed; falling back")
        return _github_fallback(repos)


def _enrich_scholar(scholar: dict) -> list[EnrichedProject]:
    pubs = (scholar.get('top_publications') or [])[:_MAX_PUBLICATIONS]
    if not pubs:
        return []
    profile_url = scholar.get('profile_url') or ''
    affiliation = scholar.get('affiliation') or ''
    prompt = f"""Turn each Google Scholar publication below into a project-shaped
entry for a resume's Projects or Publications section. Return one entry per
publication, in the same order.

Each entry needs:
  - `name`: the publication title (verbatim).
  - `summary`: one sentence describing the contribution / method, inferred
    only from the title + venue. NEVER fabricate findings or numbers the
    title doesn't evidence.
  - `tech_stack`: research methods or tools the title hints at (e.g. "Deep
    Learning", "PyTorch", "fMRI"). If the title doesn't evidence any, leave
    empty rather than guess.
  - `bullets`: 1-2 bullets. Lead with the venue + year if known. Surface the
    citation count if non-zero ("Cited 47 times in peer-reviewed work").
  - `source`: always "scholar".
  - `source_id`: a slug derived from the title (lowercased, hyphenated,
    truncated to 60 chars).
  - `source_url`: always the candidate's profile_url
    ({profile_url!r}) — Scholar doesn't expose stable per-paper URLs.

AUTHOR AFFILIATION: {affiliation or 'unknown'}

PUBLICATIONS:
{json.dumps(pubs, indent=2, default=str)}
"""
    try:
        structured = get_structured_llm(
            EnrichedProjectBatch,
            temperature=0.3,
            max_tokens=3000,
            task='project_enricher',
        )
        result = structured.invoke(prompt)
        for i, p in enumerate(result.projects):
            if not p.source:
                p.source = 'scholar'
            if not p.source_url:
                p.source_url = profile_url
            if not p.source_id and i < len(pubs):
                p.source_id = _slugify(pubs[i].get('title', ''))
        return list(result.projects)
    except Exception:
        logger.exception("project_enricher: Scholar LLM call failed; falling back")
        return _scholar_fallback(pubs, profile_url)


def _enrich_kaggle(kaggle: dict) -> list[EnrichedProject]:
    """Kaggle exposes only aggregate stats per category, not individual entries.
    We surface ONE project per non-empty category (Competitions / Datasets /
    Notebooks / Discussion) rather than one-per-entry.
    """
    profile_url = kaggle.get('profile_url') or ''
    overall_tier = kaggle.get('overall_tier') or ''
    categories = []
    for cat_name in ('competitions', 'datasets', 'notebooks', 'discussion'):
        cat = kaggle.get(cat_name) or {}
        if (cat.get('count') or 0) > 0:
            categories.append({'category': cat_name, **cat})
    if not categories:
        return []

    prompt = f"""Turn each non-empty Kaggle category below into a single
project-shaped entry suitable for a resume. Return one entry per category, in
the same order.

Each entry needs:
  - `name`: short category-aware title (e.g. "Kaggle Competitions",
    "Kaggle Datasets").
  - `summary`: one sentence stating the volume + tier. E.g. "Competitions
    Expert with 12 entries and 3 silver medals."
  - `tech_stack`: empty list (Kaggle's aggregate API doesn't expose
    per-competition tech).
  - `bullets`: 1-2 bullets. Lead with the medal count if any; otherwise
    the count + tier. Concrete: "Silver in 3 competitions; tier:
    Competitions Expert."
  - `source`: always "kaggle".
  - `source_id`: the category name (e.g. "competitions").
  - `source_url`: always the candidate's profile_url ({profile_url!r}).

OVERALL TIER: {overall_tier or 'unranked'}

CATEGORIES:
{json.dumps(categories, indent=2, default=str)}
"""
    try:
        structured = get_structured_llm(
            EnrichedProjectBatch,
            temperature=0.3,
            max_tokens=2000,
            task='project_enricher',
        )
        result = structured.invoke(prompt)
        for i, p in enumerate(result.projects):
            if not p.source:
                p.source = 'kaggle'
            if not p.source_url:
                p.source_url = profile_url
            if not p.source_id and i < len(categories):
                p.source_id = categories[i].get('category', '')
        return list(result.projects)
    except Exception:
        logger.exception("project_enricher: Kaggle LLM call failed; falling back")
        return _kaggle_fallback(categories, profile_url, overall_tier)


# --- Fallbacks (no LLM) ------------------------------------------------------

def _github_fallback(repos: list[dict]) -> list[EnrichedProject]:
    """Deterministic, no-LLM enrichment for when the model is unavailable.
    Produces grounded — if dry — entries straight from the API payload."""
    out = []
    for r in repos:
        name = (r.get('name') or '').strip()
        if not name:
            continue
        desc = (r.get('description') or '').strip()
        lang = (r.get('language') or '').strip()
        stars = r.get('stargazers_count', 0) or 0
        forks = r.get('forks_count', 0) or 0
        bullets = []
        if desc:
            bullets.append(desc)
        bullets.append(
            f"Built in {lang or 'multiple languages'}; {stars} star{'s' if stars != 1 else ''}"
            + (f", {forks} fork{'s' if forks != 1 else ''}" if forks else '')
            + " on GitHub."
        )
        out.append(EnrichedProject(
            name=name,
            summary=desc or f"{name} — public repository.",
            tech_stack=[lang] if lang else [],
            bullets=bullets,
            source='github',
            source_id=r.get('full_name') or name,
            source_url=r.get('html_url') or '',
        ))
    return out


def _scholar_fallback(pubs: list[dict], profile_url: str) -> list[EnrichedProject]:
    out = []
    for p in pubs:
        title = (p.get('title') or '').strip()
        if not title:
            continue
        venue = (p.get('venue') or '').strip()
        year = (p.get('year') or '').strip()
        cites = p.get('citations', 0) or 0
        bullet_bits = []
        if venue:
            bullet_bits.append(venue + (f" ({year})" if year else ''))
        if cites:
            bullet_bits.append(f"Cited {cites} times")
        out.append(EnrichedProject(
            name=title,
            summary=(venue or 'Peer-reviewed publication') + (f", {year}" if year else ''),
            tech_stack=[],
            bullets=['; '.join(bullet_bits)] if bullet_bits else [title],
            source='scholar',
            source_id=_slugify(title),
            source_url=profile_url,
        ))
    return out


def _kaggle_fallback(categories: list[dict], profile_url: str, tier: str) -> list[EnrichedProject]:
    out = []
    for cat in categories:
        name = cat.get('category', '')
        count = cat.get('count', 0) or 0
        cat_tier = cat.get('tier') or ''
        medals = cat.get('medals') or {}
        gold = medals.get('gold', 0) or 0
        silver = medals.get('silver', 0) or 0
        bronze = medals.get('bronze', 0) or 0
        medal_bits = []
        if gold:
            medal_bits.append(f"{gold} gold")
        if silver:
            medal_bits.append(f"{silver} silver")
        if bronze:
            medal_bits.append(f"{bronze} bronze")
        bullet = f"{count} {name}"
        if cat_tier:
            bullet += f" — tier: {cat_tier}"
        if medal_bits:
            bullet += f"; medals: {', '.join(medal_bits)}"
        out.append(EnrichedProject(
            name=f"Kaggle {name.title()}",
            summary=bullet,
            tech_stack=[],
            bullets=[bullet],
            source='kaggle',
            source_id=name,
            source_url=profile_url,
        ))
    return out


# --- Helpers -----------------------------------------------------------------

def _to_dicts(projects: list[EnrichedProject]) -> list[dict]:
    return [p.model_dump() for p in projects if p.name]


def _hash_inputs(github: dict, scholar: dict, kaggle: dict) -> str:
    """Stable hash over the parts of the signal blobs the prompts actually
    consume. Excludes `fetched_at` so a re-pull that returned identical data
    doesn't invalidate the cache."""
    payload = {
        'github': {
            'public_repos': github.get('public_repos'),
            'total_stars': github.get('total_stars'),
            'top_repos': [
                {k: r.get(k) for k in ('full_name', 'description', 'stargazers_count',
                                       'forks_count', 'language')}
                for r in (github.get('top_repos') or [])
            ],
            'language_breakdown': github.get('language_breakdown'),
            'recent_commit_count': github.get('recent_commit_count'),
        },
        'scholar': {
            'affiliation': scholar.get('affiliation'),
            'top_publications': scholar.get('top_publications'),
            'total_citations': scholar.get('total_citations'),
            'h_index': scholar.get('h_index'),
        },
        'kaggle': {
            'overall_tier': kaggle.get('overall_tier'),
            'competitions': kaggle.get('competitions'),
            'datasets': kaggle.get('datasets'),
            'notebooks': kaggle.get('notebooks'),
            'discussion': kaggle.get('discussion'),
        },
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


def _kaggle_has_activity(kaggle: dict) -> bool:
    if not kaggle:
        return False
    for cat in ('competitions', 'datasets', 'notebooks', 'discussion'):
        if (kaggle.get(cat) or {}).get('count', 0) > 0:
            return True
    return False


def _slugify(text: str, max_len: int = 60) -> str:
    if not text:
        return ''
    s = ''.join(c.lower() if c.isalnum() else '-' for c in text)
    s = '-'.join(part for part in s.split('-') if part)
    return s[:max_len]
