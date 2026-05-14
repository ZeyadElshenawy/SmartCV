"""Final-pass polish for `data_content['projects']` bullets.

Defends against three patterns that kept slipping into output even with
prompt fixes upstream:

1. **GitHub-metadata filler**: "garnering 1 star on GitHub", "with 0
   stars on GitHub", "earned 2 stars". The enricher prompt was rewritten
   to ban this, but cached LLM output and any future prompt regression
   would still surface them. Final-pass regex strip catches all variants.

2. **Filler flourishes**: "showcasing expertise in", "enabling insights
   into", "leveraging", "utilizing", "cutting-edge", etc. Marketing
   adjectives that read as AI filler and add no signal.

3. **Bullet trim + null-field cleanup**: cap each project at 3 bullets,
   drop empty-list / empty-string / None values that clutter the JSON.

Runs at the end of the Stage 3 assembly in `rebuild_master_profile`,
after dedupe and date-sort. Idempotent — running it twice produces the
same output the second time.
"""
from __future__ import annotations

import re
from typing import Any

# Match phrases like:
#   ", with 1 star on GitHub."
#   "; 12 stars, 3 forks on GitHub"
#   " — garnering 50 stars on GitHub"
# anywhere in a bullet. Captures trailing/leading punctuation so the
# surviving sentence reads cleanly.
_GH_METADATA_PATTERNS = [
    re.compile(
        r"[\s,;.\-—]*"
        r"(?:with|garnering|earning|earned|featuring)?\s*"
        r"\d+\s+stars?(?:\s*(?:and|,)\s*\d+\s+forks?)?"
        r"\s*on\s*GitHub\b\.?",
        re.IGNORECASE,
    ),
    re.compile(r"[\s,;.\-—]*\b\d+\s+stars?,\s*\d+\s+forks?\s+on\s+GitHub\b\.?", re.IGNORECASE),
    re.compile(r"[\s,;.\-—]*\bwith\s+\d+\s+stars?\b\.?", re.IGNORECASE),
]

# Drop these as standalone phrases. Each is wrapped in word-boundary +
# optional leading/trailing connectives so the surrounding bullet stays
# grammatical.
_FILLER_PHRASES = [
    r"\s*,?\s*showcasing\s+(?:expertise|proficiency|skills?)\s+in\s+[^.]*",
    r"\s*,?\s*demonstrating\s+(?:expertise|proficiency)\s+in\s+[^.]*",
    r"\s*,?\s*enabling\s+insights?\s+into\s+[^.]*",
    r"\s*,?\s*for\s+an\s+interactive\s+user\s+experience\b",
    r"\s*,?\s*leveraging\s+",  # leading filler verb; keep object
    r"\s*,?\s*utilizing\s+",
    r"\s*,?\s*cutting[\s-]edge\b",
    r"\s*,?\s*best[\s-]in[\s-]class\b",
]
_FILLER_RE = re.compile("|".join(_FILLER_PHRASES), re.IGNORECASE)

# Final-line punctuation tidy after the strips. Collapses ", ." / " ,"
# / double spaces into clean output.
_TIDY_PATTERNS = [
    (re.compile(r"\s*,\s*\."), "."),
    (re.compile(r"\s*\.\s*,"), ","),
    (re.compile(r"\s*,\s*,"), ","),
    (re.compile(r"\s{2,}"), " "),
    (re.compile(r"\s+\."), "."),
    (re.compile(r"\s+,"), ","),
    # If the strip left a trailing connective ("…using OpenCV and ."),
    # drop the orphan "and" / "with" / "using" at the very end.
    (re.compile(r"\b(?:and|with|using|including)\s*\.\s*$", re.IGNORECASE), "."),
]

_MAX_BULLETS = 3


def strip_github_metadata_filler(bullet: str) -> str:
    """Remove "N star(s) on GitHub" -style filler from a single bullet."""
    if not bullet:
        return bullet
    text = bullet
    for pattern in _GH_METADATA_PATTERNS:
        text = pattern.sub("", text)
    return _tidy(text)


def strip_filler_phrases(bullet: str) -> str:
    """Remove generic AI-filler flourishes from a single bullet."""
    if not bullet:
        return bullet
    text = _FILLER_RE.sub(" ", bullet)
    return _tidy(text)


def _tidy(text: str) -> str:
    text = text.strip()
    for pattern, replacement in _TIDY_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip()


def polish_bullet(bullet: Any) -> str:
    """Run every strip + tidy step on a single bullet. Returns a string
    (empty if everything got stripped away — caller drops empties)."""
    if bullet is None:
        return ""
    if not isinstance(bullet, str):
        bullet = str(bullet)
    bullet = strip_github_metadata_filler(bullet)
    bullet = strip_filler_phrases(bullet)
    return bullet.strip()


def polish_projects(projects: list[dict]) -> list[dict]:
    """Run the full polish pass over a project list.

    For each project:
      - Polish each bullet (strip filler + GitHub metadata).
      - Drop empty bullets and second-pass dedupe identical strings.
      - Cap at 3 bullets — resume best practice.
      - Drop fields whose value is None / empty string / empty list.
    """
    if not projects:
        return projects or []
    out: list[dict] = []
    for project in projects:
        if not isinstance(project, dict):
            out.append(project)
            continue
        cleaned = dict(project)
        # `description` is the bullet list. May be a string in legacy CV
        # entries; normalize first.
        desc = cleaned.get('description')
        if isinstance(desc, str):
            bullets = [b.strip() for b in desc.split('\n') if b.strip()]
        elif isinstance(desc, list):
            bullets = list(desc)
        else:
            bullets = []

        polished: list[str] = []
        seen_lower: set[str] = set()
        for b in bullets:
            p = polish_bullet(b)
            if not p:
                continue
            key = p.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            polished.append(p)
            if len(polished) >= _MAX_BULLETS:
                break

        if polished:
            cleaned['description'] = polished
        else:
            cleaned.pop('description', None)

        # Drop null / empty fields. Done after bullet polish so we don't
        # strip the `description` key just because it was temporarily
        # empty mid-pass.
        cleaned = {
            k: v for k, v in cleaned.items()
            if v is not None and v != "" and v != [] and v != {}
        }
        out.append(cleaned)
    return out
