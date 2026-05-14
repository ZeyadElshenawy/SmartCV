"""Sort projects newest-first on the master profile.

The master-profile `projects` list comes from multiple sources — typed by
the user, parsed out of the CV, enriched from GitHub (with `pushed_at`),
LinkedIn (with `duration` strings like "Jan 2024 - Dec 2024"), Scholar,
Kaggle. Each source carries dates in a different shape and some not at
all, so we extract a best-effort `(year, month)` tuple from any
date-bearing field and sort descending.

Applied at three points:
  - `auto_apply_enriched_projects` after every dedupe pass (canonical).
  - Manual-form POST handler so user-added projects also land in order.
  - `_build_profile_form_context` as a defensive read-time sort for any
    legacy list that pre-dates this module.

Projects with no extractable year fall to the bottom — that matches the
user's intent (newest first, undateable entries last) and is stable.
"""
from __future__ import annotations

import re
from typing import Any

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

# Month name → number, used to break year ties when both LinkedIn-style
# "Aug 2025" and bare "2025" appear in the same field.
_MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
_MONTH_NEAR_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+((?:19|20)\d{2})\b",
    re.IGNORECASE,
)


def _extract_latest_date(text: Any) -> tuple[int, int]:
    """Pull the latest plausible `(year, month)` from arbitrary text.

    Returns `(0, 0)` if no year is present so undateable projects sort
    last. Month is 0 when no month appears near the year (still sortable;
    same-year projects with months land above same-year-no-month).

    Also accepts an ISO-ish prefix like `2026-04-01T00:00:00Z` (the GitHub
    `pushed_at` shape) — the year + month components are still extracted
    by the regex.
    """
    if not text:
        return (0, 0)
    s = str(text)
    years = _YEAR_RE.findall(s)
    if not years:
        return (0, 0)
    latest_year = max(int(y) for y in years)
    latest_month = 0
    for match in _MONTH_NEAR_YEAR_RE.finditer(s):
        m_name, m_year = match.group(1).lower(), int(match.group(2))
        if m_year == latest_year:
            latest_month = max(latest_month, _MONTHS[m_name[:3]])
    # ISO date like "2026-04-01" — pull the month directly when the year
    # matches the latest.
    for m in re.finditer(rf"\b{latest_year}-(\d{{2}})-\d{{2}}", s):
        latest_month = max(latest_month, int(m.group(1)))
    return (latest_year, latest_month)


# Date-bearing fields in roughly descending order of trust.
_DATE_FIELDS = ('pushed_at', 'end_date', 'date', 'year', 'duration', 'start_date')
# Free-text fields where a date might be mentioned in prose.
_TEXT_FIELDS = ('description', 'highlights', 'bullets', 'name', 'summary')


def project_sort_key(project: Any) -> tuple[int, int]:
    """Sort key for a single project. Pair with `reverse=True` for
    newest-first ordering."""
    if not isinstance(project, dict):
        return (0, 0)
    for field in _DATE_FIELDS:
        val = project.get(field)
        if not val:
            continue
        key = _extract_latest_date(val)
        if key != (0, 0):
            return key
    parts: list[str] = []
    for field in _TEXT_FIELDS:
        val = project.get(field)
        if isinstance(val, list):
            parts.extend(str(x) for x in val if x)
        elif val:
            parts.append(str(val))
    return _extract_latest_date(" ".join(parts))


def sort_projects_newest_first(projects: list[dict]) -> list[dict]:
    """Return a new list of projects ordered newest→oldest. Stable for
    ties (projects with the same extracted date keep their input order)."""
    if not projects:
        return []
    return sorted(projects, key=project_sort_key, reverse=True)


def backfill_github_dates(projects: list[dict], data_content: dict) -> list[dict]:
    """Fill in `pushed_at` on GitHub-source projects by matching `source_id`
    (the repo's `full_name`) against `github_signals['top_repos']`. The
    enricher doesn't preserve the timestamp on its own — without this step
    every GitHub project ends up undateable and sinks to the bottom of the
    sort.

    Mutates in place AND returns the list for caller convenience.
    """
    gh = (data_content or {}).get('github_signals') or {}
    repos = gh.get('top_repos') or []
    by_full = {r.get('full_name'): r.get('pushed_at') for r in repos
               if isinstance(r, dict) and r.get('full_name')}
    if not by_full:
        return projects
    for p in projects:
        if not isinstance(p, dict):
            continue
        if p.get('source') != 'github' or p.get('pushed_at'):
            continue
        pushed = by_full.get(p.get('source_id'))
        if pushed:
            p['pushed_at'] = pushed
    return projects
