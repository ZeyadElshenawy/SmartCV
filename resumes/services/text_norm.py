"""Shared text-normalisation helper.

A single ``norm()`` implementation reused by:
  - resume_v2_adapter.py (entity-match chain — title/company normalisation)
  - resume_planner_v2.py (strength-gate anchor-restatement check)

Kept tiny and behavioural-stable on purpose. Existing callers depend on
the exact transform: lowercase, internal whitespace collapsed to single
spaces, leading/trailing whitespace stripped, ``None`` / non-strings
returning ``""``. Do not add Unicode normalisation, punctuation
stripping, or stemming here — those belong in caller-specific helpers
that wrap this one.
"""
from __future__ import annotations

import re


def norm(s) -> str:
    """Normalise a string for comparison — lowercase, whitespace collapsed,
    leading/trailing whitespace stripped. ``None`` / non-strings → ``""``."""
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s.lower()).strip()
