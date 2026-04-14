# Profile-Strength Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute a 0-100 profile-strength score with tier, three weighted components, and top-3 actionable CTAs; render as a compact ring on the dashboard and as a full breakdown on `/insights/`.

**Architecture:** Add a pure-function module `profiles/services/profile_strength.py` exporting `compute_profile_strength(profile, user) -> ProfileStrength`. Views inject the result into context; two new template partials render it. No caching, no persistence, no async — recomputed on each render.

**Tech Stack:** Django 5.2, Tailwind v4, Alpine.js. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-15-profile-strength-scoring-design.md`

---

## File Structure

**Create:**
- `profiles/services/profile_strength.py` — the module
- `templates/components/profile_strength_ring.html` — compact dashboard partial
- `templates/components/profile_strength_breakdown.html` — full insights partial

**Modify:**
- `profiles/views.py` — `dashboard` view injects `profile_strength` into context
- `core/views.py` — `insights_view` injects `profile_strength` into context
- `templates/profiles/dashboard.html` — include ring partial
- `templates/core/insights.html` — include breakdown partial
- `profiles/tests.py` — new test class `ProfileStrengthTests`
- `core/tests.py` — extend `insights_view` test (or add new) to assert `profile_strength` in context

**Test:** all unit tests in `profiles/tests.py`; view tests split between `profiles/tests.py` (dashboard) and `core/tests.py` (insights).

---

## Task 1: Module skeleton + TypedDicts

**Files:**
- Create: `profiles/services/profile_strength.py`
- Test: `profiles/tests.py` — new class `ProfileStrengthTests`

- [ ] **Step 1: Create the skeleton file.**

Write `profiles/services/profile_strength.py`:

```python
"""Profile-strength scoring.

Pure function `compute_profile_strength(profile, user)` returns a dict
describing the profile's overall strength (0-100), tier label, three
weighted components, and up to three actionable CTAs.

Scoring is recomputed on each render — no caching, no persistence.
"""
from __future__ import annotations

from typing import TypedDict, Literal


Tier = Literal['Weak', 'Developing', 'Solid', 'Strong']


class StrengthItem(TypedDict):
    key: str
    label: str
    met: bool
    points: int


class StrengthComponent(TypedDict):
    key: str
    label: str
    score: int
    max: int
    items: list[StrengthItem]


class StrengthAction(TypedDict):
    label: str
    href: str
    points: int


class ProfileStrength(TypedDict):
    score: int
    tier: Tier
    components: list[StrengthComponent]
    top_actions: list[StrengthAction]


# CTA href map — stable routing from item keys to user-facing destinations.
HREF_BY_KEY: dict[str, str] = {
    'has_identity':         '/profiles/setup/review/',
    'has_three_exps':       '/profiles/setup/review/',
    'has_education':        '/profiles/setup/review/',
    'has_five_skills':      '/profiles/setup/review/',
    'has_summary':          '/profiles/setup/review/',
    'has_location_phone':   '/profiles/setup/review/',
    'descriptions_rich':    '/profiles/setup/review/',
    'has_project':          '/profiles/setup/review/',
    'has_credential':       '/profiles/setup/review/',
    'descriptions_metric':  '/profiles/setup/review/',
    'github_connected':     '/insights/',
    'scholar_or_kaggle':    '/insights/',
    'has_linkedin':         '/profiles/setup/review/',
    'signals_fresh':        '/insights/',
}


def compute_profile_strength(profile, user) -> ProfileStrength:
    """Top-level entry point — stub until subsequent tasks wire up components."""
    return ProfileStrength(score=0, tier='Weak', components=[], top_actions=[])
```

- [ ] **Step 2: Write initial smoke test.** Append to `profiles/tests.py` (at end of file):

```python
# ============================================================
# Profile strength scoring
# ============================================================

from django.test import TestCase
from django.contrib.auth import get_user_model


