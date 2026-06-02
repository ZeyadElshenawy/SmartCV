"""Canonical banned-bullet-opening verbs — the SINGLE SOURCE OF TRUTH.

Consumed by three places that previously held drifted copies:

  1. ``resume_generator_v2._BULLET_QUALITY_RULES`` (the per-bullet
     prompt) — formats the canonical set into the LLM instruction so
     first-attempt generation avoids these verbs.
  2. ``resume_reviewer_v2._scan_bullet`` (the deterministic post-gen
     scan) — flags any bullet whose first word matches one of the
     canonical openings.
  3. ``resume_reviewer_v2._apply_regen_round`` (the regen feedback) —
     names the full canonical set in the REGENERATION instruction so
     the LLM doesn't swap one banned opener for another (e.g. the
     ``Utilized → Leveraged`` swap caught in the v2 smoke run).

Adding or removing a verb here updates all three consumers at once.
Match is case-insensitive against the bullet's first token(s), after
stripping leading whitespace / punctuation / bullet glyphs.

The set encodes the project's bullet-quality philosophy:
  - "duty framing" verbs (Helped, Worked on, Assisted with,
    Contributed to, Tasked with, In charge of, Duties included) tell
    the recruiter what you were SUPPOSED to do, not what you
    accomplished.
  - "credit-seeking" verbs (Spearheaded, Utilized, Leveraged,
    Crafted) are the recognizable AI-tell openers Jobscan's audits
    flag in 5 seconds.
"""
from __future__ import annotations

import re
from typing import Optional


BANNED_OPENINGS: tuple[str, ...] = (
    # Generic AI-tell verbs — recruiters spot these instantly.
    "utilized",
    "utilised",          # UK spelling
    "leveraged",
    "spearheaded",
    "crafted",
    # Duty-framing — tells the recruiter your job description, not
    # what you accomplished.
    "helped",
    "worked on",
    "assisted with",
    "contributed to",
    "tasked with",
    "in charge of",
    "duties included",
    "was responsible for",
    "responsible for",
)


# Regex to strip leading non-letter characters (whitespace, bullet
# glyphs, opening quotes, stray punctuation) before testing against
# the canonical set. Matches what the LLM occasionally emits as
# prefix.
_LEADING_NOISE_RE = re.compile(r"^[\s\"'•\-\*\(]+")


def find_banned_opening(text: str) -> Optional[str]:
    """Return the canonical banned-opening token a bullet starts with,
    or ``None`` if the opening is clean. Match is case-insensitive on
    the cleaned text. Multi-word openings ("worked on", "responsible
    for") are tested as prefixes.
    """
    if not isinstance(text, str):
        return None
    cleaned = _LEADING_NOISE_RE.sub("", text).lower()
    for banned in BANNED_OPENINGS:
        if cleaned.startswith(banned):
            # Ensure we matched a full word boundary so "utilized"
            # doesn't false-match "utilization" (no current entry has
            # this issue, but the guard is cheap).
            tail_idx = len(banned)
            if tail_idx == len(cleaned) or not cleaned[tail_idx].isalpha():
                return banned
    return None


def format_banned_openings_for_prompt() -> str:
    """Render the canonical set as a Title-Cased, slash-separated
    string suitable for embedding in the LLM bullet-quality prompt.

    Example output::

        "Utilized / Utilised / Leveraged / Spearheaded / Crafted /
         Helped / Worked on / Assisted with / Contributed to /
         Tasked with / In charge of / Duties included / Was responsible for /
         Responsible for"
    """
    return " / ".join(b[:1].upper() + b[1:] for b in BANNED_OPENINGS)
