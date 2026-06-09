"""Presentation-only template helpers for the ATS slide-over panel.

`cards_of_kind` filters the UNCHANGED ``build_ats_cards`` output for display —
it groups the same card dicts into Quick wins / Add evidence / Watch-outs. It
touches no scoring, card, or endpoint logic and never mutates a card; it only
re-presents what the server already produced.
"""
from django import template

register = template.Library()


@register.filter
def cards_of_kind(cards, kind):
    """Return the subset of *cards* whose ``kind`` equals *kind* (display only)."""
    if not cards:
        return []
    return [c for c in cards if isinstance(c, dict) and c.get("kind") == kind]