class ProfileStrengthTests(TestCase):
    """compute_profile_strength — see spec 2026-04-15-profile-strength-scoring-design.md."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ps@example.com', email='ps@example.com', password='x'
        )

    def _make_profile(self, **overrides):
        from profiles.models import UserProfile
        defaults = dict(user=self.user, full_name='', email='', data_content={})
        defaults.update(overrides)
        return UserProfile.objects.create(**defaults)

    def test_module_exports_compute_profile_strength(self):
        from profiles.services.profile_strength import compute_profile_strength
        self.assertTrue(callable(compute_profile_strength))

    def test_href_map_covers_every_item_key(self):
        from profiles.services.profile_strength import HREF_BY_KEY
        expected_keys = {
            'has_identity', 'has_three_exps', 'has_education', 'has_five_skills',
            'has_summary', 'has_location_phone',
            'descriptions_rich', 'has_project', 'has_credential', 'descriptions_metric',
            'github_connected', 'scholar_or_kaggle', 'has_linkedin', 'signals_fresh',
        }
        self.assertEqual(set(HREF_BY_KEY.keys()), expected_keys)
```

- [ ] **Step 3: Run the tests.**

`python manage.py test profiles.tests.ProfileStrengthTests -v 2`

Expected: 2 tests PASS.

- [ ] **Step 4: Commit.**

```bash
git add profiles/services/profile_strength.py profiles/tests.py
git commit -m "feat(profile): profile_strength scaffold — types, href map, stub fn"
```

---

## Task 2: Completeness component scorer (max 35)

**Files:**
- Modify: `profiles/services/profile_strength.py`
- Test: `profiles/tests.py` — extend `ProfileStrengthTests`

- [ ] **Step 1: Write failing tests.** Append to `ProfileStrengthTests`:

```python
    def test_completeness_empty_profile_scores_zero(self):
        from profiles.services.profile_strength import _score_completeness
        profile = self._make_profile()
        c = _score_completeness(profile)
        self.assertEqual(c['key'], 'completeness')
        self.assertEqual(c['max'], 35)
        self.assertEqual(c['score'], 0)
        self.assertTrue(all(not i['met'] for i in c['items']))

    def test_completeness_full_profile_scores_max(self):
        from profiles.services.profile_strength import _score_completeness
        profile = self._make_profile(
            full_name='Jane Doe', email='j@example.com',
            location='Cairo', phone='+20 100 000',
            data_content={
                'summary': 'x' * 50,
                'skills': [{'name': s} for s in ['Python', 'Go', 'SQL', 'React', 'Django']],
                'experiences': [
                    {'title': 'A', 'description': 'Did stuff.'},
                    {'title': 'B', 'description': 'More stuff.'},
                    {'title': 'C', 'description': 'Even more.'},
                ],
                'education': [{'degree': 'BSc', 'institution': 'KSIU'}],
            },
        )
        c = _score_completeness(profile)
        self.assertEqual(c['score'], 35)
        self.assertTrue(all(i['met'] for i in c['items']))

    def test_completeness_partial_only_counts_met_items(self):
        from profiles.services.profile_strength import _score_completeness
        profile = self._make_profile(
            full_name='Jane', email='j@example.com',
            data_content={'skills': [{'name': s} for s in ['Python', 'Go', 'SQL', 'React', 'Django']]},
        )
        c = _score_completeness(profile)
        # identity (5) + skills (5) = 10
        self.assertEqual(c['score'], 10)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['has_identity'])
        self.assertTrue(met['has_five_skills'])
        self.assertFalse(met['has_three_exps'])
        self.assertFalse(met['has_education'])
        self.assertFalse(met['has_summary'])
        self.assertFalse(met['has_location_phone'])
```

- [ ] **Step 2: Run. Expect ImportError on `_score_completeness`.**

`python manage.py test profiles.tests.ProfileStrengthTests -v 2`

- [ ] **Step 3: Implement `_score_completeness`.** In `profiles/services/profile_strength.py`, add above the `compute_profile_strength` function:

```python
def _score_completeness(profile) -> StrengthComponent:
    """35-point breakdown: identity, experiences, education, skills, summary, contact."""
    data = profile.data_content or {}
    experiences = data.get('experiences') or []
    education = data.get('education') or []
    skills = data.get('skills') or []
    summary = (data.get('summary') or '').strip()

    items: list[StrengthItem] = []

    # Identity
    items.append(StrengthItem(
        key='has_identity',
        label='Add your name and email',
        met=bool(profile.full_name) and bool(profile.email),
        points=5,
    ))

    # Three experiences with descriptions
    has_three_exps = (
        len(experiences) >= 3
        and all(
            isinstance(exp, dict) and (exp.get('description') or '').strip()
            for exp in experiences[:3]
        )
    )
    items.append(StrengthItem(
        key='has_three_exps',
        label='Describe at least 3 experiences',
        met=has_three_exps,
        points=10,
    ))

    # Education
    items.append(StrengthItem(
        key='has_education',
        label='Add at least one education entry',
        met=len(education) >= 1,
        points=5,
    ))

    # Five skills
    items.append(StrengthItem(
        key='has_five_skills',
        label='List at least 5 skills',
        met=len(skills) >= 5,
        points=5,
    ))

    # Summary
    items.append(StrengthItem(
        key='has_summary',
        label='Write a professional summary',
        met=len(summary) >= 40,
        points=5,
    ))

    # Location + phone
    items.append(StrengthItem(
        key='has_location_phone',
        label='Fill in location and phone',
        met=bool(profile.location) and bool(profile.phone),
        points=5,
    ))

    score = sum(i['points'] for i in items if i['met'])
    return StrengthComponent(
        key='completeness', label='Completeness',
        score=score, max=35, items=items,
    )
```

- [ ] **Step 4: Run. Expect 3 new tests PASS.**

- [ ] **Step 5: Commit.**

```bash
git add profiles/services/profile_strength.py profiles/tests.py
git commit -m "feat(profile): _score_completeness — 35-pt component"
```

---

## Task 3: Evidence-depth component scorer (max 30)

**Files:**
- Modify: `profiles/services/profile_strength.py`
- Test: `profiles/tests.py` — extend `ProfileStrengthTests`

- [ ] **Step 1: Write failing tests.** Append to `ProfileStrengthTests`:

```python
    def test_evidence_empty_profile_scores_zero(self):
        from profiles.services.profile_strength import _score_evidence
        profile = self._make_profile()
        c = _score_evidence(profile)
        self.assertEqual(c['key'], 'evidence')
        self.assertEqual(c['max'], 30)
        self.assertEqual(c['score'], 0)

    def test_evidence_full_scores_max(self):
        from profiles.services.profile_strength import _score_evidence
        long_desc = 'Led a team to deliver 30% faster throughput across 5 services.' * 3
        profile = self._make_profile(
            data_content={
                'experiences': [
                    {'description': long_desc},
                    {'description': long_desc},
                    {'description': long_desc},
                ],
                'projects': [{'name': 'X', 'description': 'Built a thing.'}],
                'certifications': [{'name': 'AWS SAA'}],
            },
        )
        c = _score_evidence(profile)
        self.assertEqual(c['score'], 30)

    def test_evidence_descriptions_metric_requires_digit(self):
        from profiles.services.profile_strength import _score_evidence
        from profiles.models import UserProfile

        # First profile: description contains a digit → metric met.
        profile_with = self._make_profile(
            data_content={
                'experiences': [{'description': 'Improved throughput by 30% and cut latency.'}],
            },
        )
        c = _score_evidence(profile_with)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['descriptions_metric'])

        # Second profile: description has no digit → metric NOT met.
        # UserProfile has a OneToOne with user, so we need a separate user.
        u2 = get_user_model().objects.create_user(
            username='b@example.com', email='b@example.com', password='x'
        )
        profile_without = UserProfile.objects.create(
            user=u2, full_name='B', email='b@example.com',
            data_content={'experiences': [{'description': 'Improved throughput and cut latency.'}]},
        )
        c2 = _score_evidence(profile_without)
        met2 = {i['key']: i['met'] for i in c2['items']}
        self.assertFalse(met2['descriptions_metric'])

    def test_evidence_credential_accepts_publications_or_awards(self):
        from profiles.services.profile_strength import _score_evidence
        profile = self._make_profile(data_content={'publications': [{'title': 'Paper'}]})
        c = _score_evidence(profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['has_credential'])
```

- [ ] **Step 2: Run. Expect failures.**

- [ ] **Step 3: Implement `_score_evidence`.** Add below `_score_completeness` in `profiles/services/profile_strength.py`:

```python
def _score_evidence(profile) -> StrengthComponent:
    """30-point breakdown: description richness, projects, credentials, quantification."""
    data = profile.data_content or {}
    experiences = [e for e in (data.get('experiences') or []) if isinstance(e, dict)]
    projects = [p for p in (data.get('projects') or []) if isinstance(p, dict)]
    certifications = data.get('certifications') or []
    publications = data.get('publications') or []
    awards = data.get('awards') or []

    items: list[StrengthItem] = []

    # Description richness — mean length of first 5 experience descriptions
    descs = [(e.get('description') or '').strip() for e in experiences[:5]]
    avg_len = (sum(len(d) for d in descs) / len(descs)) if descs else 0
    items.append(StrengthItem(
        key='descriptions_rich',
        label='Flesh out experience descriptions (≥150 chars each)',
        met=avg_len >= 150,
        points=10,
    ))

    # Has project
    items.append(StrengthItem(
        key='has_project',
        label='Add at least one described project',
        met=any((p.get('description') or '').strip() for p in projects),
        points=6,
    ))

    # Has credential
    has_credential = (
        (len(certifications) > 0)
        or (len(publications) > 0)
        or (len(awards) > 0)
    )
    items.append(StrengthItem(
        key='has_credential',
        label='Add a certification, publication, or award',
        met=has_credential,
        points=6,
    ))

    # Metric in at least one description
    any_metric = any(
        any(ch.isdigit() for ch in (e.get('description') or ''))
        for e in experiences
    )
    items.append(StrengthItem(
        key='descriptions_metric',
        label='Quantify wins in at least one experience',
        met=any_metric,
        points=8,
    ))

    score = sum(i['points'] for i in items if i['met'])
    return StrengthComponent(
        key='evidence', label='Evidence depth',
        score=score, max=30, items=items,
    )
```

- [ ] **Step 4: Run. Expect all new tests PASS.**

- [ ] **Step 5: Commit.**

```bash
git add profiles/services/profile_strength.py profiles/tests.py
git commit -m "feat(profile): _score_evidence — 30-pt component"
```

---

## Task 4: Signals component scorer (max 35)

**Files:**
- Modify: `profiles/services/profile_strength.py`
- Test: `profiles/tests.py` — extend `ProfileStrengthTests`

- [ ] **Step 1: Write failing tests.**

```python
    def test_signals_empty_profile_scores_zero(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile()
        c = _score_signals(profile)
        self.assertEqual(c['key'], 'signals')
        self.assertEqual(c['max'], 35)
        self.assertEqual(c['score'], 0)

    def test_signals_github_with_repos_scores_14(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(
            data_content={
                'github_signals': {
                    'username': 'x', 'public_repos': 5,
                    'fetched_at': '2026-04-10T00:00:00Z',
                },
            },
        )
        c = _score_signals(profile)
        met = {i['key']: i['points'] for i in c['items'] if i['met']}
        self.assertEqual(met.get('github_connected'), 14)

    def test_signals_errored_github_counts_as_unmet(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(
            data_content={
                'github_signals': {
                    'error': 'rate limited', 'username': 'x', 'public_repos': 99,
                },
            },
        )
        c = _score_signals(profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertFalse(met['github_connected'])

    def test_signals_scholar_citations_awards_points(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(
            data_content={'scholar_signals': {'total_citations': 25, 'fetched_at': '2026-04-10T00:00:00Z'}},
        )
        c = _score_signals(profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['scholar_or_kaggle'])

    def test_signals_kaggle_competitions_awards_points(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(
            data_content={'kaggle_signals': {'competitions': {'count': 2}, 'fetched_at': '2026-04-10T00:00:00Z'}},
        )
        c = _score_signals(profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['scholar_or_kaggle'])

    def test_signals_linkedin_url_awards_points(self):
        from profiles.services.profile_strength import _score_signals
        profile = self._make_profile(linkedin_url='https://linkedin.com/in/x')
        c = _score_signals(profile)
        met = {i['key']: i['points'] for i in c['items'] if i['met']}
        self.assertEqual(met.get('has_linkedin'), 4)

    def test_signals_freshness_requires_recent_fetched_at(self):
        from profiles.services.profile_strength import _score_signals
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        fresh_profile = self._make_profile(
            data_content={'github_signals': {'public_repos': 2, 'fetched_at': recent}},
        )
        c = _score_signals(fresh_profile)
        met = {i['key']: i['met'] for i in c['items']}
        self.assertTrue(met['signals_fresh'])

        User = get_user_model()
        u2 = User.objects.create_user(username='s2@example.com', email='s2@example.com', password='x')
        from profiles.models import UserProfile
        stale_profile = UserProfile.objects.create(
            user=u2, full_name='S', email='s@e.com',
            data_content={'github_signals': {'public_repos': 2, 'fetched_at': old}},
        )
        c2 = _score_signals(stale_profile)
        met2 = {i['key']: i['met'] for i in c2['items']}
        self.assertFalse(met2['signals_fresh'])
```

- [ ] **Step 2: Run, confirm failures.**

- [ ] **Step 3: Implement `_score_signals`.** Add below `_score_evidence`:

```python
def _is_active_signal(signal: dict) -> bool:
    """A signal is 'active' when it's a dict, has no error, and shows activity."""
    if not isinstance(signal, dict) or signal.get('error'):
        return False
    # GitHub: repos > 0
    if 'public_repos' in signal:
        return (signal.get('public_repos') or 0) > 0
    # Scholar: citations OR publications
    if 'total_citations' in signal or 'top_publications' in signal:
        return (signal.get('total_citations') or 0) > 0 or bool(signal.get('top_publications'))
    # Kaggle: any of the category counts
    for cat in ('competitions', 'datasets', 'notebooks', 'discussion'):
        entry = signal.get(cat)
        if isinstance(entry, dict) and (entry.get('count') or 0) > 0:
            return True
    return False


