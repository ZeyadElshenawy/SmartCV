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


def compute_profile_strength(profile, user) -> ProfileStrength:
    """Top-level entry point — stub until subsequent tasks wire up components."""
    return ProfileStrength(score=0, tier='Weak', components=[], top_actions=[])
