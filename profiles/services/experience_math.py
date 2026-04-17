"""Compute total years of experience from JSONB `experiences` data.

Replaces an earlier inline regex-year subtraction in profiles.views that
over-counted freelance/intern résumés:
  - "Aug 2025" with no end date was treated as ongoing (+1 year to today)
  - "Jul 2024" with no end date was treated as ongoing (+2 years to today)
  -> a CV with 2 single-month internships reported 3 years.

Design:
  - Month-precision date parsing (regex-only, no external deps).
  - Empty end date  -> single-month entry (end = start).
  - "Present"/"Current"/"Now"/"Ongoing" keyword -> credit up to today.
  - Overlapping intervals merged so concurrent jobs don't double-count.
  - Final result floored to whole years for display.

Public API:
  compute_years_of_experience(experiences, today=None) -> int
"""
from __future__ import annotations

import datetime
import logging
import re

logger = logging.getLogger(__name__)


_MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

_MONTH_NAME_RE = re.compile(
    r'\b(' + '|'.join(sorted(_MONTH_NAMES, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r'\b(19|20)\d{2}\b')
_ONGOING_KEYWORDS = ('present', 'current', 'ongoing', 'now', 'to date', 'today')


def _parse_date(value) -> datetime.date | None:
    """Parse a free-text date string to the 1st of the matched month.

    Returns None if no 4-digit year (1900-2099) can be extracted.
    Falls back to January if the string has a year but no month.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    year_match = _YEAR_RE.search(s)
    if not year_match:
        return None
    year = int(year_match.group(0))

    month = 1
    name_match = _MONTH_NAME_RE.search(s)
    if name_match:
        month = _MONTH_NAMES[name_match.group(1).lower()]
    else:
        # Numeric month forms: "2020-05", "05/2020", "05.2020". Strip the
        # year first so the year's trailing digits don't get picked up.
        residual = s.replace(str(year), ' ', 1)
        num_match = re.search(r'(?<!\d)(\d{1,2})(?!\d)', residual)
        if num_match:
            candidate = int(num_match.group(1))
            if 1 <= candidate <= 12:
                month = candidate

    try:
        return datetime.date(year, month, 1)
    except ValueError:
        return None


def _is_ongoing(value) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    return any(kw in s for kw in _ONGOING_KEYWORDS)


def _month_index(d: datetime.date) -> int:
    return d.year * 12 + (d.month - 1)


def _experience_to_interval(exp: dict, today: datetime.date):
    """Convert one experience dict to (start_month_index, end_month_index).

    Returns None if start date can't be parsed. End is inclusive.
    """
    start = _parse_date(exp.get('start_date'))

    # Fallback: many older LLM parses store the full range in 'duration'.
    if start is None:
        duration = exp.get('duration')
        if duration:
            parts = re.split(r'[–—\-]|(?:\s+to\s+)', str(duration), maxsplit=1)
            if parts:
                start = _parse_date(parts[0])
                if start is not None and not exp.get('end_date'):
                    tail = parts[1] if len(parts) > 1 else ''
                    if _is_ongoing(tail):
                        return (_month_index(start), _month_index(today))
                    end = _parse_date(tail)
                    if end is not None:
                        return (_month_index(start), _month_index(end))
    if start is None:
        return None

    end_raw = exp.get('end_date')
    if _is_ongoing(end_raw):
        end = today
    else:
        end = _parse_date(end_raw)
        if end is None:
            # Single-date entry -> treat as one month (start month only).
            end = start

    return (_month_index(start), _month_index(end))


def compute_years_of_experience(experiences, today=None) -> int:
    """Sum years of experience across `experiences`, merging overlapping ranges.

    Args:
        experiences: iterable of dicts with (optionally) start_date / end_date /
            duration string fields.
        today: optional datetime.date to anchor "Present" / ongoing ranges.
            Defaults to datetime.date.today(). Passed explicitly by tests so
            assertions don't drift with the calendar.

    Returns:
        Total whole years of experience (floor of total_months / 12).
    """
    today = today or datetime.date.today()
    today_start = today.replace(day=1)

    intervals = []
    for exp in experiences or []:
        try:
            iv = _experience_to_interval(exp, today_start)
        except Exception:
            logger.exception("Failed to parse experience: %r", exp)
            iv = None
        if iv is None:
            continue
        start_idx, end_idx = iv
        if end_idx < start_idx:
            # Swapped / typo dates — skip.
            continue
        intervals.append((start_idx, end_idx))

    if not intervals:
        return 0

    # Merge overlapping / touching intervals.
    intervals.sort()
    merged = [intervals[0]]
    for start_idx, end_idx in intervals[1:]:
        last_start, last_end = merged[-1]
        if start_idx <= last_end + 1:  # overlap OR back-to-back months
            merged[-1] = (last_start, max(last_end, end_idx))
        else:
            merged.append((start_idx, end_idx))

    # Each (start_idx, end_idx) pair represents inclusive months -> +1.
    total_months = sum(end - start + 1 for start, end in merged)
    return max(0, total_months // 12)