def _signal_is_fresh(signal: dict, max_age_days: int = 90) -> bool:
    from datetime import datetime, timezone
    if not isinstance(signal, dict):
        return False
    raw = signal.get('fetched_at')
    if not raw:
        return False
    try:
        # Handle both 'Z' and explicit offsets.
        s = raw.replace('Z', '+00:00') if isinstance(raw, str) else raw
        dt = datetime.fromisoformat(s)
    except Exception:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta_days = (datetime.now(timezone.utc) - dt).days
    return delta_days <= max_age_days


def _score_signals(profile) -> StrengthComponent:
    """35-point breakdown: GitHub, Scholar/Kaggle, LinkedIn, freshness."""
    data = profile.data_content or {}
    gh = data.get('github_signals') or {}
    sc = data.get('scholar_signals') or {}
    kg = data.get('kaggle_signals') or {}

    items: list[StrengthItem] = []

    # GitHub
    items.append(StrengthItem(
        key='github_connected',
        label='Connect GitHub',
        met=_is_active_signal(gh),
        points=14,
    ))

    # Scholar OR Kaggle
    items.append(StrengthItem(
        key='scholar_or_kaggle',
        label='Connect Google Scholar or Kaggle',
        met=_is_active_signal(sc) or _is_active_signal(kg),
        points=10,
    ))

    # LinkedIn URL
    items.append(StrengthItem(
        key='has_linkedin',
        label='Add your LinkedIn URL',
        met=bool(getattr(profile, 'linkedin_url', None)),
        points=4,
    ))

    # Freshness — any connected signal with recent fetched_at
    any_fresh = any(
        _signal_is_fresh(s) and _is_active_signal(s)
        for s in (gh, sc, kg)
    )
    items.append(StrengthItem(
        key='signals_fresh',
        label='Refresh your external signals (older than 90 days)',
        met=any_fresh,
        points=7,
    ))

    score = sum(i['points'] for i in items if i['met'])
    return StrengthComponent(
        key='signals', label='External signals',
        score=score, max=35, items=items,
    )
