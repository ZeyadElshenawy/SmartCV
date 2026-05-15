"""Sanitize a candidate's `data_content` before it reaches the resume prompt.

The CV parser + LinkedIn signal merger land plenty of garbage on the
master profile that, if dumped into the LLM prompt verbatim, makes the
generated resume noisy: 70+ skills (many soft, many label-leaked /
paren-broken / mis-cased), ALL-CAPS experience titles with parser typos,
first-person verbs inside experience descriptions, kebab-case GitHub
project names, dozens of irrelevant certifications.

This module returns a CLEANED COPY of `data_content`. The original is
never mutated; the prompt builder uses the sanitized view. Functions
here are pure — no DB writes, no LLM calls — so they're cheap to run
on every resume generation.

Public API
----------
- ``sanitize_profile_data(data_content) -> dict``
    One-call entry point. Returns a deep-copied dict with all cleanups
    applied. Idempotent.
- ``sanitize_skills(skills) -> list[dict]``
    Skill-only cleanup, exposed so the resume normalizer's post-LLM
    safety net can reuse the same blocklist + typo dict.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skill cleanup config
# ---------------------------------------------------------------------------

# Soft skills the resume prompt rule says NEVER belong in the Skills array.
# Stored as canonical (lowercase + alphanum-only) so the matcher is
# punctuation/case-insensitive.
_SOFT_SKILL_BLOCKLIST_CANON = {
    'communication', 'communications', 'communicationsplanning',
    'presentation', 'presentationskills', 'publicspeaking',
    'leadership', 'leadershipskills',
    'teamwork', 'teambuilding', 'teammanagement', 'teamperformancemanagement',
    'projectmanagement', 'projectplanning', 'projectperformance', 'projectcontrol',
    'peopledevelopment', 'peoplemanagement',
    'problemsolving', 'criticalthinking',
    'adaptability', 'flexibility',
    'timemanagement', 'organization',
    'performancereporting',
    'collaboration', 'interpersonalskills',
    # MIS / management-information-systems is borderline; keep for tech profiles
    # but the candidate's own MIS skill leaks into the resume even when the JD
    # has nothing to do with it. Block it.
    'managementinformationsystemsmis', 'managementinformationsystems',
    'mis',
    # Round 1.5 — JDs frequently lift these from soft-skill paragraphs
    # and the gap analyzer extracts them as "matched skills". They are
    # NOT technical skills and don't belong in the Skills section.
    'agile', 'agilemethodologies', 'agilemethodology', 'scrum', 'kanban',
    'multitasking', 'taskmanagement',
    'timemanagement',
    # "Scripting" is a category, not a skill — when "Bash" or "Python"
    # is already in the list it's redundant filler.
    'scripting', 'shellscripting',
    # Generic noise that DevOps JDs sometimes pull in.
    'cicdtools', 'devopstools',
}

# Label-prefix leaks the CV parser bakes into skill names.
_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:libraries?|tools?|software|frameworks?|languages?|stack)\s*:\s*",
    re.IGNORECASE,
)

# LinkedIn-style "(Programming Language)" suffix — strip.
_LINKEDIN_VERBOSE_RE = re.compile(
    r"\s*\(\s*(?:programming\s+language|software|library)\s*\)\s*$",
    re.IGNORECASE,
)

# Common parser typos seen on the user's actual profile. Add as the
# regression surface area grows.
_SKILL_TYPO_FIXES = {
    'nfrastructure as a service (iaas)': 'Infrastructure as a Service (IaaS)',
    'nfrastructure': 'Infrastructure',
    'power bi': 'Power BI',
    'power query': 'Power Query',
}

# Acronyms that must stay UPPERCASE when we Title-Case ALL-CAPS strings.
# Lowercase form → canonical-case form.
_ACRONYM_FIX = {
    'ai': 'AI', 'ml': 'ML', 'it': 'IT', 'hr': 'HR', 'sap': 'SAP',
    'erp': 'ERP', 'crm': 'CRM', 'sql': 'SQL', 'nosql': 'NoSQL',
    'aws': 'AWS', 'gcp': 'GCP', 'cv': 'CV', 'nlp': 'NLP', 'ci': 'CI',
    'cd': 'CD', 'mlops': 'MLOps', 'devops': 'DevOps', 'api': 'API',
    'apis': 'APIs', 'ui': 'UI', 'ux': 'UX', 'ios': 'iOS', 'mvp': 'MVP',
    'iaas': 'IaaS', 'paas': 'PaaS', 'saas': 'SaaS', 'iot': 'IoT',
    'mcit': 'MCIT', 'depi': 'DEPI', 'rfmt': 'RFMT', 'rfm': 'RFM',
    'mri': 'MRI', 'cnn': 'CNN', 'rnn': 'RNN', 'lstm': 'LSTM', 'gru': 'GRU',
    'rag': 'RAG', 'llm': 'LLM', 'llms': 'LLMs', 'gpu': 'GPU', 'cpu': 'CPU',
    'ksiu': 'KSIU', 'mit': 'MIT',
}

# Word-level typos the CV parser surfaces verbatim. Keep small and high-
# confidence — we don't want to over-correct legitimate text.
_TITLE_TYPO_FIXES = {
    'infromation': 'Information',
    'automative': 'Automotive',
    'managment': 'Management',
    'devolpment': 'Development',
    'enginerring': 'Engineering',
}

# First-person words to strip from descriptions. Used with word boundaries.
_FIRST_PERSON_RE = re.compile(r"\b(?:i|my|me)\b", re.IGNORECASE)
# After stripping, fix two-space gaps and orphan punctuation.
_TIDY_PATTERNS = [
    (re.compile(r"\s{2,}"), " "),
    (re.compile(r"\s+,"), ","),
    (re.compile(r"\s+\."), "."),
    (re.compile(r"^\s*,\s*"), ""),
    # "Throughout the program  focused on..." after stripping "I" → fix subject
    # by capitalizing the next verb. Best-effort — leaves the sentence readable
    # even if subject-verb agreement is informal.
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _canonical(text: str) -> str:
    """Lowercase + alphanum-only — used as a dedup/equality key for the
    soft-skill blocklist and for skill dedup."""
    if not text:
        return ''
    return ''.join(c.lower() for c in text if c.isalnum())


def _close_unbalanced_parens(text: str) -> str:
    """Strip a trailing unbalanced paren or close it. The CV parser
    sometimes produces ``"Transfer Learning (TensorFlow"`` — neither
    side is correct on its own. Heuristic: drop everything from the
    last `(` to end-of-string, then trim."""
    if not text:
        return text
    opens = text.count('(')
    closes = text.count(')')
    if opens > closes:
        # More `(` than `)` — drop from the last `(` onward.
        idx = text.rfind('(')
        if idx >= 0:
            text = text[:idx].rstrip()
    elif closes > opens:
        # Orphan trailing `)` — strip.
        text = text.rstrip(') ').rstrip()
    return text


def _title_case_with_acronyms(text: str) -> str:
    """Title-case a string while preserving known acronyms.

    Only applies the transformation when the input is meaningfully
    ALL-CAPS or all-lowercase. Mixed-case inputs (``"SmartCV"``,
    ``"BookShop"``) are returned unchanged.
    """
    if not text:
        return text
    stripped = text.strip()
    if not stripped:
        return text
    # Mixed case: trust the original.
    has_lower = any(c.islower() for c in stripped)
    has_upper = any(c.isupper() for c in stripped)
    if has_lower and has_upper:
        return text

    # Apply word-level typo fixes pre-title-case (so "INFROMATION" maps).
    parts = re.split(r"(\W+)", stripped)
    out_parts: list[str] = []
    for p in parts:
        if not p:
            continue
        if not p.isalnum():
            out_parts.append(p)
            continue
        low = p.lower()
        if low in _TITLE_TYPO_FIXES:
            out_parts.append(_TITLE_TYPO_FIXES[low])
            continue
        if low in _ACRONYM_FIX:
            out_parts.append(_ACRONYM_FIX[low])
            continue
        # English connector words stay lowercase mid-string (Title Case
        # style). The leading word still gets capitalized by the regex
        # pass at the end of `_strip_first_person` callers that need
        # initial-cap; here we just want the words *inside* to look right.
        if low in {'to', 'of', 'in', 'on', 'at', 'by', 'for', 'and', 'or', 'the', 'a', 'an', 'with', 'as'}:
            out_parts.append(low)
            continue
        out_parts.append(p.capitalize())
    return ''.join(out_parts)


def _kebab_to_title(slug: str) -> str:
    """Convert a GitHub-style kebab-case repo name to a readable title.

    Heuristic: split on dashes/underscores; title-case each token unless
    it's a known acronym (which stays uppercase). The LAST token, if it
    looks like an organization/project tag (DEPI, RFMT) we recognize via
    the acronym dict, gets wrapped in parens: ``"healthcare-prediction-depi"``
    → ``"Healthcare Prediction (DEPI)"``.
    """
    if not slug or '-' not in slug and '_' not in slug:
        return slug
    tokens = re.split(r"[-_]+", slug)
    last = tokens[-1].lower() if tokens else ''
    head_tokens = tokens[:-1] if last in _ACRONYM_FIX else tokens
    tail_acronym = _ACRONYM_FIX.get(last) if last in _ACRONYM_FIX else None

    def _format(tok: str) -> str:
        low = tok.lower()
        if low in _ACRONYM_FIX:
            return _ACRONYM_FIX[low]
        return tok.capitalize() if tok else tok

    head = ' '.join(_format(t) for t in head_tokens if t)
    if tail_acronym:
        return f"{head} ({tail_acronym})".strip()
    return head


# Invisible Unicode characters that the CV parser / copy-paste from web
# CVs leaves in skill names, project names, and bullets. They render
# fine visually but break ATS keyword matching ("ACR-QA⁠" is not
# the same string as "ACR-QA"). Strip globally.
#
# Includes (the most common offenders we've seen):
#   U+2060 WORD JOINER
#   U+200B ZERO WIDTH SPACE
#   U+200C ZERO WIDTH NON-JOINER
#   U+200D ZERO WIDTH JOINER
#   U+FEFF BYTE ORDER MARK / ZERO WIDTH NO-BREAK SPACE
_ZERO_WIDTH_CHARS = '⁠​‌‍﻿'
_ZERO_WIDTH_RE = re.compile(f'[{_ZERO_WIDTH_CHARS}]')


def _strip_zero_width(text: str) -> str:
    """Remove every zero-width / word-joiner character from a string.

    Cheap to call (no-op when text has none). Runs on every textual
    field touched by the sanitizer so the LLM never sees the invisible
    characters and the docx renderer never emits them.
    """
    if not isinstance(text, str) or not text:
        return text
    return _ZERO_WIDTH_RE.sub('', text)


def _scrub_zero_width_deep(value: Any) -> Any:
    """Recursively strip zero-width chars from any string anywhere in
    a nested dict/list structure. Used at the END of
    ``sanitize_profile_data`` so we don't have to thread the rule
    through every sub-sanitizer."""
    if isinstance(value, str):
        return _strip_zero_width(value)
    if isinstance(value, list):
        return [_scrub_zero_width_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_zero_width_deep(v) for k, v in value.items()}
    return value


def _fix_word_typos(text: str) -> str:
    """Substitute known word-level typos in ANY text, regardless of
    casing. The CV parser surfaces clear typos ("INFROMATION",
    "Almansour Automative") that get title-cased properly by
    `_title_case_with_acronyms` for ALL-CAPS strings, but mixed-case
    strings short-circuit that code path. This helper does the same
    typo lookup for any input — preserves the original casing for the
    surrounding letters of words it doesn't touch."""
    if not text:
        return text
    parts = re.split(r"(\W+)", text)
    out: list[str] = []
    for p in parts:
        if not p or not p.isalnum():
            out.append(p)
            continue
        fixed = _TITLE_TYPO_FIXES.get(p.lower())
        if fixed is None:
            out.append(p)
            continue
        # Preserve the original casing pattern when possible — if the
        # source was ALL CAPS use the corrected word in caps; if it
        # was Title Case, keep Title Case; otherwise plain.
        if p.isupper():
            out.append(fixed.upper())
        elif p[0].isupper():
            out.append(fixed[:1].upper() + fixed[1:])
        else:
            out.append(fixed.lower())
    return ''.join(out)


