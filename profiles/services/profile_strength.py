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
