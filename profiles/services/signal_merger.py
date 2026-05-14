"""Merge external signals into the master profile sections.

When a user connects LinkedIn / GitHub / Scholar / Kaggle, the snapshots
land in `profile.data_content['{source}_signals']` but the user-visible
"master profile" sections (experiences, certifications, skills, education,
volunteer_experience) are not touched. The user expects connected-account
data to flow into those sections so the resume builder sees a complete
profile without manual copy-paste.

Projects are handled separately by [[project_dedupe]] via LLM-based merge.
Here we cover the remaining sections with cheap normalized-key dedup —
keys are canonicalized (lowercase + alpha-num only) so "Python" and
"Python (Programming Language)" collapse to the same skill. Existing
entries always win: we never overwrite or "merge content"; we just
append LinkedIn-only items the master profile didn't already have. That
matches the user's explicit ask: "be careful not to merge duplicate
projects, skills, certs."

Idempotent: re-running with unchanged signals is a no-op. The caller
mutates `profile.data_content` and decides whether to save().
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

# Org/company/institution names are very prone to spelling drift between
# the CV (often typed by hand) and LinkedIn (the canonical source). The CV
# might say "Almansour Automative" while LinkedIn renders "Al-Mansour
# Automotive". A canonical alpha-num key won't catch these. We match on
# `difflib.SequenceMatcher` ratio with a 0.85 cutoff — the same cutoff the
# gap analyzer uses for skill reconciliation (per CLAUDE.md).
_ORG_FUZZY_CUTOFF = 0.85


def merge_signals_into_profile(profile) -> dict[str, int]:
    """Merge LinkedIn (and GitHub language) signals into the master profile.

    Returns a summary `{section: added_count}` suitable for logging or
    surfacing in a status banner. Mutates `profile.data_content` in place;
    does NOT call `profile.save()` — the caller controls the transaction.
    """
    data = profile.data_content or {}
    linkedin = data.get('linkedin_signals') or {}
    github = data.get('github_signals') or {}

    summary = {'experiences': 0, 'certifications': 0, 'skills': 0,
               'education': 0, 'volunteer_experience': 0}

    if linkedin.get('scraped') and not linkedin.get('error'):
        data['experiences'], summary['experiences'] = _merge_experiences(
            data.get('experiences') or [], linkedin.get('experience') or [],
        )
        data['certifications'], summary['certifications'] = _merge_certifications(
            data.get('certifications') or [], linkedin.get('licenses') or [],
        )
        data['skills'], summary['skills'] = _merge_skills(
            data.get('skills') or [], linkedin.get('skills') or [],
        )
        data['education'], added_edu = _merge_education(
            data.get('education') or [], linkedin.get('education') or [],
        )
        summary['education'] += added_edu
        data['volunteer_experience'], added_vol = _merge_volunteering(
            data.get('volunteer_experience') or [], linkedin.get('volunteering') or [],
        )
        summary['volunteer_experience'] += added_vol

    # GitHub language_breakdown → skills (top languages only, blocklisted
    # generic ones already filtered by the aggregator).
    if github and not github.get('error'):
        gh_langs = [lang for lang, _count in (github.get('language_breakdown') or [])[:8]]
        data['skills'], added_gh = _merge_skills_from_strings(
            data.get('skills') or [], gh_langs,
        )
        summary['skills'] += added_gh

    profile.data_content = data
    logger.info("signal_merger: added %s", summary)
    return summary


# ---------- Canonicalization ------------------------------------------------

_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")
# Strip trailing parentheticals like "Python (Programming Language)" → "Python".
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")


def _canonical(text: str | None) -> str:
    if not text:
        return ""
    t = _TRAILING_PAREN.sub("", str(text)).strip().lower()
    return _NON_ALPHANUM.sub("", t)


def _fuzzy_match(canonical_new: str, seen_canonicals: set[str], cutoff: float = _ORG_FUZZY_CUTOFF) -> bool:
    """True iff `canonical_new` is similar enough to any string in
    `seen_canonicals` to count as the same entity. Exact matches are
    handled separately for speed; this is the fallback for spelling drift."""
    if not canonical_new:
        return False
    for s in seen_canonicals:
        if not s:
            continue
        # Skip the obvious cheap exits before the more expensive ratio.
        if abs(len(s) - len(canonical_new)) > max(len(s), len(canonical_new)) * 0.3:
            continue
        if SequenceMatcher(None, s, canonical_new).ratio() >= cutoff:
            return True
    return False


# ---------- Per-section mergers --------------------------------------------

def _merge_experiences(existing: list[dict], linkedin_exp: list[dict]) -> tuple[list[dict], int]:
    """LinkedIn experience entries group multiple designations per company.
    Flatten so each designation becomes one master-profile experience, then
    dedupe. Match strategy: (1) exact canonical key on company+title, then
    (2) fuzzy company-name match alone — catches typos like "Al-Mansour
    Automotive" vs "Almansour Automative".

    Side-effect: when a fuzzy-matched existing entry has end_date='Present'
    but LinkedIn's duration shows the role is finite and already in the
    past, correct the existing entry's end_date. The CV parser tends to
    drop "Present" on short internships when no explicit end is in the
    PDF; LinkedIn's "Aug 2025 · 1 mo" is authoritative.
    """
    seen_keys = {_exp_key(e) for e in existing}
    seen_companies = {_canonical(e.get('company')) for e in existing if e.get('company')}
    added: list[dict] = []
    for company_entry in linkedin_exp:
        company = (company_entry.get('company_name') or '').strip()
        emp_type = (company_entry.get('employment_type') or '').strip()
        designations = company_entry.get('designations') or []
        for d in designations:
            title = (d.get('designation') or '').strip()
            if not title and not company:
                continue
            duration = (d.get('duration') or '').strip()
            start, end = _split_date_range(duration)
            mapped = {
                'title': title,
                'company': company,
                'start_date': start,
                'end_date': end,
                'description': (d.get('description') or '').strip(),
                'highlights': [],
                'location': (d.get('location') or '').strip(),
                'employment_type': emp_type,
                'source': 'linkedin',
            }
            key = _exp_key(mapped)
            company_canon = _canonical(company)
            fuzzy_match = bool(company_canon and _fuzzy_match(company_canon, seen_companies))
            if key in seen_keys or fuzzy_match:
                _correct_wrong_present(existing, company_canon, start, end)
                continue
            seen_keys.add(key)
            if company_canon:
                seen_companies.add(company_canon)
            added.append(mapped)
    return existing + added, len(added)


def _correct_wrong_present(
    existing: list[dict], company_canon: str, new_start: str, new_end: str,
) -> None:
    """If an existing entry for the same company carries end_date='Present'
    but LinkedIn says the role ended in the past, overwrite with LinkedIn's
    dates. Guard: only fires when LinkedIn's `new_end` parses as a real
    month/year — never blanks an end-date with no replacement. Uses fuzzy
    match on the company name (same threshold the dedupe path uses) so
    typos like "Almansour Automative" ≈ "Al-Mansour Automotive" are
    treated as the same entry."""
    if not new_end or new_end.lower() == 'present':
        return
    new_end_parsed = _parse_month_year(new_end)
    if not new_end_parsed:
        return
    import datetime as _dt
    today = _dt.date.today()
    end_y, end_m = new_end_parsed
    if (end_y, end_m) > (today.year, today.month):
        return  # End is in the future — leave 'Present' alone.
    for entry in existing:
        entry_canon = _canonical(entry.get('company'))
        if not entry_canon:
            continue
        if entry_canon != company_canon and not _fuzzy_match(entry_canon, {company_canon}):
            continue
        if (entry.get('end_date') or '').strip().lower() != 'present':
            continue
        entry['end_date'] = new_end
        if new_start and not (entry.get('start_date') or '').strip():
            entry['start_date'] = new_start


def _exp_key(exp: dict) -> str:
    return f"{_canonical(exp.get('company'))}|{_canonical(exp.get('title'))}"


def _merge_certifications(existing: list[dict], linkedin_certs: list[dict]) -> tuple[list[dict], int]:
    """Dedupe by (name) canonical — LinkedIn occasionally duplicates a cert
    across multiple "Issued by" entries, the canonical name still wins."""
    seen = {_canonical((c or {}).get('name')) for c in existing}
    added: list[dict] = []
    for cert in linkedin_certs:
        name = (cert.get('name') or '').strip()
        if not name:
            continue
        key = _canonical(name)
        if key in seen:
            continue
        seen.add(key)
        added.append({
            'name': name,
            'issuer': (cert.get('institute') or '').strip(),
            'date': (cert.get('issued_date') or '').strip(),
            'url': (cert.get('credential_url') or '').strip() or None,
            'source': 'linkedin',
        })
    return existing + added, len(added)


def _merge_skills(existing: list, linkedin_skills: list[dict]) -> tuple[list, int]:
    """Master-profile skills are usually a list of {name, ...} dicts but the
    CV parser sometimes drops bare strings. Accept either shape; emit dicts
    on the output side for consistency."""
    seen = {_canonical(_skill_name(s)) for s in existing}
    added: list[dict] = []
    for skill in linkedin_skills:
        name = (skill.get('name') or '').strip() if isinstance(skill, dict) else str(skill).strip()
        if not name:
            continue
        key = _canonical(name)
        if not key or key in seen:
            continue
        seen.add(key)
        added.append({
            'name': name,
            'proficiency': None,
            'years': None,
            'source': 'linkedin',
        })
    return list(existing) + added, len(added)


def _merge_skills_from_strings(existing: list, names: list[str]) -> tuple[list, int]:
    """Add bare skill names (e.g. GitHub languages). Dedup against existing."""
    seen = {_canonical(_skill_name(s)) for s in existing}
    added: list[dict] = []
    for raw in names:
        name = (raw or '').strip()
        if not name:
            continue
        key = _canonical(name)
        if not key or key in seen:
            continue
        seen.add(key)
        added.append({
            'name': name,
            'proficiency': None,
            'years': None,
            'source': 'github',
        })
    return list(existing) + added, len(added)


def _skill_name(s: Any) -> str:
    if isinstance(s, dict):
        return str(s.get('name') or '')
    return str(s or '')


def _merge_education(existing: list[dict], linkedin_edu: list[dict]) -> tuple[list[dict], int]:
    """Dedupe by institution name — exact canonical first, then fuzzy fallback
    to catch the same school spelled differently between CV and LinkedIn."""
    seen = {_canonical((e or {}).get('institution')) for e in existing if (e or {}).get('institution')}
    added: list[dict] = []
    for entry in linkedin_edu:
        institution = (entry.get('college') or '').strip()
        if not institution:
            continue
        key = _canonical(institution)
        if key in seen or _fuzzy_match(key, seen):
            continue
        seen.add(key)
        duration = (entry.get('duration') or '').strip()
        added.append({
            'institution': institution,
            'degree': (entry.get('degree') or '').strip(),
            'graduation_year': _last_year(duration),
            'description': (entry.get('description') or '').strip(),
            'source': 'linkedin',
        })
    return existing + added, len(added)


def _merge_volunteering(existing: list[dict], linkedin_vol: list[dict]) -> tuple[list[dict], int]:
    """Dedupe by organization name — exact canonical first, then fuzzy."""
    seen_keys = {_vol_key(e) for e in existing}
    seen_orgs = {_canonical(e.get('organization')) for e in existing if e.get('organization')}
    added: list[dict] = []
    for entry in linkedin_vol:
        org = (entry.get('organization') or '').strip()
        role = (entry.get('role') or '').strip()
        if not org and not role:
            continue
        mapped = {
            'title': role,
            'organization': org,
            'date': (entry.get('duration') or '').strip(),
            'cause': (entry.get('cause') or '').strip(),
            'description': (entry.get('description') or '').strip(),
            'source': 'linkedin',
        }
        key = _vol_key(mapped)
        if key in seen_keys:
            continue
        org_canon = _canonical(org)
        if org_canon and _fuzzy_match(org_canon, seen_orgs):
            continue
        seen_keys.add(key)
        if org_canon:
            seen_orgs.add(org_canon)
        added.append(mapped)
    return existing + added, len(added)


def _vol_key(entry: dict) -> str:
    return f"{_canonical(entry.get('organization'))}|{_canonical(entry.get('title'))}"


# ---------- Date helpers ----------------------------------------------------

_DATE_RANGE_RE = re.compile(
    r"^\s*(?P<start>(?:[A-Za-z]+\s+\d{4}|\d{4}))\s*[–\-]\s*"
    r"(?P<end>(?:[A-Za-z]+\s+\d{4}|\d{4}|Present))",
    re.IGNORECASE,
)
# LinkedIn renders short stints as "Aug 2025 · 1 mo" or "May 2024 · 2 yrs":
# a single anchor date plus a `· N mo|yr` length. No explicit end. We use
# the length to compute the end date so the role isn't surfaced as ongoing.
_SINGLE_DATE_DURATION_RE = re.compile(
    r"^\s*(?P<start>(?:[A-Za-z]+\s+\d{4}|\d{4}))\s*·\s*"
    r"(?P<n>\d+)\s*(?P<unit>yr|mo|year|month)",
    re.IGNORECASE,
)
_MONTH_NAMES = (
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
)
_MONTH_LOOKUP = {n.lower(): i + 1 for i, n in enumerate(_MONTH_NAMES)}


def _split_date_range(text: str) -> tuple[str, str]:
    """Pull start + end out of a LinkedIn duration string. Handles:
      - "Jun 2025 - Dec 2025 · 7 mos" → ("Jun 2025", "Dec 2025")
      - "Aug 2025 · 1 mo"            → ("Aug 2025", "Sep 2025")
      - "May 2023 · 2 yrs"           → ("May 2023", "May 2025")
    Returns ("", "") when no shape matches — caller leaves the dates empty
    so the user can fill them rather than persisting a wrong guess.
    """
    if not text:
        return "", ""
    m = _DATE_RANGE_RE.match(text)
    if m:
        return m.group('start').strip(), m.group('end').strip()
    m = _SINGLE_DATE_DURATION_RE.match(text)
    if m:
        start = m.group('start').strip()
        n = int(m.group('n'))
        unit = m.group('unit').lower()
        months = n * 12 if unit.startswith('y') else n
        end = _add_months_to_date(start, months)
        return start, end
    return "", ""


def _parse_month_year(text: str) -> tuple[int, int] | None:
    """Parse 'Aug 2025' / '2025' / 'August 2025' → (year, month). Returns
    None if the shape doesn't match. Year-only returns month=1 so the
    arithmetic still works (treated as January)."""
    if not text:
        return None
    t = text.strip()
    parts = t.split()
    if len(parts) == 1 and parts[0].isdigit() and len(parts[0]) == 4:
        return int(parts[0]), 1
    if len(parts) == 2:
        month_name, year_str = parts
        month = _MONTH_LOOKUP.get(month_name[:3].lower())
        if month and year_str.isdigit() and len(year_str) == 4:
            return int(year_str), month
    return None


def _add_months_to_date(start: str, months: int) -> str:
    """Add `months` to a 'Mon YYYY' / 'YYYY' string. Returns the formatted
    end date or '' if the start couldn't be parsed."""
    parsed = _parse_month_year(start)
    if not parsed:
        return ""
    year, month = parsed
    month += months
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return f"{_MONTH_NAMES[month - 1]} {year}"


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _last_year(text: str) -> str:
    """Pull the trailing 4-digit year out of a LinkedIn date range. Empty
    string if no year is present."""
    if not text:
        return ""
    years = _YEAR_RE.findall(text)
    return years[-1] if years else ""