```

- [ ] **Step 4: Run, expect all new tests PASS.**

- [ ] **Step 5: Commit.**

```bash
git add profiles/services/profile_strength.py profiles/tests.py
git commit -m "feat(profile): _score_signals — 35-pt component with freshness"
```

---

## Task 5: Tier + top-actions helpers

**Files:**
- Modify: `profiles/services/profile_strength.py`
- Test: `profiles/tests.py` — extend `ProfileStrengthTests`

- [ ] **Step 1: Write failing tests.**

```python
    def test_tier_thresholds_boundary_cases(self):
        from profiles.services.profile_strength import _tier
        self.assertEqual(_tier(0), 'Weak')
        self.assertEqual(_tier(34), 'Weak')
        self.assertEqual(_tier(35), 'Developing')
        self.assertEqual(_tier(59), 'Developing')
        self.assertEqual(_tier(60), 'Solid')
        self.assertEqual(_tier(79), 'Solid')
        self.assertEqual(_tier(80), 'Strong')
        self.assertEqual(_tier(100), 'Strong')

    def test_top_actions_returns_three_highest_point_unmet_items(self):
        from profiles.services.profile_strength import _top_actions, StrengthComponent, StrengthItem
        comps: list[StrengthComponent] = [
            {'key': 'completeness', 'label': 'C', 'score': 0, 'max': 35, 'items': [
                {'key': 'has_identity', 'label': 'Add name+email', 'met': False, 'points': 5},
                {'key': 'has_three_exps', 'label': 'Describe 3 experiences', 'met': False, 'points': 10},
                {'key': 'has_summary', 'label': 'Summary', 'met': True, 'points': 5},
            ]},
            {'key': 'signals', 'label': 'S', 'score': 0, 'max': 35, 'items': [
                {'key': 'github_connected', 'label': 'Connect GitHub', 'met': False, 'points': 14},
                {'key': 'has_linkedin', 'label': 'LinkedIn URL', 'met': False, 'points': 4},
            ]},
        ]
        actions = _top_actions(comps)
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0]['points'], 14)  # GitHub
        self.assertEqual(actions[1]['points'], 10)  # three exps
        self.assertEqual(actions[2]['points'], 5)   # identity
        # Label format includes points suffix
        self.assertIn('+14 points', actions[0]['label'])
        # href comes from the map
        self.assertEqual(actions[0]['href'], '/insights/')
        self.assertEqual(actions[1]['href'], '/profiles/setup/review/')

    def test_top_actions_stable_tiebreak_by_key(self):
        from profiles.services.profile_strength import _top_actions
        comps = [{
            'key': 'completeness', 'label': 'C', 'score': 0, 'max': 35,
            'items': [
                {'key': 'has_summary',     'label': 'Summary',   'met': False, 'points': 5},
                {'key': 'has_education',   'label': 'Education', 'met': False, 'points': 5},
                {'key': 'has_five_skills', 'label': 'Skills',    'met': False, 'points': 5},
            ],
        }]
        actions = _top_actions(comps)
        # Alphabetical tie-break on key
        self.assertEqual([a['label'].split(' · ')[0] for a in actions], ['Education', 'Skills', 'Summary'])

    def test_top_actions_empty_when_nothing_unmet(self):
        from profiles.services.profile_strength import _top_actions
        comps = [{
            'key': 'completeness', 'label': 'C', 'score': 35, 'max': 35,
            'items': [
                {'key': 'has_identity', 'label': 'Identity', 'met': True, 'points': 5},
            ],
        }]
        self.assertEqual(_top_actions(comps), [])