def _strip_first_person(text: str) -> str:
    if not text:
        return text
    out = _FIRST_PERSON_RE.sub('', text)
    for pat, repl in _TIDY_PATTERNS:
        out = pat.sub(repl, out)
    # Strip first so the next regex's `^` anchor binds to the actual
    # first character of the result, not whatever leading whitespace
    # the first-person strip left behind.
    out = out.strip()
    # Capitalize the first letter of each sentence — "i developed..."
    # became "developed..." and now needs initial-cap. Same for
    # sentences after a period.
    out = re.sub(
        r"(^|\.\s+)([a-z])",
        lambda m: m.group(1) + m.group(2).upper(),
        out,
    )
    return out


# ---------------------------------------------------------------------------
# Skill sanitizer
# ---------------------------------------------------------------------------

def sanitize_skills(skills: Any) -> list[dict]:
    """Return a cleaned list of skill dicts.

    Drops soft skills, strips label leaks, closes parens, fixes typos,
    strips LinkedIn-verbose suffixes, dedupes by canonical key. Input
    can be a list of strings OR dicts; output is always a list of dicts
    so the rest of the pipeline can rely on the shape.
    """
    if not isinstance(skills, list):
        return []
    out: list[dict] = []
    seen_canon: set[str] = set()
    dropped_soft = 0

    for entry in skills:
        if isinstance(entry, dict):
            name = (entry.get('name') or '').strip()
            extras = {k: v for k, v in entry.items() if k != 'name'}
        else:
            name = str(entry or '').strip()
            extras = {}
        if not name:
            continue

        # Label-prefix strip ("Libraries: Pandas" → "Pandas").
        name = _LABEL_PREFIX_RE.sub('', name).strip()
        # LinkedIn-verbose strip ("Python (Programming Language)" → "Python").
        name = _LINKEDIN_VERBOSE_RE.sub('', name).strip()
        # Paren-balance fix ("Transfer Learning (TensorFlow").
        name = _close_unbalanced_parens(name)
        if not name:
            continue
        # Word-level typo fix (case-insensitive lookup).
        name_low = name.lower()
        if name_low in _SKILL_TYPO_FIXES:
            name = _SKILL_TYPO_FIXES[name_low]

        canon = _canonical(name)
        if not canon:
            continue
        if canon in _SOFT_SKILL_BLOCKLIST_CANON:
            dropped_soft += 1
            continue
        if canon in seen_canon:
            continue
        seen_canon.add(canon)
        out.append({'name': name, **extras})

    if dropped_soft:
        logger.info(
            "profile_sanitizer: dropped %d soft skills from skills list",
            dropped_soft,
        )
    return out


