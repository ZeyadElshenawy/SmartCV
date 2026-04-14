# Profile-Strength Scoring — Design Spec

**Date:** 2026-04-15
**Feature:** #2 of 3 in the agent-enrichment arc (Feature #1 = job-aware context, shipped in commit `bbfcfcd`; Feature #3 = proactive notifications, follows this)
**Status:** Design approved, pending user spec review

## Problem

SmartCV surfaces evidence-confidence on `/insights/` and a stage-based hero on the dashboard, but nowhere does it tell the user "how strong is my profile overall, and what would most improve it?" The user has to guess what to polish next. A single top-line score — with component breakdown and actionable CTAs — turns the profile into an upgradable object.

## Goal

A pure function `compute_profile_strength(profile, user)` returns a `ProfileStrength` dict describing a 0-100 score, a tier label, three weighted components, and the top 3 actionable CTAs. Surfaced on the dashboard hero (compact ring) and `/insights/` (full breakdown with clickable CTA chips).

## Non-goals

- Persisting score history / tracking over time
- Background recomputation (cron, async)
- Real-time updates (every reload recomputes; cheap)
- Gamification (badges, streaks, confetti)
- Scoring gating other features
- Feeding the score into any LLM prompt (future cycle)
- Caching (add only if profiling shows a problem)

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  profiles/services/profile_strength.py  (new)                        │
│                                                                      │
│  compute_profile_strength(profile, user) -> ProfileStrength          │
│                                                                      │
│  Component scorers (pure, all inside the same module):               │
│    _score_completeness(profile)   -> Component (max 35)              │
│    _score_evidence(profile)       -> Component (max 30)              │
│    _score_signals(profile)        -> Component (max 35)              │
│                                                                      │
│  Derived:                                                            │
│    _tier(score) -> 'Weak' | 'Developing' | 'Solid' | 'Strong'        │
│    _top_actions(components) -> list[StrengthAction] (top 3)          │
└──────────────┬───────────────────────────────────────────────────────┘
               │
               ├────▶  profiles/views.py : dashboard_view                  (hero ring)
               └────▶  core/views.py      : insights_view                  (full breakdown)
                         │
                         └───▶ templates/components/profile_strength_ring.html
                               templates/components/profile_strength_breakdown.html
```

Both templates are new and live under `templates/components/` — they inherit the existing primitive-component pattern from the design system.

## Types

```python
from typing import TypedDict, Literal

Tier = Literal['Weak', 'Developing', 'Solid', 'Strong']

class StrengthItem(TypedDict):
    key: str          # stable identifier, e.g. 'has_summary'
    label: str        # user-facing, e.g. 'Add a professional summary'
    met: bool
    points: int       # contribution when met

class StrengthComponent(TypedDict):
    key: str          # 'completeness' | 'evidence' | 'signals'
    label: str        # display label
    score: int        # earned points
    max: int          # maximum (35 / 30 / 35)
    items: list[StrengthItem]

class StrengthAction(TypedDict):
    label: str        # e.g. 'Connect GitHub · +14 points'
    href: str
    points: int       # for sorting + badge display

class ProfileStrength(TypedDict):
    score: int        # 0-100
    tier: Tier
    components: list[StrengthComponent]  # length 3
    top_actions: list[StrengthAction]    # length up to 3