```

- [ ] **Step 2: Run, expect ImportError.**

- [ ] **Step 3: Implement `_tier` and `_top_actions`.** Add above `compute_profile_strength`:

```python
def _tier(score: int) -> Tier:
    if score >= 80:
        return 'Strong'
    if score >= 60:
        return 'Solid'
    if score >= 35:
        return 'Developing'
    return 'Weak'


def _top_actions(components: list[StrengthComponent]) -> list[StrengthAction]:
    """Flatten all unmet items, sort by points DESC then key ASC, take top 3.

    Labels are formatted as ``"<item label> · +<points> points"`` and
    routed via ``HREF_BY_KEY``.
    """
    unmet: list[StrengthItem] = []
    for comp in components:
        for item in comp.get('items') or []:
            if not item.get('met'):
                unmet.append(item)
    unmet.sort(key=lambda i: (-i['points'], i['key']))
    actions: list[StrengthAction] = []
    for item in unmet[:3]:
        actions.append(StrengthAction(
            label=f"{item['label']} · +{item['points']} points",
            href=HREF_BY_KEY.get(item['key'], '/profiles/setup/review/'),
            points=item['points'],
        ))
    return actions
```

- [ ] **Step 4: Run, expect PASS.**

- [ ] **Step 5: Commit.**

```bash
git add profiles/services/profile_strength.py profiles/tests.py
git commit -m "feat(profile): _tier thresholds + _top_actions helper"
```

---

## Task 6: Assemble `compute_profile_strength`

**Files:**
- Modify: `profiles/services/profile_strength.py`
- Test: `profiles/tests.py` — extend `ProfileStrengthTests`

- [ ] **Step 1: Write failing integration tests.**

```python
    def test_compute_empty_profile_scores_zero_weak(self):
        from profiles.services.profile_strength import compute_profile_strength
        profile = self._make_profile()
        s = compute_profile_strength(profile, self.user)
        self.assertEqual(s['score'], 0)
        self.assertEqual(s['tier'], 'Weak')
        self.assertEqual(len(s['components']), 3)
        self.assertEqual([c['key'] for c in s['components']], ['completeness', 'evidence', 'signals'])

    def test_compute_score_is_sum_of_component_scores(self):
        from profiles.services.profile_strength import compute_profile_strength
        profile = self._make_profile(
            full_name='J', email='j@e.com',
            data_content={
                'skills': [{'name': s} for s in ['A', 'B', 'C', 'D', 'E']],
                'github_signals': {
                    'public_repos': 3,
                    'fetched_at': '2026-04-10T00:00:00Z',
                },
            },
        )
        s = compute_profile_strength(profile, self.user)
        comp_scores = {c['key']: c['score'] for c in s['components']}
        self.assertEqual(s['score'], sum(comp_scores.values()))
        self.assertIn('completeness', comp_scores)
        self.assertIn('evidence', comp_scores)
        self.assertIn('signals', comp_scores)

    def test_compute_top_actions_present_when_gaps_exist(self):
        from profiles.services.profile_strength import compute_profile_strength
        profile = self._make_profile(full_name='J', email='j@e.com')  # mostly empty
        s = compute_profile_strength(profile, self.user)
        self.assertGreater(len(s['top_actions']), 0)
        self.assertLessEqual(len(s['top_actions']), 3)
        for a in s['top_actions']:
            self.assertIn('href', a)
            self.assertIn('label', a)
            self.assertIn('points', a)
