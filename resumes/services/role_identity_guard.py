"""Hard guards against role / company / project fabrication.

The LLM is repeatedly instructed via prompt to "keep titles and
companies exactly as in the CV." The end-to-end trace caught a
"Banque Misr" role shipping through the regen-section path — proving
prompt instructions alone are insufficient. These guards are CODE
checks that compare LLM output against the user's real data
(``profile.data_content`` for main gen, or the in-flight
``current_content`` for section regen) and drop entries whose
identity doesn't match a real one.

Identity policy:
  - **Experience** — primary key is the normalized COMPANY name
    (case-insensitive, whitespace-collapsed). LLM may rewrite the
    title for clarity; companies are not paraphrased. When the
    returned entry has no company at all, fall back to normalized
    title match.
  - **Projects** — primary key is the normalized URL (exact). If
    the entry has no URL, fall back to fuzzy name similarity
    (SequenceMatcher ratio ≥ 0.7) to tolerate the common
    kebab-case → display-case rename
    ("healthcare-prediction-depi" → "Healthcare Prediction (DEPI)").

The guards are deliberately CONSERVATIVE on false-positives.
Dropping a real role due to a slight rename is annoying but
recoverable (the user retries). Shipping a phantom role is not.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r'\s+')


def _normalize(s) -> str:
    if not isinstance(s, str):
        return ''
    return _WS_RE.sub(' ', s.strip()).lower()


# ---- identity-set builders (read from a known/source list) ----------

def known_company_set(entries) -> set[str]:
    out = set()
    for e in entries or []:
        if isinstance(e, dict):
            c = _normalize(e.get('company', ''))
            if c:
                out.add(c)
    return out


def known_title_set(entries) -> set[str]:
    out = set()
    for e in entries or []:
        if isinstance(e, dict):
            t = _normalize(e.get('title', ''))
            if t:
                out.add(t)
    return out


def known_project_urls(entries) -> set[str]:
    out = set()
    for p in entries or []:
        if isinstance(p, dict):
            u = _normalize(p.get('url', ''))
            if u:
                out.add(u)
    return out


def known_project_names(entries) -> list[str]:
    out: list[str] = []
    for p in entries or []:
        if isinstance(p, dict):
            n = _normalize(p.get('name', ''))
            if n:
                out.append(n)
    return out


# ---- per-entry match predicates -------------------------------------

def experience_matches_known(entry: dict, known_companies, known_titles) -> bool:
    if not isinstance(entry, dict):
        return False
    c = _normalize(entry.get('company', ''))
    if c:
        return c in known_companies
    t = _normalize(entry.get('title', ''))
    if t:
        return t in known_titles
    return False


def project_matches_known(entry: dict, known_urls, known_names) -> bool:
    if not isinstance(entry, dict):
        return False
    u = _normalize(entry.get('url', ''))
    if u and u in known_urls:
        return True
    n = _normalize(entry.get('name', ''))
    if not n:
        return False
    if n in known_names:
        return True
    # Fuzzy fallback for the kebab→display rename pattern.
    return any(SequenceMatcher(None, n, kn).ratio() >= 0.7 for kn in known_names)


# ---- list-level filters (kept / dropped) ----------------------------

def filter_experiences_to_known(returned, known_entries):
    """Returns ``(kept, dropped)``. Drops entries whose company (or
    fallback title) doesn't appear in ``known_entries``."""
    known_companies = known_company_set(known_entries)
    known_titles = known_title_set(known_entries)
    kept: list = []
    dropped: list = []
    for e in returned or []:
        if experience_matches_known(e, known_companies, known_titles):
            kept.append(e)
        else:
            dropped.append(e)
    return kept, dropped


def filter_projects_to_known(returned, known_entries):
    """Returns ``(kept, dropped)``. URL match first, then fuzzy name."""
    known_urls = known_project_urls(known_entries)
    known_names = known_project_names(known_entries)
    kept: list = []
    dropped: list = []
    for p in returned or []:
        if project_matches_known(p, known_urls, known_names):
            kept.append(p)
        else:
            dropped.append(p)
    return kept, dropped


# ---- coverage check (used by section-regen reject path) -------------

def covers_known_identities(kept_entries, known_entries, *, kind: str) -> bool:
    """True when every entry in ``known_entries`` is matched by at
    least one entry in ``kept_entries``. Used after filtering to
    decide whether to REJECT the regeneration entirely (if the LLM
    failed to cover a real role/project)."""
    if kind == 'experience':
        kept_companies = known_company_set(kept_entries)
        kept_titles = known_title_set(kept_entries)
        for k in (known_entries or []):
            if not experience_matches_known(k, kept_companies, kept_titles):
                return False
        return True
    if kind == 'projects':
        kept_urls = known_project_urls(kept_entries)
        kept_names = known_project_names(kept_entries)
        for k in (known_entries or []):
            if not project_matches_known(k, kept_urls, kept_names):
                return False
        return True
    return True


def _identity_summary(entry: dict, *, kind: str) -> str:
    """Short string for logs — never raw dict (might contain free-text)."""
    if not isinstance(entry, dict):
        return repr(entry)[:80]
    if kind == 'experience':
        return f"title={entry.get('title','')!r} company={entry.get('company','')!r}"
    if kind == 'projects':
        return f"name={entry.get('name','')!r} url={entry.get('url','')!r}"
    return ''


def log_dropped(dropped, *, kind: str, surface: str) -> None:
    """Single log line per drop, structured so the entries are
    grep-able. Surface = 'main-gen' | 'section-regen' | etc."""
    for d in dropped or []:
        logger.warning(
            "role_identity_guard: dropped invented %s entry on %s (%s)",
            kind, surface, _identity_summary(d, kind=kind),
        )