# ---------------------------------------------------------------------------
# Experience sanitizer
# ---------------------------------------------------------------------------

_LOCATION_PREFIX_RE = re.compile(
    r"^\s*(?:Qesm|Markaz|Madinat|City\s+of)\s+",
    re.IGNORECASE,
)


def _clean_location(text: str) -> str:
    """Strip Arabic-government-registry prefixes ("Qesm El Zamalek" →
    "El Zamalek") that LinkedIn scrapers sometimes include in location
    fields. These read as auto-translated to recruiters."""
    if not text:
        return text
    return _LOCATION_PREFIX_RE.sub('', text).strip()


def _sanitize_experience(exp: dict) -> dict:
    if not isinstance(exp, dict):
        return exp
    out = dict(exp)
    if out.get('title'):
        # Title-case ALL-CAPS titles, then run a typo pass that also
        # cleans mixed-case strings (the title-caser short-circuits
        # on already-mixed-case input).
        out['title'] = _fix_word_typos(_title_case_with_acronyms(out['title']))
    if out.get('company'):
        # Same two-step: ALL-CAPS company names get Title-Cased, then
        # known typos get fixed regardless of casing — covers the
        # "Almansour Automative" → "Almansour Automotive" case that the
        # pure title-caser missed because the input was already mixed
        # case.
        out['company'] = _fix_word_typos(_title_case_with_acronyms(out['company']))
    if out.get('location'):
        out['location'] = _clean_location(out['location'])
    desc = out.get('description')
    if isinstance(desc, str):
        out['description'] = _strip_first_person(desc)
    elif isinstance(desc, list):
        out['description'] = [_strip_first_person(str(b)) for b in desc if b]
    # Highlights are bullets — also strip first-person.
    hls = out.get('highlights')
    if isinstance(hls, list):
        out['highlights'] = [_strip_first_person(str(b)) for b in hls if b]
    return out


