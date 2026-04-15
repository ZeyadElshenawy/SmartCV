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


def _score_completeness(profile) -> StrengthComponent:
    """35-point breakdown: identity, experiences, education, skills, summary, contact."""
    data = profile.data_content or {}
    experiences = data.get('experiences') or []
    education = data.get('education') or []
    skills = data.get('skills') or []
    summary = (data.get('summary') or '').strip()

    items: list[StrengthItem] = []

    items.append(StrengthItem(
        key='has_identity',
        label='Add your name and email',
        met=bool(profile.full_name) and bool(profile.email),
        points=5,
    ))

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

    items.append(StrengthItem(
        key='has_education',
        label='Add at least one education entry',
        met=len(education) >= 1,
        points=5,
    ))

    items.append(StrengthItem(
        key='has_five_skills',
        label='List at least 5 skills',
        met=len(skills) >= 5,
        points=5,
    ))

    items.append(StrengthItem(
        key='has_summary',
        label='Write a professional summary',
        met=len(summary) >= 40,
        points=5,
    ))

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


def _score_evidence(profile) -> StrengthComponent:
    """30-point breakdown: description richness, projects, credentials, quantification."""
    data = profile.data_content or {}
    experiences = [e for e in (data.get('experiences') or []) if isinstance(e, dict)]
    projects = [p for p in (data.get('projects') or []) if isinstance(p, dict)]
    certifications = data.get('certifications') or []
    publications = data.get('publications') or []
    awards = data.get('awards') or []

    items: list[StrengthItem] = []

    descs = [(e.get('description') or '').strip() for e in experiences[:5]]
    avg_len = (sum(len(d) for d in descs) / len(descs)) if descs else 0
    items.append(StrengthItem(
        key='descriptions_rich',
        label='Flesh out experience descriptions (≥150 chars each)',
        met=avg_len >= 150,
        points=10,
    ))

    items.append(StrengthItem(
        key='has_project',
        label='Add at least one described project',
        met=any((p.get('description') or '').strip() for p in projects),
        points=6,
    ))

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


def _is_active_signal(signal: dict) -> bool:
    """A signal is 'active' when it's a dict, has no error, and shows activity."""
    if not isinstance(signal, dict) or signal.get('error'):
        return False
    if 'public_repos' in signal:
        return (signal.get('public_repos') or 0) > 0
    if 'total_citations' in signal or 'top_publications' in signal:
        return (signal.get('total_citations') or 0) > 0 or bool(signal.get('top_publications'))
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

    items.append(StrengthItem(
        key='github_connected',
        label='Connect GitHub',
        met=_is_active_signal(gh),
        points=14,
    ))

    items.append(StrengthItem(
        key='scholar_or_kaggle',
        label='Connect Google Scholar or Kaggle',
        met=_is_active_signal(sc) or _is_active_signal(kg),
        points=10,
    ))

    items.append(StrengthItem(
        key='has_linkedin',
        label='Add your LinkedIn URL',
        met=bool(getattr(profile, 'linkedin_url', None)),
        points=4,
    ))

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


def compute_profile_strength(profile, user) -> ProfileStrength:
    """Top-level entry point — stub until subsequent tasks wire up components."""
    return ProfileStrength(score=0, tier='Weak', components=[], top_actions=[])