```

- [ ] **Step 2: Run, confirm failures (function still returns stub).**

- [ ] **Step 3: Replace the stub body of `compute_profile_strength` with the real assembly:**

In `profiles/services/profile_strength.py`, replace:

```python
def compute_profile_strength(profile, user) -> ProfileStrength:
    """Top-level entry point — stub until subsequent tasks wire up components."""
    return ProfileStrength(score=0, tier='Weak', components=[], top_actions=[])
```

with:

```python
def compute_profile_strength(profile, user) -> ProfileStrength:
    """Compute 0-100 score, tier label, component breakdown, and top-3 CTAs.

    Pure — no DB writes, no caching. ``user`` is accepted for forward
    compatibility (future signals may depend on it) but is not currently read.
    """
    components = [
        _score_completeness(profile),
        _score_evidence(profile),
        _score_signals(profile),
    ]
    score = sum(c['score'] for c in components)
    return ProfileStrength(
        score=score,
        tier=_tier(score),
        components=components,
        top_actions=_top_actions(components),
    )
```

- [ ] **Step 4: Run ALL ProfileStrengthTests.**

`python manage.py test profiles.tests.ProfileStrengthTests -v 2`

Expected: all tests PASS.

- [ ] **Step 5: Commit.**

```bash
git add profiles/services/profile_strength.py profiles/tests.py
git commit -m "feat(profile): compute_profile_strength — assembly + top actions"
```

---

## Task 7: Dashboard view integration

**Files:**
- Modify: `profiles/views.py` — function `dashboard` (around line 444)
- Test: `profiles/tests.py` — extend `ProfileStrengthTests`

- [ ] **Step 1: Write failing view test.**

```python
    def test_dashboard_view_includes_profile_strength_in_context(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        self._make_profile(full_name='Jane', email='jane@e.com')
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('profile_strength', resp.context)
        ps = resp.context['profile_strength']
        self.assertIn('score', ps)
        self.assertIn('tier', ps)
        self.assertIn('components', ps)
        self.assertIn('top_actions', ps)
```

- [ ] **Step 2: Run, confirm the assertion `'profile_strength' in resp.context` fails.**

- [ ] **Step 3: Wire the score into the dashboard view.** Open `profiles/views.py`. Find the `dashboard` view (around line 444). Locate the line where `career_stage` is computed (around line 486):

```python
    career_stage = detect_stage_for_dashboard(profile, kanban_boards)
```

Immediately below that line, add:

```python
    from profiles.services.profile_strength import compute_profile_strength
    profile_strength = compute_profile_strength(profile, request.user)
```

Then locate the `render(...)` / context dict (around line 498) that contains `'career_stage': career_stage,`. Add `'profile_strength': profile_strength,` as a new entry in that dict, immediately after the `career_stage` entry.

- [ ] **Step 4: Run the test. Expect PASS.**

- [ ] **Step 5: Commit.**

```bash
git add profiles/views.py profiles/tests.py
git commit -m "feat(profile): dashboard view injects profile_strength"
```

---

## Task 8: Insights view integration

**Files:**
- Modify: `core/views.py` — function `insights_view`
- Test: `core/tests.py` — new class `InsightsViewProfileStrengthTests`

- [ ] **Step 1: Write failing view test.** Append to `core/tests.py`:

```python
class InsightsViewProfileStrengthTests(TestCase):
    """/insights/ includes profile_strength in its template context."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='iv@example.com', email='iv@example.com', password='x'
        )
        self.client.force_login(self.user)

    def test_insights_view_injects_profile_strength(self):
        from profiles.models import UserProfile
        UserProfile.objects.create(user=self.user, full_name='J', email='j@e.com')
        resp = self.client.get(reverse('insights'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('profile_strength', resp.context)
        ps = resp.context['profile_strength']
        self.assertIn('score', ps)
        self.assertIn('top_actions', ps)
```

- [ ] **Step 2: Run. Confirm failure.**

- [ ] **Step 3: Wire the score into `insights_view`.** Open `core/views.py`. Locate `def insights_view(request)` (around line 132). Find the existing line near the top of the function body:

```python
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
```

Immediately after that line, add:

```python
    from profiles.services.profile_strength import compute_profile_strength
    profile_strength = compute_profile_strength(profile, request.user)
```

Then find the `return render(request, 'core/insights.html', { ... })` at the end. Add `'profile_strength': profile_strength,` to the context dict (alongside `'evidence': evidence,`).

- [ ] **Step 4: Run the test. Expect PASS.**

- [ ] **Step 5: Commit.**

```bash
git add core/views.py core/tests.py
git commit -m "feat(profile): insights view injects profile_strength"
```

---

## Task 9: Dashboard ring partial + include

**Files:**
- Create: `templates/components/profile_strength_ring.html`
- Modify: `templates/profiles/dashboard.html` — include the partial
- Test: `profiles/tests.py` — template-content assertion

- [ ] **Step 1: Write failing template-content test.** Append to `ProfileStrengthTests`:

```python
    def test_dashboard_renders_profile_strength_ring(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        self._make_profile(full_name='Jane', email='jane@e.com')
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        # Score badge / data attribute visible on page
        self.assertContains(resp, 'data-profile-strength')
        # Tier label appears (new empty-ish profile = Weak)
        self.assertContains(resp, 'Weak')
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Create the ring partial.** Write `templates/components/profile_strength_ring.html`:

```html
{# Compact profile-strength ring — dashboard hero sidebar.
   Requires: profile_strength (dict with score, tier, top_actions).
   Fail-closed: if profile_strength is missing or None, renders nothing. #}
{% if profile_strength %}
<a href="{% url 'insights' %}#profile-strength"
   data-profile-strength="{{ profile_strength.score }}"
   class="block rounded-2xl bg-white dark:bg-neutral-900 ring-1 ring-neutral-200 dark:ring-neutral-800 p-4 hover:ring-brand-300 dark:hover:ring-brand-700 transition-all">
    <div class="flex items-center gap-3">
        {# SVG ring #}
        <span class="relative flex items-center justify-center w-14 h-14">
            <svg viewBox="0 0 36 36" class="w-14 h-14 -rotate-90">
                <circle cx="18" cy="18" r="15.9155"
                        fill="none" stroke-width="3"
                        class="stroke-neutral-200 dark:stroke-neutral-800"></circle>
                <circle cx="18" cy="18" r="15.9155"
                        fill="none" stroke-width="3" stroke-linecap="round"
                        stroke-dasharray="{{ profile_strength.score }},100"
                        class="stroke-brand-600 dark:stroke-brand-400"></circle>
            </svg>
            <span class="absolute text-sm font-semibold text-neutral-900 dark:text-neutral-50"
                  x-text="{{ profile_strength.score }}">{{ profile_strength.score }}</span>
        </span>
        <div class="min-w-0">
            <div class="text-[11px] uppercase tracking-[0.14em] font-medium text-neutral-500 dark:text-neutral-400">
                Profile strength
            </div>
            <div class="flex items-baseline gap-2 mt-0.5">
                <span class="font-display text-lg leading-none text-neutral-900 dark:text-neutral-50"
                      style="font-variation-settings: 'opsz' 20;">{{ profile_strength.tier }}</span>
            </div>
            {% if profile_strength.top_actions %}
            <p class="mt-1 text-[11px] text-neutral-500 dark:text-neutral-400 truncate">
                {{ profile_strength.top_actions.0.label }}
            </p>
            {% endif %}
        </div>
    </div>
</a>
{% endif %}
```

- [ ] **Step 4: Include the ring in the dashboard template.**

Open `templates/profiles/dashboard.html`. Near the top of the hero area — anywhere inside the hero layout where a sidebar card makes sense — insert:

```django
{% include "components/profile_strength_ring.html" %}
```

If the dashboard hero has a right-side sidebar column, place it at the top of that column. If no sidebar exists, place it immediately after the existing stage-based hero card. Use your judgment on exact placement — the partial is self-contained and works anywhere inside the hero.

- [ ] **Step 5: Rebuild Tailwind (picks up any new class tokens used in the partial).**

```bash
npm run build:css
```

- [ ] **Step 6: Run the test, expect PASS.**

- [ ] **Step 7: Commit.**

```bash
git add templates/components/profile_strength_ring.html templates/profiles/dashboard.html static/css/output.css profiles/tests.py
git commit -m "feat(profile): dashboard ring partial for profile strength"
```

---

## Task 10: Insights breakdown partial + include

**Files:**
- Create: `templates/components/profile_strength_breakdown.html`
- Modify: `templates/core/insights.html` — include the partial
- Test: `core/tests.py` — template-content assertion inside `InsightsViewProfileStrengthTests`

- [ ] **Step 1: Write failing template-content test.** Append to `InsightsViewProfileStrengthTests`:

```python
    def test_insights_renders_profile_strength_breakdown(self):
        from profiles.models import UserProfile
        UserProfile.objects.create(
            user=self.user, full_name='J', email='j@e.com',
            data_content={'skills': [{'name': s} for s in ['A', 'B', 'C', 'D', 'E']]},
        )
        resp = self.client.get(reverse('insights'))
        # Anchor for the hash deep-link from the dashboard ring
        self.assertContains(resp, 'id="profile-strength"')
        # All three component labels render
        self.assertContains(resp, 'Completeness')
        self.assertContains(resp, 'Evidence depth')
        self.assertContains(resp, 'External signals')
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Create the breakdown partial.** Write `templates/components/profile_strength_breakdown.html`:

```html
{# Full profile-strength breakdown — /insights/ page.
   Requires: profile_strength. Fail-closed if missing. #}
{% if profile_strength %}
<section id="profile-strength"
         class="rounded-2xl bg-white dark:bg-neutral-900 ring-1 ring-neutral-200 dark:ring-neutral-800 p-6"
         x-data="{ expanded: false }">

    <header class="flex items-start justify-between gap-4">
        <div>
            <div class="text-[11px] uppercase tracking-[0.14em] font-medium text-neutral-500 dark:text-neutral-400">
                Profile strength
            </div>
            <div class="mt-1 flex items-baseline gap-3">
                <span class="font-display text-4xl leading-none text-neutral-900 dark:text-neutral-50"
                      style="font-variation-settings: 'opsz' 48;">{{ profile_strength.score }}</span>
                <span class="px-2 py-0.5 rounded-full text-[11px] font-medium
                             bg-brand-50 dark:bg-brand-950/40 text-brand-800 dark:text-brand-200 ring-1 ring-brand-200 dark:ring-brand-800">
                    {{ profile_strength.tier }}
                </span>
            </div>
        </div>
        <button type="button" @click="expanded = !expanded"
                class="text-[11px] uppercase tracking-[0.14em] font-medium text-neutral-500 dark:text-neutral-400 hover:text-brand-700 dark:hover:text-brand-400">
            <span x-text="expanded ? 'Hide details' : 'See details'"></span>
        </button>
    </header>

    {# Three component bars #}
    <div class="mt-6 space-y-3">
        {% for comp in profile_strength.components %}
        <div>
            <div class="flex items-baseline justify-between text-xs">
                <span class="font-medium text-neutral-800 dark:text-neutral-200">{{ comp.label }}</span>
                <span class="text-neutral-500 dark:text-neutral-400">{{ comp.score }} / {{ comp.max }}</span>
            </div>
            <div class="mt-1 h-1.5 rounded-full bg-neutral-100 dark:bg-neutral-800 overflow-hidden">
                <div class="h-full bg-brand-600 dark:bg-brand-400 rounded-full"
                     style="width: {% widthratio comp.score comp.max 100 %}%;"></div>
            </div>
        </div>
        {% endfor %}
    </div>

    {# Expanded per-item list #}
    <div x-show="expanded" x-transition class="mt-6 space-y-4 border-t border-neutral-200 dark:border-neutral-800 pt-5">
        {% for comp in profile_strength.components %}
        <div>
            <div class="text-[11px] uppercase tracking-[0.14em] font-medium text-neutral-500 dark:text-neutral-400">
                {{ comp.label }}
            </div>
            <ul class="mt-2 space-y-1">
                {% for item in comp.items %}
                <li class="flex items-center gap-2 text-sm {% if item.met %}text-neutral-400 dark:text-neutral-500 line-through{% else %}text-neutral-800 dark:text-neutral-200{% endif %}">
                    <span aria-hidden="true" class="w-1.5 h-1.5 rounded-full {% if item.met %}bg-emerald-500{% else %}bg-neutral-300 dark:bg-neutral-700{% endif %}"></span>
                    <span>{{ item.label }}</span>
                    {% if not item.met %}
                    <span class="ml-auto text-[11px] text-neutral-500 dark:text-neutral-400">+{{ item.points }}</span>
                    {% endif %}
                </li>
                {% endfor %}
            </ul>
        </div>
        {% endfor %}
    </div>

    {# Top 3 CTA chips #}
    {% if profile_strength.top_actions %}
    <div class="mt-6 flex flex-wrap gap-2 border-t border-neutral-200 dark:border-neutral-800 pt-5">
        {% for action in profile_strength.top_actions %}
        <a href="{{ action.href }}"
           class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-brand-50 dark:bg-brand-950/40 text-brand-800 dark:text-brand-200 ring-1 ring-brand-200 dark:ring-brand-800 hover:bg-brand-100 dark:hover:bg-brand-900/60 transition-colors">
            {{ action.label }}
        </a>
        {% endfor %}
    </div>
    {% endif %}
</section>
{% endif %}
```

- [ ] **Step 4: Include the partial in `templates/core/insights.html`.**

Open `templates/core/insights.html`. Find a natural place near the top of the main content area (e.g., above the evidence-confidence tile). Insert:

```django
{% include "components/profile_strength_breakdown.html" %}
```

If unsure about placement, put it immediately after the page `<h1>` / hero.

- [ ] **Step 5: Rebuild Tailwind.**

```bash
npm run build:css
```

- [ ] **Step 6: Run the test. Expect PASS.**

- [ ] **Step 7: Commit.**

```bash
git add templates/components/profile_strength_breakdown.html templates/core/insights.html static/css/output.css core/tests.py
git commit -m "feat(profile): insights breakdown partial with component bars and CTAs"
```

---

## Task 11: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite.**

`python manage.py test -v 1`

Expected: ~193 tests PASS (up from 177), 0 failures.

- [ ] **Step 2: Manual smoke test.**

Start dev server: `python manage.py runserver`

In a browser:
- Visit `/profiles/dashboard/` — ring visible in the hero area with score, tier, and a nudge line. Clicking the ring navigates to `/insights/#profile-strength`.
- On `/insights/`, the breakdown card is visible near the top with:
  - Big score and tier badge
  - Three component bars (Completeness, Evidence depth, External signals) each showing `score / max`
  - "See details" button — clicking expands the per-item list; each unmet item has a `+N` points badge
  - Up to 3 CTA chips at the bottom — clickable
- Click a CTA chip — lands on the target (`/profiles/setup/review/` or `/insights/` depending on key)

- [ ] **Step 3: Commit any trailing output.css diff if needed.**

```bash
git status
# If static/css/output.css has uncommitted changes:
git add static/css/output.css
git commit -m "chore(css): rebuild tailwind after profile strength templates"
```

- [ ] **Step 4: Final all-clear.**

`git status`
Expected: working tree clean.

---

## Verification checklist (spec coverage)

- [x] `compute_profile_strength(profile, user) -> ProfileStrength` — Task 6
- [x] Three components, weighted 35/30/35 — Tasks 2-4
- [x] Tier thresholds Weak/Developing/Solid/Strong at 0/35/60/80 — Task 5
- [x] Top-3 actions, sorted by points desc then key asc — Task 5
- [x] HREF_BY_KEY map covering all 14 item keys — Task 1
- [x] Dashboard compact ring — Tasks 7, 9
- [x] `/insights/` full breakdown with expand toggle and CTA chips — Tasks 8, 10
- [x] Edge cases (no profile, empty data_content, errored signals) covered by unit tests — Tasks 2-4, 6
- [x] No caching, no persistence, no history — by construction (pure function)
- [x] Full regression — Task 11