# ---------------------------------------------------------------------------
# Project sanitizer
# ---------------------------------------------------------------------------

def _sanitize_project(proj: dict) -> dict:
    if not isinstance(proj, dict):
        return proj
    out = dict(proj)
    name = (out.get('name') or '').strip()
    if name:
        # All-caps name → sentence/title case.
        stripped = name.strip()
        has_lower = any(c.islower() for c in stripped)
        has_upper = any(c.isupper() for c in stripped)
        if has_upper and not has_lower:
            out['name'] = _title_case_with_acronyms(name)
        # Kebab-case repo slug → readable title.
        elif '-' in name and not has_upper and ' ' not in name:
            out['name'] = _kebab_to_title(name)
        # Mixed case (SmartCV, BookShop) — leave alone.
    return out


# ---------------------------------------------------------------------------
# Certification sanitizer
# ---------------------------------------------------------------------------

def _sanitize_certification(cert: dict) -> dict:
    if not isinstance(cert, dict):
        return cert
    out = dict(cert)
    name = (out.get('name') or '').strip()
    if name:
        # Strip trailing punctuation that some parsers leave on cert names
        # (e.g. "Neural Networks and Deep Learning.").
        out['name'] = name.rstrip('. ').strip()
    issuer = (out.get('issuer') or '').strip()
    if issuer and issuer.isupper() and len(issuer) > 4:
        # "DATACAMP" / "COURSERA" → "DataCamp" / "Coursera" reads better
        # but we don't want to over-rewrite — issuer brand canonicalization
        # is a known short-list.
        out['issuer'] = {
            'DATACAMP': 'DataCamp',
            'COURSERA': 'Coursera',
            'DIGITAL EGYPT PIONEERS INITIATIVE': 'Digital Egypt Pioneers Initiative',
        }.get(issuer, issuer.title())
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def sanitize_profile_data(data_content: Any) -> dict:
    """Return a deep-copied, sanitized view of `data_content`.

    Idempotent — running on already-clean data is a near no-op. Cheap
    enough to run on every resume generation. Does not mutate the input.
    """
    if not isinstance(data_content, dict):
        return {}
    out = copy.deepcopy(data_content)

    if isinstance(out.get('skills'), list):
        out['skills'] = sanitize_skills(out['skills'])
    if isinstance(out.get('experiences'), list):
        out['experiences'] = [_sanitize_experience(e) for e in out['experiences']]
    if isinstance(out.get('projects'), list):
        out['projects'] = [_sanitize_project(p) for p in out['projects']]
    if isinstance(out.get('certifications'), list):
        out['certifications'] = [_sanitize_certification(c) for c in out['certifications']]
    # Final pass: scrub invisible Unicode (word joiners, zero-width
    # spaces) from every string anywhere in the tree. The CV parser /
    # web-paste path leaves these in skill names, project names, and
    # bullets — they break ATS keyword matching even though they look
    # invisible.
    out = _scrub_zero_width_deep(out)
    return out