```

## Component breakdown

### Component A: Completeness (max 35)

| Item key            | Label                                   | Points | Condition |
|---------------------|-----------------------------------------|--------|-----------|
| `has_identity`      | Add your name and email                 | 5      | `profile.full_name` truthy AND `profile.email` truthy |
| `has_three_exps`    | Describe at least 3 experiences         | 10     | `len(profile.experiences) >= 3` AND each of the first 3 has a non-empty description |
| `has_education`     | Add at least one education entry        | 5      | `len(profile.education) >= 1` |
| `has_five_skills`   | List at least 5 skills                  | 5      | `len(profile.skills) >= 5` |
| `has_summary`       | Write a professional summary            | 5      | `profile.data_content.get('summary')` truthy (≥40 chars) |
| `has_location_phone`| Fill in location and phone              | 5      | `profile.location` truthy AND `profile.phone` truthy |

### Component B: Evidence Depth (max 30)

| Item key             | Label                                                    | Points | Condition |
|----------------------|----------------------------------------------------------|--------|-----------|
| `descriptions_rich`  | Flesh out experience descriptions (≥150 chars each)      | 10     | Average description length across first 5 experiences ≥150 chars |
| `has_project`        | Add at least one described project                       | 6      | ≥1 project with non-empty description |
| `has_credential`     | Add a certification, publication, or award               | 6      | Any of `profile.certifications` / `data_content.publications` / `data_content.awards` non-empty |
| `descriptions_metric`| Quantify wins in at least one experience                 | 8      | ≥1 experience description contains a digit (0-9) |

### Component C: External Signals (max 35)

| Item key             | Label                                               | Points | Condition |
|----------------------|-----------------------------------------------------|--------|-----------|
| `github_connected`   | Connect GitHub                                      | 14     | `github_signals` exists, `error` is falsy, `public_repos >= 1` |
| `scholar_or_kaggle`  | Connect Google Scholar or Kaggle                    | 10     | Either signal object exists, non-error, and has at least one meaningful count (citations / competitions / datasets / notebooks) |
| `has_linkedin`       | Add your LinkedIn URL                               | 4      | `profile.linkedin_url` truthy |
| `signals_fresh`      | Refresh your external signals (older than 90 days)  | 7      | Most recent signal `fetched_at` within last 90 days. If no signals connected, item is `met=False` (don't double-penalize — the prior two items already capture that, but we still reward refresh hygiene). |

## Tier thresholds

- **Weak** — 0–34
- **Developing** — 35–59
- **Solid** — 60–79
- **Strong** — 80–100

Labels render with existing tone tokens: Weak = `neutral`, Developing = `accent`, Solid = `brand`, Strong = `success`.

## Top actions selection

1. Flatten all `items` where `met is False` across all three components.
2. Sort by `points` descending, then by a stable `key` order (alphabetical) to break ties deterministically.
3. Take first 3.
4. For each, synthesize a `StrengthAction`:
   - `label`: `f"{item.label} · +{item.points} points"`
   - `href`: looked up via a per-`key` map (see below)
   - `points`: `item.points`

### href map (by item key)

| Item key               | href |
|------------------------|------|
| `has_identity`         | `/profiles/setup/review/` |
| `has_three_exps`       | `/profiles/setup/review/` |
| `has_education`        | `/profiles/setup/review/` |
| `has_five_skills`      | `/profiles/setup/review/` |
| `has_summary`          | `/profiles/setup/review/` |
| `has_location_phone`   | `/profiles/setup/review/` |
| `descriptions_rich`    | `/profiles/setup/review/` |
| `has_project`          | `/profiles/setup/review/` |
| `has_credential`       | `/profiles/setup/review/` |
| `descriptions_metric`  | `/profiles/setup/review/` |
| `github_connected`     | `/insights/` |
| `scholar_or_kaggle`    | `/insights/` |
| `has_linkedin`         | `/profiles/setup/review/` |
| `signals_fresh`        | `/insights/` |

## Surfaces

### Dashboard hero (compact)

New partial: `templates/components/profile_strength_ring.html`. Renders a small SVG ring with the score number inside, tier label below, and a single-line nudge derived from `top_actions[0]` when one exists ("Connect GitHub to reach **Solid**"). Positioned in the hero sidebar, next to the stage-aware CTA block.

### /insights/ (full)

New partial: `templates/components/profile_strength_breakdown.html`. A card with:
- Header row: big score + tier badge
- Three component bars (label, earned / max, progress fill)
- Collapsible list of unmet items per component (Alpine `x-show` toggle)
- Top 3 CTA chips at the bottom, each clickable

The existing evidence-confidence tile on `/insights/` stays — they measure different things (evidence confidence = per-skill corroboration against signals; profile strength = holistic breadth). Adjacent placement.

## Data flow

1. User opens `/profiles/dashboard/` → view calls `compute_profile_strength(profile, user)` → context carries `profile_strength`.
2. Template renders the ring partial with the compact subset of fields (score, tier, first action).
3. User clicks the ring (or a nudge link) → navigates to `/insights/` with a hash anchor `#profile-strength`.
4. `/insights/` view computes the same structure, renders the full breakdown partial.
5. User clicks a CTA chip → navigates to `/profiles/setup/review/` or `/insights/` (to connect signals) → fixes the gap → returns → recomputed on next render.

## Edge cases

| Scenario | Behavior |
|---|---|
| No `UserProfile` row exists | `get_or_create` in view; score = 0; tier = Weak; all items unmet; top actions = top 3 highest-point items |
| `data_content` is `{}` or `None` | Treated as empty for all sub-reads; no crash |
| Extremely old profile (created before `has_seen_welcome` flag etc.) | Keys just absent; sub-reads tolerate that |
| Signals dict has `error` key | Item counts as unmet regardless of other fields |
| User has >100 skills | Capped at 5 for scoring (only count matters); doesn't bloat |
| Profile `experiences[0]` missing `description` key | Treated as empty string for length calc |

## Testing

New file: add a class `ProfileStrengthTests` to `profiles/tests.py` (existing convention).

Unit tests for the pure function:

1. `test_empty_profile_scores_zero_and_weak_tier`
2. `test_completeness_full_scores_max`
3. `test_completeness_partial_only_counts_met_items`
4. `test_evidence_full_scores_max`
5. `test_evidence_description_metrics_require_digit`
6. `test_signals_github_awards_points_when_repos_positive`
7. `test_signals_errored_github_counts_as_unmet`
8. `test_signals_freshness_requires_recent_fetched_at`
9. `test_tier_thresholds_boundary_cases` (0, 34, 35, 59, 60, 79, 80, 100)
10. `test_top_actions_returns_three_highest_point_unmet_items`
11. `test_top_actions_stable_tiebreak_by_key`
12. `test_top_actions_empty_when_profile_maxed_out`
13. `test_href_map_covers_every_item_key`
14. `test_score_is_sum_of_component_scores` (integration sanity)

Integration tests for the views:

15. `test_dashboard_view_includes_profile_strength_in_context` (profiles/tests.py or core/tests.py depending on which view)
16. `test_insights_view_includes_profile_strength_in_context`

Template render tests are implicit via the view tests (Django test client `assertContains` on tier label / score).

Target: ~16 new tests. Full suite 177 → ~193.

## Out of scope (future cycles)

- Feature #3: Proactive agent notifications (can consume `profile_strength` signals — e.g., "your profile dropped a tier because a signal went stale" — but that's its own design)
- Feeding strength into the agent system prompt
- Historical tracking / charts
- Per-job "how strong is my profile *for this job*" (different axis — matches gap analysis territory, not this feature)
