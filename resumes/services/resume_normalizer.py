"""Post-LLM safety net that normalizes the model's resume output.

The prompt tells the LLM not to include soft skills, to Title-Case titles,
to consolidate coursework, to drop first-person voice — but the smaller
Groq model still leaks all of those into the output. This module is the
deterministic last-mile cleanup that runs AFTER the LLM has produced
output and BEFORE the grounding validator looks at it.

It also enforces the inclusion plan when one is available: drop projects
the planner didn't pick, drop certs the planner didn't pick, cap skills.

Pure module — no DB writes, no LLM calls. Reuses constants and helpers
from ``profiles.services.profile_sanitizer`` so the upstream sanitizer
and the post-LLM normalizer share one source of truth for the
soft-skill list, title-case rules, and first-person regex.

Public API
----------
``normalize_resume(resume_content, plan=None) -> dict``
    One call applies every rule below in order. Always returns a new
    dict; never mutates the input.
"""
from __future__ import annotations

import copy
import datetime as _dt
import logging
import re
from difflib import SequenceMatcher
from typing import Any, Optional

from profiles.services.profile_sanitizer import (
    _SOFT_SKILL_BLOCKLIST_CANON,
    _canonical,
    _matches_soft_skill_blocklist,
    _strip_first_person,
    _title_case_with_acronyms,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Caps — keep aligned with the plan and the inclusion_planner constants.
# ---------------------------------------------------------------------------

# 14 was the user-visible target in the plan ("Skills section ~12-14 items").
# The inclusion_planner already caps at 20; this is a tighter post-LLM cap so
# the rendered resume doesn't spill across the page.
_HARD_SKILL_CAP = 14
# Match inclusion_planner._MAX_PROJECTS / _MAX_PROJECTS_RETAIN_AS_FALLBACK.
_PROJECT_CAP = 6
# Match the plan's "Cap certs at 8 JD-relevant entries".
_CERT_CAP = 8

# Fuzzy threshold for plan↔resume name reconciliation when the LLM has
# lightly renamed a kept project / cert (e.g. "Associate Data Scientist
# in Python" vs "Associate Data Scientist - Python"). 0.85 matches the
# convention used in profiles.services.signal_merger.
_FUZZY_MATCH_CUTOFF = 0.85

# A bullet that looks like a course-name leftover from a coursework
# section: short noun phrase, no terminal punctuation, no action-verb
# start. We collapse a run of 2+ consecutive such bullets into one
# "Coursework: A, B, C." line so the resume doesn't render eight
# tiny one-line bullets.
#
# Cap was 7 in v1 — bumped to 12 after the real-resume audit showed the
# LLM emitting legitimate course titles like "Python for Data Science,
# AI & Development + Python Project" (9 words) that the 7-word cap was
# rejecting. 12 is still tight enough to exclude full achievement
# sentences (which are typically 15+ words).
_COURSEWORK_MAX_WORDS = 12
_VERB_START_RE = re.compile(
    r"^\s*(?:i\s+|my\s+|"  # first-person leftovers (defence-in-depth)
    r"developed|built|designed|implemented|shipped|launched|"
    r"reduced|improved|accelerated|cut|increased|delivered|"
    r"led|owned|coordinated|mentored|managed|"
    r"analy[sz]ed|investigated|diagnosed|practi[sc]ed|practi[cs]ed|"
    r"collaborated|presented|created|automated|deployed|"
    r"applied|focused|trained|tested|evaluated|prepared|engineered|"
    r"selected|completed|conducted|drove|wrote|published|"
    r"researched|optimi[sz]ed|refactored|migrated|integrated|"
    r"used|leveraged|utili[sz]ed|spearheaded|enabled|facilitated"
    r")\b",
    re.IGNORECASE,
)

# Words ending in -ed that are nouns / adjectives in resume context,
# NOT past-tense verbs. Used to whitelist short noun phrases like
# "Supervised Learning" against the "starts with -ed → action bullet"
# heuristic below.
_PAST_TENSE_NON_VERB = frozenset({
    'advanced', 'distributed', 'embedded', 'supervised', 'unsupervised',
    'guided', 'mixed', 'related', 'limited', 'closed', 'red', 'tied',
    'extended', 'shared', 'untrained', 'pretrained', 'integrated',
    'detailed', 'experienced', 'qualified', 'skilled', 'fine-tuned',
    'pre-trained', 'self-paced',
})


def _starts_with_past_tense_verb(text: str) -> bool:
    """Heuristic: a bullet that starts with a past-tense verb (a word
    ending in -ed, longer than 3 characters) is almost certainly an
    action / achievement bullet, not a course-name leftover. The
    _PAST_TENSE_NON_VERB whitelist exempts -ed words that act as
    adjectives or nouns ("Supervised Learning")."""
    parts = text.split()
    if not parts:
        return False
    first = parts[0].lower().rstrip(',.:;()')
    if not first.endswith('ed') or len(first) <= 3:
        return False
    return first not in _PAST_TENSE_NON_VERB


# A bullet whose content is dominated by soft skills (Communication,
# Teamwork, Leadership, ...) — the kind the LLM still emits in spite of
# the prompt's "no soft skills" rule. Drop these from descriptions
# entirely; they're filler and dilute the JD-relevant signal.
_SOFT_SKILL_TOKENS = (
    'communication', 'communications', 'communicating',
    'teamwork', 'team work', 'collaboration', 'collaborated',
    'leadership', 'lead by example',
    'adaptability', 'adapting', 'flexibility',
    'problem-solving', 'problem solving',
    'critical thinking',
    'time management', 'time-management',
    'interpersonal', 'people skills',
    'cross-team', 'cross team', 'cross-functional',
)
_SOFT_SKILL_BULLET_OPENER_RE = re.compile(
    r"^\s*(?:developed|built|gained|cultivated|honed|"
    r"strengthened|enhanced|improved|grew|expanded|practi[sc]ed|"
    r"showed|demonstrated)\s+"
    r"(?:my\s+|the\s+|strong\s+|effective\s+|professional\s+)?"
    r"(?:soft|interpersonal|personal|people)\s+skills\b",
    re.IGNORECASE,
)
# Inline bullet markers an LLM sometimes embeds inside a single string
# bullet (e.g. "Topics:\n• Python\n• SQL").
_EMBEDDED_BULLET_RUN_RE = re.compile(
    r"(?:^|\n)[ \t]*[•*\-][ \t]+([^\n]+)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fuzzy_in(name: str, pool_canon: set[str], pool_pretty: list[str]) -> bool:
    """Return True if ``name`` matches anything in the plan pool — exact
    canonical match OR SequenceMatcher ≥ ``_FUZZY_MATCH_CUTOFF`` against
    any pool entry OR canonical-substring containment.

    The substring rule (Round 1.5.3) catches the case where the LLM
    truncated a project name: it emitted ``"ACR-QA"`` while the plan
    has ``"ACR-QA — Automated Code Review Platform"``. SequenceMatcher
    on those two strings scores under 0.85 because the lengths differ
    so much; canonical-substring containment is the right test (both
    canonicalise so 'acrqa' is a prefix of
    'acrqaautomatedcodereviewplatform').
    """
    c = _canonical(name)
    if not c:
        return False
    if c in pool_canon:
        return True
    # Substring containment in either direction. Cap the short side at
    # ≥ 4 chars so single tokens don't match every long name.
    if len(c) >= 4:
        for pc in pool_canon:
            if not pc:
                continue
            if c in pc or pc in c:
                return True
    low = name.lower()
    for candidate in pool_pretty:
        score = SequenceMatcher(None, low, candidate.lower()).ratio()
        if score >= _FUZZY_MATCH_CUTOFF:
            return True
    return False


# PR1 Fix 3 — unambiguously-technical terms that never appear in real
# coursework titles. Hit on any of these → reject the bullet from
# coursework consolidation. Word-boundary match, case-insensitive.
_TECHNICAL_TOKENS = (
    'Docker', 'Kubernetes', 'Terraform', 'Ansible', 'Jenkins',
    'Prometheus', 'Grafana', 'AWS', 'GCP', 'Azure', 'GitHub Actions',
    'GitLab CI', 'CI/CD', 'IaC', 'RTO', 'RPO', 'HPA', 'k6', 'OWASP',
    'SARIF', 'FastAPI', 'Flask', 'Django', 'Spring Boot', 'RAG', 'LLM',
    'LLaMA', 'TensorFlow', 'PyTorch', 'scikit-learn', 'CNN', 'RNN',
    'LSTM', 'Pandas', 'NumPy', 'MLflow', 'Hugging Face', 'Streamlit',
    'Power BI', 'OpenCV', 'PySpark', 'pgvector', 'Groq', 'Supabase',
)
# Build a single compiled regex: any token, word-boundary, case-insensitive.
# Escape each token (`Spring Boot` and `CI/CD` have special chars) and
# anchor with lookarounds that exclude adjacent word chars — same shape
# as scoring._count_skill_occurrences.
_TECHNICAL_TOKEN_RE = re.compile(
    '|'.join(
        rf"(?<!\w){re.escape(tok)}(?!\w)"
        for tok in _TECHNICAL_TOKENS
    ),
    re.IGNORECASE,
)
# URL-ish substrings — any of these in a bullet means it's a real
# achievement (link to source), not a course-name leftover.
_URL_INDICATOR_RE = re.compile(
    r"https?://|github\.com|gitlab\.com|kaggle\.com",
    re.IGNORECASE,
)
# Parenthesised acronym / tech-list pattern, e.g. "(EC2, S3, IAM modules)"
# — the all-caps inside parens is a strong signal of a capstone artefact,
# not a course name. Requires uppercase start + 4+ chars + only
# uppercase letters, digits, comma, slash, space, hyphen, period inside.
_PARENS_TECHLIST_RE = re.compile(r"\([A-Z][A-Z0-9, /\-.]{3,}\)")


def _coursework_reject_reason(text: str) -> str | None:
    """Return a short rule-id string if `text` should NOT be treated as
    coursework, or None if it passes every rejection check.

    Split out from `_is_coursework_bullet` so the consolidation loop
    can log WHY a near-coursework-looking bullet got rejected. Keeps
    the boolean predicate simple and the diagnostic surface area in
    one place.
    """
    if not text:
        return 'empty'
    s = text.strip()
    if not s:
        return 'empty'
    # PR1 Fix 3 — the three new high-confidence rejections run FIRST so
    # they win over the less-informative digit/colon/verb checks: a URL
    # contains a `.com` (would falsely match terminal_punctuation),
    # `(EC2, S3, IAM modules)` contains digits (would match
    # contains_digit), and `Provisioned (AWS, GCP, ...)` trips the
    # past-tense verb heuristic. Whichever rule we report drives the
    # diagnostic log line, so reporting the most specific one wins.
    if _TECHNICAL_TOKEN_RE.search(s):
        return 'technical_token'
    if _URL_INDICATOR_RE.search(s):
        return 'url'
    if _PARENS_TECHLIST_RE.search(s):
        return 'parens_techlist'
    # Multi-sentence or terminal punctuation → real prose / list header.
    if '.' in s.rstrip('.') or s.endswith(('!', '?', ':')):
        return 'terminal_punctuation'
    if any(c.isdigit() for c in s):
        return 'contains_digit'
    if ':' in s:
        return 'midbullet_colon'
    words = s.split()
    if not words:
        return 'empty'
    if len(words) > _COURSEWORK_MAX_WORDS:
        return 'too_long'
    if len(words) >= 4:
        if _VERB_START_RE.match(s):
            return 'action_verb_start'
        if _starts_with_past_tense_verb(s):
            return 'past_tense_verb_start'
    return None


def _is_coursework_bullet(text: str) -> bool:
    """Boolean wrapper around _coursework_reject_reason. See that helper
    for the full rule list.

    Round 1.5.2 — tightened to fix the DevOps regression where capstone
    metrics like "3 Grafana dashboards (19 panels)" got mis-classified
    as coursework and folded into a fake "Coursework included: ..."
    line.

    PR1 Fix 3 — three more rejections (TECHNICAL_TOKEN, URL, parens
    techlist) catch the remaining capstone artefacts that slipped
    through the digit/colon checks.
    """
    return _coursework_reject_reason(text) is None


def _is_soft_skill_bullet(text: str) -> bool:
    """A bullet whose content is dominated by soft-skill nouns. Used by
    ``filter_soft_skill_bullets`` to drop them from descriptions — the
    JD-tailored resume should never claim "Developed cross-team
    communication, problem-solving, and adaptability" because that's
    filler that crowds out actual evidence."""
    if not text:
        return False
    s = text.strip()
    if not s:
        return False
    # Pattern 1: explicit "Developed soft skills..." opener.
    if _SOFT_SKILL_BULLET_OPENER_RE.search(s):
        return True
    # Pattern 2: bullet content is mostly soft-skill nouns. Count token
    # hits — 2+ in a short bullet (< 25 words) means filler. Longer
    # bullets that incidentally mention "communication" once stay.
    low = s.lower()
    hits = sum(1 for token in _SOFT_SKILL_TOKENS if token in low)
    if hits >= 2 and len(s.split()) < 25:
        return True
    return False


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def normalize_titles(resume: dict) -> dict:
    """Title-case any ALL-CAPS / all-lowercase ``experience[*].title`` the
    sanitizer missed (e.g. when generation pulled the raw CV string and
    bypassed the upstream cleanup). Mixed-case titles are left alone."""
    for exp in resume.get('experience') or []:
        if not isinstance(exp, dict):
            continue
        title = exp.get('title') or ''
        new_title = _title_case_with_acronyms(title)
        if new_title != title:
            exp['title'] = new_title
    # Same treatment for education degree fields — "BACHELOR OF SCIENCE"
    # is the same parser artefact.
    for edu in resume.get('education') or []:
        if not isinstance(edu, dict):
            continue
        for key in ('degree', 'field', 'institution'):
            v = edu.get(key) or ''
            new = _title_case_with_acronyms(v)
            if new != v:
                edu[key] = new
    return resume


def filter_soft_skills(resume: dict) -> dict:
    """Drop any entry in ``resume.skills`` that matches the soft-skill
    blocklist. The blocklist lives in ``profile_sanitizer`` so the
    upstream sanitizer and this post-LLM filter never drift."""
    skills = resume.get('skills') or []
    if not isinstance(skills, list):
        return resume
    out: list[str] = []
    dropped: list[str] = []
    for entry in skills:
        if isinstance(entry, dict):
            name = (entry.get('name') or '').strip()
        else:
            name = str(entry or '').strip()
        if not name:
            continue
        if _matches_soft_skill_blocklist(_canonical(name)):
            dropped.append(name)
            continue
        out.append(name)
    resume['skills'] = out
    if dropped:
        logger.info("resume_normalizer: dropped %d soft skill(s): %s", len(dropped), dropped)
    return resume


def enforce_skill_hard_cap(resume: dict, cap: int = _HARD_SKILL_CAP) -> dict:
    """Truncate ``resume.skills`` at ``cap`` entries, preserving the LLM's
    ordering (the prompt asks for JD-required first)."""
    skills = resume.get('skills') or []
    if isinstance(skills, list) and len(skills) > cap:
        resume['skills'] = skills[:cap]
        logger.info("resume_normalizer: capped skills %d → %d", len(skills), cap)
    return resume


def _strip_first_person_field(value: Any) -> Any:
    """Apply ``_strip_first_person`` to a value that may be a str OR a
    list of strs. Anything else is returned unchanged."""
    if isinstance(value, str):
        return _strip_first_person(value)
    if isinstance(value, list):
        return [_strip_first_person(s) if isinstance(s, str) else s for s in value]
    return value


def strip_first_person_from_resume(resume: dict) -> dict:
    """Last-line defence: regex-strip ``I / my / me`` (word-boundary) from
    every prose-bearing field. Re-capitalizes sentence starts so the
    output stays grammatical."""
    if 'professional_summary' in resume:
        resume['professional_summary'] = _strip_first_person(
            resume.get('professional_summary') or ''
        )
    if 'objective' in resume:
        resume['objective'] = _strip_first_person(resume.get('objective') or '')
    for exp in resume.get('experience') or []:
        if not isinstance(exp, dict):
            continue
        for key in ('description', 'highlights', 'achievements'):
            if key in exp:
                exp[key] = _strip_first_person_field(exp[key])
    for proj in resume.get('projects') or []:
        if not isinstance(proj, dict):
            continue
        for key in ('description', 'highlights'):
            if key in proj:
                proj[key] = _strip_first_person_field(proj[key])
    return resume


_COURSEWORK_MIN_RUN = 3  # PR1 Fix 3 — was 2; raised to err on the side of
                         # NOT collapsing technical bullets


def _consolidate_bullet_list(bullets: list) -> list:
    """Collapse runs of ``_COURSEWORK_MIN_RUN`` (=3) or more consecutive
    coursework-like bullets into one ``Coursework included: A, B, C.``
    entry. Operates on the bullet list in order, preserving non-course
    bullets verbatim.

    Logs each rejection that fired one of the PR1-added rules
    (TECHNICAL_TOKEN / URL / parens techlist) at INFO so future
    debugging can see why a specific bullet wasn't consolidated.
    """
    if not bullets:
        return bullets
    out: list[Any] = []
    buffer: list[str] = []

    def _flush():
        if len(buffer) >= _COURSEWORK_MIN_RUN:
            out.append(f"Coursework included: {', '.join(buffer)}.")
        else:
            out.extend(buffer)
        buffer.clear()

    # Only log the new rejection reasons — the older ones (digit, colon,
    # length, verb-start) are already self-explanatory and would just
    # be log noise for every action bullet in the resume.
    _LOGGED_REASONS = {'technical_token', 'url', 'parens_techlist'}

    for b in bullets:
        if isinstance(b, str):
            reason = _coursework_reject_reason(b)
            if reason is None:
                buffer.append(b.strip())
                continue
            if reason in _LOGGED_REASONS:
                snippet = b.strip()
                if len(snippet) > 100:
                    snippet = snippet[:100] + '…'
                logger.info(
                    "resume_normalizer: coursework-consolidate skipped bullet "
                    "(rule=%s): %r", reason, snippet,
                )
        _flush()
        out.append(b)
    _flush()
    return out


def _split_embedded_bullets(text: str) -> list[str]:
    """If a single bullet string has embedded ``\\n• Course`` runs, split
    the prelude from the embedded items so ``_consolidate_bullet_list``
    can then collapse them. Returns a list of strings (one item if no
    embedded bullets were found).

    A prelude that ends with ``:`` is treated as a list-introducer header
    (e.g. ``"Coursework consisted of:"``) and dropped — the consolidated
    ``Coursework included: ...`` line that follows already carries the
    same intent, so keeping both is redundant. Preludes without a
    trailing colon are kept as their own bullet (they're real content).
    """
    if not isinstance(text, str) or not text:
        return [text] if text else []
    matches = list(_EMBEDDED_BULLET_RUN_RE.finditer(text))
    if not matches:
        return [text]
    first_start = matches[0].start()
    prelude_raw = text[:first_start].strip()
    drop_prelude = prelude_raw.endswith(':')
    prelude = '' if drop_prelude else prelude_raw
    items = [m.group(1).strip() for m in matches if m.group(1).strip()]
    out: list[str] = []
    if prelude:
        out.append(prelude)
    out.extend(items)
    return out


def consolidate_coursework(resume: dict) -> dict:
    """For every ``experience[*].description`` and ``projects[*].description``,
    split any embedded ``\\n• Course`` runs into separate entries, then
    collapse runs of coursework-like bullets into a single
    ``Coursework included: ...`` line."""
    for exp in resume.get('experience') or []:
        if not isinstance(exp, dict):
            continue
        desc = exp.get('description')
        if isinstance(desc, str):
            split = _split_embedded_bullets(desc)
            exp['description'] = _consolidate_bullet_list(split) if len(split) > 1 else desc
        elif isinstance(desc, list):
            flattened: list[Any] = []
            for entry in desc:
                if isinstance(entry, str):
                    flattened.extend(_split_embedded_bullets(entry))
                else:
                    flattened.append(entry)
            exp['description'] = _consolidate_bullet_list(flattened)
    for proj in resume.get('projects') or []:
        if not isinstance(proj, dict):
            continue
        desc = proj.get('description')
        if isinstance(desc, list):
            flattened = []
            for entry in desc:
                if isinstance(entry, str):
                    flattened.extend(_split_embedded_bullets(entry))
                else:
                    flattened.append(entry)
            proj['description'] = _consolidate_bullet_list(flattened)
    return resume


def normalize_bullet_punctuation(resume: dict) -> dict:
    """Make every bullet in a description list end the same way, AND
    drop orphan list-introducer stubs ("Capstone project highlights:")
    that the LLM emits when it intended to follow up with sub-bullets
    but didn't.

    Policy:
      - A bullet ending with ``:`` is a stub header — drop it. v1's
        bug was appending ``.`` to it, producing ``"foo:."`` (the
        audit flagged these as broken template scaffolding).
      - If ANY remaining bullet in the description ends with terminal
        punctuation, every other bullet gets a trailing period.
      - If no bullet ends with terminal punctuation, leave the list
        alone.
    """
    def _normalize(bullets: list) -> list:
        if not bullets:
            return bullets
        # Step 1: drop orphan list-introducer stubs.
        cleaned: list = []
        for b in bullets:
            if isinstance(b, str) and b.rstrip().endswith(':'):
                # ":" stubs that have less than ~5 words are headers
                # like "Capstone project highlights:" — drop. A real
                # bullet like "Built X in 3 phases:" probably has its
                # content right after the colon and would be paired
                # with a follow-on bullet — also drop, because the
                # next bullets are the content.
                continue
            cleaned.append(b)
        if not cleaned:
            return cleaned
        # Step 2: punctuation consistency.
        any_terminal = any(
            isinstance(b, str) and b.rstrip().endswith(('.', '!', '?'))
            for b in cleaned
        )
        if not any_terminal:
            return cleaned
        out = []
        for b in cleaned:
            if not isinstance(b, str):
                out.append(b)
                continue
            s = b.rstrip()
            if not s:
                out.append(s)
                continue
            if not s.endswith(('.', '!', '?')):
                s = s + '.'
            out.append(s)
        return out

    for section_key in ('experience', 'projects'):
        for item in resume.get(section_key) or []:
            if not isinstance(item, dict):
                continue
            for key in ('description', 'highlights'):
                value = item.get(key)
                if isinstance(value, list):
                    item[key] = _normalize(value)
    return resume


def filter_soft_skill_bullets(resume: dict) -> dict:
    """Drop bullets whose content is dominated by soft skills from every
    experience and project description. The LLM emits these despite the
    prompt's "no soft skills" rule (e.g. "Developed soft skills
    including cross-team communication, problem-solving, and
    adaptability...") — they're filler and crowd out JD-relevant
    evidence."""
    dropped_total = 0
    for section_key in ('experience', 'projects'):
        for item in resume.get(section_key) or []:
            if not isinstance(item, dict):
                continue
            for key in ('description', 'highlights'):
                value = item.get(key)
                if isinstance(value, list):
                    cleaned = [b for b in value
                               if not (isinstance(b, str) and _is_soft_skill_bullet(b))]
                    dropped_total += len(value) - len(cleaned)
                    item[key] = cleaned
                elif isinstance(value, str):
                    if _is_soft_skill_bullet(value):
                        item[key] = ''
                        dropped_total += 1
    if dropped_total:
        logger.info("resume_normalizer: dropped %d soft-skill bullet(s)", dropped_total)
    return resume


_BANNED_SUMMARY_OPENERS_RE = re.compile(
    # Round 1.5.2 base set.
    r"^\s*(?:Highly motivated|Results-driven|Detail-oriented|"
    r"Passionate|Dedicated|Hard-working|Self-motivated|"
    r"Energetic|Goal-oriented|Dynamic and|Innovative and|"
    # PR 3c additions (2026-05-16) — phrases the Zeyad audit hit. Bare
    # ``Innovative`` and ``Strategic`` openers are recruiter jargon; the
    # paired ``Innovative and`` / ``Dynamic and`` patterns above stay
    # for back-compat. ``Proven`` covers ``Proven track record`` style
    # openers. ``Self-starter`` mirrors the ``Self-motivated`` ban.
    r"Highly skilled|Highly accomplished|Highly experienced|"
    r"Highly qualified|Self-starter|Innovative|Strategic|Proven)\s+",
    re.IGNORECASE,
)
# Match "1 year of experience", "2+ years of experience", "less than
# a year of experience", "up to 2 years of experience", etc.
_YOE_CLAIM_RE = re.compile(
    r"\b(?:with|having)?\s*"
    r"(?:up\s+to\s+|less\s+than\s+|over\s+|\d+\+?\s*|a\s+|an\s+|"
    r"early-career\s+|recent\s+)"
    r"(?:year|years)\s+of(?:\s+\w+)?\s+experience"
    r"(?:\s+in\s+[^.]*)?",
    re.IGNORECASE,
)
# Detect a pipe-separated multi-title header at the START of the summary.
# Matches "<title>(\| <title>){1,4}\s+<connector>" where title is up to
# 4 words and connector is a body-starting word/punctuation.
# Belt-and-suspenders for the round-3 prompt rule the LLM kept ignoring.
_PIPE_TITLE_HEADER_RE = re.compile(
    # Non-greedy ``{0,3}?`` on each title's extra-word slot stops the
    # regex from eating body words ("with applied experience", "hands-
    # on Python work") as part of the last pipe-title. Hyphen-sep
    # requires whitespace on BOTH sides so an embedded hyphen in
    # "hands-on" can't terminate the title group.
    r"^\s*(?P<primary>[\w/&\-]+(?:\s+[\w/&\-]+){0,3}?)"
    r"(?:\s*\|\s*[\w/&\-]+(?:\s+[\w/&\-]+){0,3}?){1,4}"
    r"(?P<sep>\s+(?:with|focused|focusing|skilled|experienced|having|"
    r"specializing|specialized|who|that)\b|\s*,\s*|\s*\.\s*|\s+[\-—–]\s+)",
    re.IGNORECASE,
)


def _strip_pipe_title_header(summary: str, jd_title: str = '') -> str:
    """If the summary leads with "X | Y | Z [connector] ..." rewrite to
    "<jd_title> [connector] ..." (falling back to the primary title when
    no JD title is provided)."""
    if '|' not in summary[:120]:
        return summary
    m = _PIPE_TITLE_HEADER_RE.match(summary)
    if not m:
        return summary
    title = (jd_title or m.group('primary')).strip()
    sep = m.group('sep')
    # Connector is a word boundary — keep the leading whitespace so the
    # body's first word is correctly spaced. For a punctuation sep
    # (", " or ". "), replace with a single space and uppercase the body.
    rest = summary[m.end():]
    if re.match(r"^\s+(?:with|focused|focusing|skilled|experienced|having|"
                r"specializing|specialized|who|that)\b", sep, re.IGNORECASE):
        return f"{title}{sep}{rest}"
    # Punctuation separator: start a new sentence after the title.
    rest = rest.lstrip()
    if rest:
        rest = rest[0].upper() + rest[1:]
    return f"{title}. {rest}" if rest else title


def clean_summary_phrasing(resume: dict, job=None) -> dict:
    """Strip recruiter-tell phrases and unsupported YoE claims from the
    LLM's generated summary.

    Targets:
      - "Highly motivated", "Results-driven", "Detail-oriented",
        "Passionate", "Dedicated" — empty signal words that every junior
        resume opens with. The audit called these "dead on arrival
        recruiter jargon".
      - "1 year of experience", "up to 2 years of experience",
        etc. — the LLM extrapolates these from a 6-month role plus an
        unrelated 2-month internship, which doesn't add up to 12+
        months in the JD's discipline. The prompt's YoE rule is
        ignored often; this is the safety net.
    """
    summary = (resume.get('professional_summary') or '').strip()
    if not summary:
        return resume
    cleaned = summary
    # 2026-05-29 round-4: the LLM keeps emitting "DS | AI Engineer | DA
    # with body" headers despite the round-3 prompt rule. Strip
    # deterministically. JD title (from job.title) is the rewrite anchor;
    # falls back to the LLM's first title if no job.
    jd_title = (getattr(job, 'title', '') or '').strip() if job is not None else ''
    after_strip = _strip_pipe_title_header(cleaned, jd_title)
    if after_strip != cleaned:
        logger.info(
            "resume_normalizer: stripped pipe-title summary header (jd_title=%r)",
            jd_title or '<unset>',
        )
        cleaned = after_strip
    # Strip leading recruiter-jargon openers; re-capitalise the next word.
    new_cleaned = _BANNED_SUMMARY_OPENERS_RE.sub('', cleaned)
    if new_cleaned and new_cleaned != cleaned:
        new_cleaned = new_cleaned.strip()
        if new_cleaned:
            new_cleaned = new_cleaned[0].upper() + new_cleaned[1:]
        cleaned = new_cleaned
    # Strip YoE claims. They typically appear as a prepositional
    # clause; remove plus the leading "with" / "having".
    cleaned = _YOE_CLAIM_RE.sub('', cleaned)
    # Tidy whitespace + orphan commas / "with" left behind.
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r"\bwith\s+\.", ".", cleaned)
    cleaned = re.sub(r"\s+\.", ".", cleaned)
    cleaned = cleaned.strip()
    if cleaned != summary:
        logger.info(
            "resume_normalizer: cleaned summary phrasing (len %d → %d)",
            len(summary), len(cleaned),
        )
        resume['professional_summary'] = cleaned
    return resume


def enforce_verbatim_titles(resume: dict, profile_data: dict | None = None) -> dict:
    """Snap experience titles back to the verbatim CV title when the
    LLM paraphrases. The audit caught "DevOps Engineering Trainee"
    (CV) being rendered as "DevOps Engineer Trainee" (LLM) — a small
    edit that risks LinkedIn-verification mismatch for an entry-level
    candidate.

    Uses fuzzy match (SequenceMatcher ≥ 0.75) against the CV's
    experience titles. When a match is found and the canonical text
    differs, snap back to the CV form. Otherwise leave as-is (the LLM
    may have a legitimate cleanup).
    """
    if not isinstance(profile_data, dict):
        return resume
    cv_titles = []
    for exp in (profile_data.get('experiences') or []):
        if isinstance(exp, dict):
            t = (exp.get('title') or '').strip()
            if t:
                cv_titles.append(t)
    if not cv_titles:
        return resume
    for exp in resume.get('experience') or []:
        if not isinstance(exp, dict):
            continue
        llm_title = (exp.get('title') or '').strip()
        if not llm_title:
            continue
        # Exact match → no-op.
        if any(llm_title == t for t in cv_titles):
            continue
        # Best fuzzy match.
        best_score = 0.0
        best_cv_title = ''
        for cv_t in cv_titles:
            score = SequenceMatcher(None, llm_title.lower(), cv_t.lower()).ratio()
            if score > best_score:
                best_score = score
                best_cv_title = cv_t
        # Snap back when reasonably similar (≥ 0.75) but not identical.
        if best_score >= 0.75 and best_cv_title and best_cv_title != llm_title:
            logger.info(
                "resume_normalizer: snapped title %r → %r (CV verbatim, sim=%.2f)",
                llm_title, best_cv_title, best_score,
            )
            exp['title'] = best_cv_title
    return resume


def backfill_summary(resume: dict, job=None) -> dict:
    """If professional_summary is empty / whitespace, synthesize a
    minimal one from the candidate's experience + top skills.

    The LLM sometimes returns "" for the summary (especially when the
    prompt's "no first-person, no third-person-by-name" constraint
    conflicts with the model's instinct) — that leaves the rendered
    docx with no summary section, which reads as missing-data to a
    recruiter. A short, deterministic backfill is better than nothing.

    Picks the experience whose title best matches the JD title (when a
    job is provided) instead of always defaulting to the most-recent
    role. For a "Data Scientist" JD against a candidate with [Digital
    Transformation Intern, IT Intern, AI & Data Science Trainee], the
    AI/DS Trainee is the right summary lead, not the DT Intern that
    happens to be chronologically newest.

    Format: "<Title> with hands-on <skill1>, <skill2>, <skill3> work."
    Falls back to no-op if there's not enough data to synthesize.
    """
    summary = (resume.get('professional_summary') or '').strip()
    if summary:
        return resume
    exps = resume.get('experience') or []
    if not exps:
        return resume
    # Pick the experience whose title shares the most word tokens with
    # the JD title. Falls back to the first experience when no JD or
    # when nothing matches.
    jd_title = ''
    if job is not None:
        jd_title = (getattr(job, 'title', '') or '').lower()
    jd_tokens = set(re.findall(r'\w+', jd_title)) - {
        'a', 'an', 'the', 'of', 'for', 'and', 'or', 'to', 'with',
        'at', 'in', 'on', 'by', 'as', 'engineer', 'developer',
    }
    best_idx = 0
    best_score = -1
    for i, exp in enumerate(exps):
        if not isinstance(exp, dict):
            continue
        title_tokens = set(re.findall(r'\w+', (exp.get('title') or '').lower()))
        overlap = len(title_tokens & jd_tokens) if jd_tokens else 0
        if overlap > best_score:
            best_score = overlap
            best_idx = i
    chosen = exps[best_idx] if isinstance(exps[best_idx], dict) else {}
    title = (chosen.get('title') or '').strip()
    if not title:
        return resume
    # Use the JD title (when known) to lead the summary instead of the
    # candidate's role title — recruiter scan reads the role first, and
    # "Junior DevOps Engineer with hands-on Docker..." beats "DevOps
    # Engineering Trainee with hands-on..." for ATS alignment.
    jd_title_clean = (getattr(job, 'title', '') or '').strip() if job else ''
    lead_title = jd_title_clean or title

    skills = [s for s in (resume.get('skills') or []) if s]
    top = skills[:4]
    if top:
        if len(top) == 1:
            skills_phrase = top[0]
        elif len(top) == 2:
            skills_phrase = ' and '.join(top)
        else:
            skills_phrase = ', '.join(top[:-1]) + f", and {top[-1]}"
        # Round 1.5: dropped the "drawing on the X role and project
        # work" clause — the audit read it as the AI describing its
        # own resume strategy ("meta-narration"). A human would never
        # write that. Plain factual statement is better.
        text = f"{lead_title} with hands-on {skills_phrase} experience."
    else:
        text = f"{lead_title} with practical project experience."
    resume['professional_summary'] = text
    logger.info(
        "resume_normalizer: backfilled empty professional_summary (len=%d, "
        "lead='%s', experience='%s', jd_overlap=%d)",
        len(text), lead_title, title, max(best_score, 0),
    )
    return resume


# Generic "category" suffixes that follow a real skill name without
# meaningfully changing what the skill is. "CI/CD tools" is the same
# concept as "CI/CD"; "AWS platform" is the same as "AWS". Used by the
# prefix dedup rule to avoid the false-positive where "Docker Compose"
# (a distinct product) gets deduped against "Docker".
_GENERIC_SKILL_SUFFIXES = (
    'tools', 'platform', 'platforms', 'framework', 'frameworks',
    'library', 'libraries', 'service', 'services',
    'tooling', 'technology', 'technologies',
)

_CONJUNCTION_RE = re.compile(r'[&+/]|\b(?:and|or)\b', re.IGNORECASE)


def _skill_tokens(name: str) -> set[str]:
    """Tokenize a skill name into its content tokens (lowercased, alphanumeric).

    Used by the token-subset dedup rule to catch "SQL" inside "Databases & SQL"
    and "Supervised Learning" inside "Supervised & Unsupervised Learning" -
    cases _canonical's alphanum-strip flattens into different strings.
    """
    if not name:
        return set()
    parts = re.split(r'[\s&+/,;]+|\b(?:and|or)\b', name.lower())
    return {re.sub(r'[^a-z0-9]', '', p) for p in parts if re.sub(r'[^a-z0-9]', '', p)}


def _has_conjunction(name: str) -> bool:
    """True if the name contains an explicit conjunction (&, +, /, 'and', 'or').

    Distinguishes "Supervised & Unsupervised Learning" (conjunction-extended)
    from "Docker Compose" (product name), so the token-subset dedup rule
    doesn't false-positive on the latter.
    """
    return bool(_CONJUNCTION_RE.search(name or ''))
# Match a trailing parenthesised acronym, e.g.
# "Continuous Integration and Continuous Delivery (CI/CD)" → "CI/CD".
_PARENS_ACRONYM_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9/&._-]{1,15})\)\s*$")


def _parens_acronym(name: str) -> str:
    """Return the trailing parenthesised acronym from a skill name, or
    '' if none. Stripped to canonical form for comparison."""
    if not name:
        return ''
    m = _PARENS_ACRONYM_RE.search(name)
    return _canonical(m.group(1)) if m else ''


def _is_near_duplicate_skill(
    existing: list[tuple[str, str]],
    new_name: str,
    new_canon: str,
) -> bool:
    """Detect near-duplicate skills the bare canonical-key dedup misses.

    Catches two patterns ONLY (the v1 blind-suffix rule was over-firing
    — SQL got deduped against PostgreSQL, Docker Compose against Docker):

      1. Acronym-in-parentheses match: "CI/CD" (canon='cicd') vs
         "Continuous Integration and Continuous Delivery (CI/CD)"
         (canon='continuousintegration...cicd'). The verbose form
         has the acronym in trailing parens; if that acronym's
         canonical matches an existing skill (or vice versa), dedup.

      2. Whitelisted-suffix prefix match: "CI/CD" vs "CI/CD tools".
         The longer canonical starts with the shorter AND the
         remainder is in _GENERIC_SKILL_SUFFIXES. Stops "Docker"
         vs "Docker Compose" from being a false positive.

    First-seen wins.

    ``existing`` is a list of (name, canon) pairs because the parens-
    acronym rule needs the original name (canonical loses the parens).
    """
    if not new_canon:
        return False
    new_parens_acro = _parens_acronym(new_name)
    for ec_name, ec_canon in existing:
        if not ec_canon:
            continue
        if ec_canon == new_canon:
            return True
        # (1) Acronym-in-parens — either direction.
        ec_parens_acro = _parens_acronym(ec_name)
        if new_parens_acro and new_parens_acro == ec_canon:
            return True
        if ec_parens_acro and ec_parens_acro == new_canon:
            return True
        if new_parens_acro and ec_parens_acro and new_parens_acro == ec_parens_acro:
            return True
        # (2) Whitelisted-suffix prefix match — either direction.
        for short, long in ((ec_canon, new_canon), (new_canon, ec_canon)):
            if len(short) < 3 or len(long) <= len(short):
                continue
            if not long.startswith(short):
                continue
            remainder = long[len(short):]
            if any(remainder == suf for suf in _GENERIC_SKILL_SUFFIXES):
                return True
        # (3) Token-subset where the LONGER name contains an explicit
        # conjunction (&, +, /, "and", "or"). The longer form is
        # implicitly "shorter + extra terms joined by a conjunction",
        # which makes the shorter redundant. Catches the cases _canonical
        # flattens into different strings (alphanum-strip loses the
        # word boundaries):
        #   "SQL" + "Databases & SQL" → drop the second-seen
        #   "Supervised Learning" + "Supervised & Unsupervised Learning"
        # Safe because connector-less compounds (Docker / Docker Compose,
        # PostgreSQL / SQL) don't satisfy the conjunction requirement.
        new_tokens = _skill_tokens(new_name)
        ec_tokens = _skill_tokens(ec_name)
        if new_tokens and ec_tokens and new_tokens != ec_tokens:
            if ec_tokens < new_tokens and _has_conjunction(new_name):
                return True
            if new_tokens < ec_tokens and _has_conjunction(ec_name):
                return True
    return False


def trim_skills_to_plan(resume: dict, plan) -> dict:
    """Replace the LLM's Skills list with the planner's
    ``skills_to_list``, re-filtered for soft skills and near-duplicates,
    then top-up from any remaining LLM extras (also filtered) up to the
    hard cap.

    The planner builds skills_to_list in JD-must-have order, but the
    gap analyzer often categorises JD soft-skill phrases ("Agile",
    "Multitasking", "Time management", "Communication") as matched
    must-haves. Without re-filtering here, those leak straight into
    the Skills section.

    Near-duplicate dedup: the plan list often contains both "CI/CD"
    and "CI/CD tools" because the JD parser pulled them as separate
    tokens. The first-seen wins.

    Skip if the plan has no skills (don't wipe just because gap
    analysis returned empty).
    """
    if plan is None or not getattr(plan, 'skills_to_list', None):
        return resume
    raw_plan = [s for s in plan.skills_to_list if s]
    if not raw_plan:
        return resume

    kept_pairs: list[tuple[str, str]] = []  # (name, canon) for dedup context
    kept: list[str] = []
    dropped_soft: list[str] = []
    dropped_dup: list[str] = []

    def _try_add(name: str) -> bool:
        nonlocal kept, kept_pairs
        if not name:
            return False
        c = _canonical(name)
        if not c:
            return False
        if _matches_soft_skill_blocklist(c):
            dropped_soft.append(name)
            return False
        if _is_near_duplicate_skill(kept_pairs, name, c):
            dropped_dup.append(name)
            return False
        kept.append(name.strip())
        kept_pairs.append((name.strip(), c))
        return True

    # Plan's ordered list, filtered.
    for s in raw_plan:
        _try_add(s)

    # Append LLM extras (also filtered for soft skills + dedup).
    llm_skills = resume.get('skills') or []
    if isinstance(llm_skills, list):
        for entry in llm_skills:
            if len(kept) >= _HARD_SKILL_CAP:
                break
            name = entry.get('name') if isinstance(entry, dict) else str(entry or '')
            _try_add(name)

    resume['skills'] = kept[:_HARD_SKILL_CAP]

    log_bits = [f"final={len(resume['skills'])}"]
    if dropped_soft:
        log_bits.append(f"dropped_soft={dropped_soft}")
    if dropped_dup:
        log_bits.append(f"dropped_dup={dropped_dup}")
    logger.info("resume_normalizer: skills via plan + filters (%s)", ", ".join(log_bits))
    return resume


def trim_projects_to_plan(resume: dict, plan) -> dict:
    """Drop any project whose name doesn't appear in ``plan.projects``.
    Caps the result at ``_PROJECT_CAP``. Skips if the plan has no
    projects (defensive — never wipe the projects section just because
    the planner returned empty)."""
    if plan is None or not getattr(plan, 'projects', None):
        return resume
    pool_pretty = [p.name for p in plan.projects if p.name]
    pool_canon = {_canonical(n) for n in pool_pretty}
    if not pool_canon:
        return resume
    projects = resume.get('projects') or []
    kept: list[dict] = []
    dropped_names: list[str] = []
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        name = (proj.get('name') or '').strip()
        if _fuzzy_in(name, pool_canon, pool_pretty):
            kept.append(proj)
        else:
            dropped_names.append(name)
    # Hard cap.
    if len(kept) > _PROJECT_CAP:
        kept = kept[:_PROJECT_CAP]
    resume['projects'] = kept
    if dropped_names:
        logger.info(
            "resume_normalizer: dropped %d project(s) not in plan: %s",
            len(dropped_names), dropped_names,
        )
    return resume


def trim_certs_to_plan(resume: dict, plan) -> dict:
    """Cap the certifications list at ``_CERT_CAP``. Does NOT filter by
    plan membership.

    Round 1.5 (DevOps audit): the previous "drop certs not in plan"
    filter was over-aggressive — it removed certs that the recruiter
    would clearly want to see (e.g. "Introduction to Software
    Testing" on a DevOps resume). The auditor's recommendation was
    explicit: "Include ALL certifications from JSON". Just keep the
    candidate's full cert list, capped at 8.

    PR2b Fix C — Option X (cap-only + JD-relevance ranking). The
    Round 1.5 invariant is preserved: for any user with ≤ _CERT_CAP
    certs, NOTHING is dropped. When the cap DOES truncate (e.g. a
    user with 13 certs), we now order the list so plan-membership
    certs (high JD relevance) come first and survive the cap, while
    low-signal certs end up in the truncation tail. This solves the
    user-visible problem (low-signal DataCamp fundamentals crowding
    out relevant Coursera/DeepLearning.AI certs) without
    reintroducing the v1-era "auditor wanted to see this and we
    dropped it" regression.

    Ordering rule (when sorting matters — i.e. count > cap):
      1. Certs whose canonical name appears in ``plan.certifications``
         (plan order preserved as a tie-breaker).
      2. All other certs, in their original profile order.
    """
    certs = resume.get('certifications') or []
    if not isinstance(certs, list):
        return resume
    kept = [c for c in certs if isinstance(c, dict) and (c.get('name') or '').strip()]
    if len(kept) <= _CERT_CAP:
        # No truncation — Round 1.5 invariant: keep every cert in
        # original order, no reranking, no logging surface area.
        resume['certifications'] = kept
        return resume

    # Only rerank when truncation will actually drop entries. This is
    # also where the (X) ranking matters.
    plan_canon_list: list[str] = []
    if plan is not None:
        for name in (getattr(plan, 'certifications', None) or []):
            c = _canonical(name)
            if c and c not in plan_canon_list:
                plan_canon_list.append(c)
    plan_canon_set = set(plan_canon_list)

    in_plan: list[dict] = []
    out_of_plan: list[dict] = []
    for cert in kept:
        cert_canon = _canonical(cert.get('name') or '')
        if cert_canon and cert_canon in plan_canon_set:
            in_plan.append(cert)
        else:
            out_of_plan.append(cert)

    # Sort the in-plan bucket so it follows plan-order — gives the LLM /
    # downstream renderer a stable, JD-relevant top of the list.
    plan_rank = {c: i for i, c in enumerate(plan_canon_list)}
    in_plan.sort(
        key=lambda c: plan_rank.get(_canonical(c.get('name') or ''), 1 << 30),
    )

    ranked = in_plan + out_of_plan
    final = ranked[:_CERT_CAP]
    demoted_names = [c.get('name') for c in ranked[_CERT_CAP:]]
    if demoted_names:
        logger.info(
            "resume_normalizer: cert cap hit (count=%d > cap=%d). "
            "in_plan=%d kept; dropped low-signal: %s",
            len(kept), _CERT_CAP, len(in_plan), demoted_names,
        )
    resume['certifications'] = final
    return resume


# ---------------------------------------------------------------------------
# Plan-as-contract restoration (PR 3a, 2026-05-16)
# ---------------------------------------------------------------------------

def _restore_plan_items(
    resume: dict,
    plan,
    profile_data: dict | None,
    *,
    section_key: str,                      # 'projects' or 'certifications'
    plan_attr: str,                        # 'projects' or 'certifications'
    cap: int,                              # _PROJECT_CAP or _CERT_CAP
    plan_item_to_name,                     # callable: plan-entry -> name string
    source_to_resume_entry,                # callable: source-CV dict -> resume entry dict
    item_label: str,                       # 'project' or 'cert' (for log messages)
) -> dict:
    """Plan-as-contract restoration core (PR 3a).

    Reorders the section so plan-ranked items come first (restored from
    source CV when the LLM dropped them; LLM version kept when the LLM
    kept them), then fills remaining cap slots with LLM-kept items not
    in the plan. This is the "evict LLM extras to make room for plan
    items" semantics — necessary because the Zeyad audit found the LLM
    filling the 8-cert cap with low-relevance picks, leaving no room
    for plan-ranked high-relevance certs under append-only semantics.

    Cap is still respected. The Round 1.5 invariant ("Include ALL
    certifications from JSON" for ≤cap users) still holds because in
    the under-cap case every LLM-kept cert survives — it just gets
    reordered behind plan-ranked items.

    DESIGN NOTE: restored items use the source-CV verbatim copy via
    ``source_to_resume_entry``. For projects this means source-CV
    description / highlights / technologies — NOT LLM-polished bullets.
    The voice gap is small in practice because both LLM-polished and
    source-CV bullets share the past-tense completed-action register
    typical of resume bullets; the alternative (a second LLM pass to
    rewrite restored bullets) doubles the LLM cost per resume.
    """
    if plan is None or not getattr(plan, plan_attr, None):
        return resume
    if not profile_data:
        return resume

    current = list(resume.get(section_key) or [])
    # Index LLM-kept items by canonical name so plan walk can reuse them.
    current_by_canon: dict[str, dict] = {}
    current_pretty: list[str] = []
    current_canon_set: set[str] = set()
    for c in current:
        if not isinstance(c, dict):
            continue
        name = c.get('name', '')
        if not name:
            continue
        canon = _canonical(name)
        if canon and canon not in current_by_canon:
            current_by_canon[canon] = c
            current_canon_set.add(canon)
            current_pretty.append(name)

    profile_items = profile_data.get(section_key, []) or []
    profile_by_canon: dict[str, dict] = {}
    for src in profile_items:
        if not isinstance(src, dict):
            continue
        canon = _canonical(src.get('name', ''))
        if canon and canon not in profile_by_canon:
            profile_by_canon[canon] = src

    final: list[dict] = []
    final_canon: set[str] = set()
    restored_names: list[str] = []

    # Pass 1: walk plan in rank order. For each plan item:
    #   - if LLM kept it (fuzzy match) — reuse LLM-polished version
    #   - if LLM dropped it — restore source-CV verbatim
    #   - if not in source profile — log warning, skip
    for plan_item in getattr(plan, plan_attr):
        plan_name = plan_item_to_name(plan_item)
        plan_canon = _canonical(plan_name)
        if not plan_canon or plan_canon in final_canon:
            continue
        if len(final) >= cap:
            break
        # Check LLM-kept first (fuzzy match, since LLM may have renamed).
        kept = current_by_canon.get(plan_canon)
        if kept is None and _fuzzy_in(plan_name, current_canon_set, current_pretty):
            for ccanon, centry in current_by_canon.items():
                # Find the LLM entry that fuzzy-matched.
                if _fuzzy_in(plan_name, {ccanon}, [centry.get('name', '')]):
                    kept = centry
                    plan_canon = ccanon  # use matched canon so dedupe is correct
                    break
        if kept is not None:
            final.append(kept)
            final_canon.add(plan_canon)
            continue
        src = profile_by_canon.get(plan_canon)
        if not src:
            logger.warning(
                "resume_normalizer: plan-ranked %s %r not in source profile; skipping restoration",
                item_label, plan_name,
            )
            continue
        final.append(source_to_resume_entry(src))
        final_canon.add(plan_canon)
        restored_names.append(src.get('name', ''))

    # Pass 2: fill remaining cap with LLM-kept items not in plan.
    evicted_names: list[str] = []
    for entry in current:
        if not isinstance(entry, dict):
            continue
        canon = _canonical(entry.get('name', ''))
        if not canon:
            continue
        if canon in final_canon:
            continue
        if len(final) >= cap:
            evicted_names.append(entry.get('name', ''))
            continue
        final.append(entry)
        final_canon.add(canon)

    # Only commit if something actually changed.
    if restored_names or evicted_names or len(final) != len(current):
        resume[section_key] = final
        if restored_names:
            logger.info(
                "resume_normalizer: restored %d plan-ranked %s(s) the LLM dropped: %s",
                len(restored_names), item_label, restored_names,
            )
        if evicted_names:
            logger.info(
                "resume_normalizer: evicted %d LLM-kept %s(s) not in plan to make cap room: %s",
                len(evicted_names), item_label, evicted_names,
            )

    return resume


def restore_plan_projects(resume: dict, plan, profile_data: dict | None = None) -> dict:
    """PR 3a — restore plan-ranked projects the LLM dropped.

    See ``_restore_plan_items`` for the shared mechanics + the plan-as-
    contract rationale. Plan-ranked projects always come first; LLM
    extras not in plan fill remaining cap slots.
    """
    def _plan_name(p):
        return getattr(p, 'name', None) or (
            p.get('name', '') if isinstance(p, dict) else ''
        )

    def _src_to_entry(src):
        return {
            'name': src.get('name', ''),
            # description / highlights may both be present, either str
            # or list — renderer's ``_ensure_list`` handles both shapes.
            'description': src.get('description', ''),
            'highlights': src.get('highlights', []),
            'technologies': (
                src.get('technologies')
                or src.get('tech_stack')
                or src.get('tech')
                or []
            ),
            'url': src.get('url', ''),
        }

    return _restore_plan_items(
        resume, plan, profile_data,
        section_key='projects',
        plan_attr='projects',
        cap=_PROJECT_CAP,
        plan_item_to_name=_plan_name,
        source_to_resume_entry=_src_to_entry,
        item_label='project',
    )


def restore_plan_certs(resume: dict, plan, profile_data: dict | None = None) -> dict:
    """PR 3a — restore plan-ranked certs the LLM dropped.

    Certs don't have bullets, so the voice-consistency note in
    ``restore_plan_projects`` doesn't apply — restoration is verbatim
    source-CV name / issuer / date / duration / url.
    """
    def _plan_name(c):
        # plan.certifications is a list[str] per InclusionPlan dataclass.
        return c if isinstance(c, str) else (
            getattr(c, 'name', None) or (c.get('name', '') if isinstance(c, dict) else '')
        )

    def _src_to_entry(src):
        return {
            'name': src.get('name', ''),
            'issuer': src.get('issuer', ''),
            'date': src.get('date', ''),
            'duration': src.get('duration', ''),
            'url': src.get('url', ''),
        }

    return _restore_plan_items(
        resume, plan, profile_data,
        section_key='certifications',
        plan_attr='certifications',
        cap=_CERT_CAP,
        plan_item_to_name=_plan_name,
        source_to_resume_entry=_src_to_entry,
        item_label='cert',
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def normalize_resume(
    resume_content: Any,
    plan: Optional[Any] = None,
    job=None,
    profile_data: dict | None = None,
) -> dict:
    """Run every normalization rule in order. Always returns a new dict;
    the input is never mutated.

    Order matters:
      1. Title-case fixes — cheap, no dependencies.
      2. Soft-skill filter — must run before the hard cap so the cap
         doesn't count blocked entries.
      3. Hard cap — final size guard.
      4. Strip first-person — applied to descriptions and summary.
      5. Soft-skill bullet filter — drops "Developed soft skills…"
         fillers BEFORE consolidation so they don't survive as one
         consolidated noise line.
      6. Consolidate coursework — operates on the cleaned bullet lists.
      7. Plan-driven trims — last so the smaller / cleaner output is
         what ships.
      8. backfill_summary — synthesize when LLM left it empty; takes
         ``job`` so it can lead with the JD-aligned experience instead
         of always picking the most-recent role.
    """
    if not isinstance(resume_content, dict):
        return resume_content
    resume = copy.deepcopy(resume_content)

    resume = normalize_titles(resume)
    resume = filter_soft_skills(resume)
    resume = enforce_skill_hard_cap(resume)
    resume = strip_first_person_from_resume(resume)
    resume = filter_soft_skill_bullets(resume)
    resume = consolidate_coursework(resume)
    resume = normalize_bullet_punctuation(resume)
    # Round 1.5.2: snap any paraphrased experience titles back to the
    # CV's verbatim form so LinkedIn verification doesn't catch a
    # mismatch (e.g. "DevOps Engineering Trainee" vs "DevOps Engineer
    # Trainee"). Runs before plan-trims so the canonical title is what
    # downstream plan-based matching sees.
    resume = enforce_verbatim_titles(resume, profile_data)
    if plan is not None:
        resume = trim_skills_to_plan(resume, plan)
        resume = trim_projects_to_plan(resume, plan)
        resume = trim_certs_to_plan(resume, plan)
        # PR 3a — plan-as-contract restoration. trim runs first (removes
        # LLM additions not in plan), then restore re-injects plan-ranked
        # projects/certs the LLM dropped. Both bounded by the same caps
        # (_PROJECT_CAP / _CERT_CAP).
        resume = restore_plan_projects(resume, plan, profile_data)
        resume = restore_plan_certs(resume, plan, profile_data)
    resume = backfill_summary(resume, job=job)
    # Round 1.5.2: strip recruiter-jargon openers ("Highly motivated…")
    # and unsupported YoE claims that survive the LLM-side prompt rule.
    # 2026-05-29 round-4: also strips pipe-title summary headers
    # ("DS | AI Eng | DA with body" → "<JD title> with body").
    resume = clean_summary_phrasing(resume, job=job)
    resume = filter_languages(resume, profile_data=profile_data)
    resume = normalize_experience_dates(resume)
    resume = mark_expected_graduation(resume)
    return resume


def filter_languages(resume: dict, profile_data: dict | None = None) -> dict:
    """Issue 2: drop non-spoken-language entries from the `languages`
    field. The resume-gen LLM sometimes dumps the entire skills list
    (programming languages + tech + soft skills) into `languages`.
    docx_exporter already filtered this at export time, but the HTML
    preview and PDF render paths showed the raw dump — so the filter
    belongs here in the central normalizer, ahead of every render path.

    Reuses ``profile_sanitizer.sanitize_languages_field`` (the same
    spoken-language heuristic the DOCX path uses) so the two never drift.

    2026-05-29 round-4: when ``profile_data`` is available, enrich bare
    name entries ("English", "Arabic") with the profile's proficiency
    annotation ("English (Fluent)", "Arabic (Native)") and order them
    by the profile's sequence — the LLM keeps stripping proficiency
    markers between regenerations, which hides a candidate's native
    language asset for region-specific roles.
    """
    if not isinstance(resume, dict):
        return resume
    langs = resume.get('languages')
    if not isinstance(langs, list) or not langs:
        return resume
    if profile_data:
        enriched_form = _build_language_proficiency_map(profile_data)
        if enriched_form:
            ordered = _reorder_and_enrich_languages(langs, enriched_form)
            if ordered != langs:
                logger.info(
                    "resume_normalizer: enriched languages from profile "
                    "proficiency map (%s → %s)", langs, ordered,
                )
                langs = ordered
    from profiles.services.profile_sanitizer import sanitize_languages_field
    resume['languages'] = sanitize_languages_field(langs)
    return resume


def _build_language_proficiency_map(profile_data: dict) -> dict:
    """Build a {bare-name-lower: fuller-form} map from profile.data_content.
    Preserves the profile's ordering via ``_profile_language_order`` (an
    underscore-prefixed extra key on the returned dict for the reorder pass)."""
    out: dict = {}
    order: list[str] = []
    profile_langs = profile_data.get('languages') or []
    if not isinstance(profile_langs, list):
        return out
    for entry in profile_langs:
        if isinstance(entry, dict):
            name = (entry.get('name') or '').strip()
            prof = (entry.get('proficiency') or '').strip()
            if not name:
                continue
            key = name.lower()
            if prof and prof.lower() != key:
                out[key] = f"{name} ({prof})"
            else:
                out[key] = name
            if key not in order:
                order.append(key)
        elif isinstance(entry, str) and entry.strip():
            bare = re.split(r'[\(\[\-–—]', entry, maxsplit=1)[0].strip()
            if not bare:
                continue
            key = bare.lower()
            out[key] = entry.strip()
            if key not in order:
                order.append(key)
    out['_profile_language_order'] = order  # type: ignore[assignment]
    return out


def _reorder_and_enrich_languages(langs: list, prof_map: dict) -> list:
    """Replace bare-name resume entries with the profile's fuller form
    and reorder by the profile's sequence. Languages not in the profile
    are kept in their original relative order, appended at the end."""
    order = prof_map.get('_profile_language_order') or []
    # Map current resume entries to their canonical key.
    indexed: list[tuple[str, str]] = []  # (key, enriched_text)
    for item in langs:
        text = (
            item if isinstance(item, str)
            else (item.get('name', '') if isinstance(item, dict) else '')
        )
        if not isinstance(text, str) or not text.strip():
            continue
        bare = re.split(r'[\(\[\-–—]', text, maxsplit=1)[0].strip().lower()
        enriched = prof_map.get(bare, text.strip())
        indexed.append((bare, enriched))
    # Emit in profile-order first, then any extras the profile didn't have.
    by_key: dict = {}
    for key, enriched in indexed:
        # If the resume mentions a language twice, keep first occurrence.
        if key not in by_key:
            by_key[key] = enriched
    out: list = []
    for key in order:
        if key in by_key:
            out.append(by_key.pop(key))
    out.extend(by_key.values())
    return out


# Month name -> 3-letter abbreviation, for date-format consistency.
_MONTH_LONG_TO_SHORT = {
    'january': 'Jan', 'february': 'Feb', 'march': 'Mar', 'april': 'Apr',
    'may': 'May', 'june': 'Jun', 'july': 'Jul', 'august': 'Aug',
    'september': 'Sep', 'october': 'Oct', 'november': 'Nov', 'december': 'Dec',
}
# Recognize either form so we can pull a month index for "expected" detection.
_MONTH_INDEX = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
}
_DATE_TOKEN_RE = re.compile(
    r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\b',
    re.IGNORECASE,
)
_EXPECTED_RE = re.compile(r'\bexpected\b', re.IGNORECASE)
_YEAR_RE = re.compile(r'\b(20\d{2}|19\d{2})\b')


def _shorten_long_month(text: str) -> str:
    """Replace any full-name month token with its 3-letter form. Other
    tokens (year, dashes, "Present") are left alone, so "August 2025 -
    Sep 2025" → "Aug 2025 - Sep 2025"."""
    if not isinstance(text, str):
        return text

    def _replace(m):
        return _MONTH_LONG_TO_SHORT.get(m.group(1).lower(), m.group(1))

    return _DATE_TOKEN_RE.sub(_replace, text)


def normalize_experience_dates(resume: dict, _today=None) -> dict:
    """Make experience date formatting consistent: pick the 3-letter
    abbreviated month form ("Aug 2025") everywhere, so mixed outputs
    like "August 2025 - Sep 2025" (observed 2026-05-29 in the
    Almansour duration) become uniform "Aug 2025 - Sep 2025".

    Touches ``duration``, ``start_date``, ``end_date``. Idempotent on
    already-abbreviated forms.
    """
    if not isinstance(resume, dict):
        return resume
    exps = resume.get('experience')
    if not isinstance(exps, list):
        return resume
    for entry in exps:
        if not isinstance(entry, dict):
            continue
        for field in ('duration', 'start_date', 'end_date'):
            val = entry.get(field)
            if isinstance(val, str) and val.strip():
                entry[field] = _shorten_long_month(val)
    return resume


def mark_expected_graduation(resume: dict, _today=None) -> dict:
    """Prefix education ``year`` with "Expected " when it lies in the
    future relative to today. A June 2026 graduation written today
    (May 2026) is a future date — recruiters expect the "Expected"
    qualifier to disambiguate "this hasn't happened yet" from "I
    already graduated".

    The check parses month + year out of the ``year`` field (which the
    CV parser uses for graduation date despite the name). If only a
    year is present, we compare year-vs-year. If both month and year
    parse, we use them together.

    Idempotent — if the field already starts with "Expected" (case-
    insensitive), it's left alone.
    """
    if not isinstance(resume, dict):
        return resume
    edus = resume.get('education')
    if not isinstance(edus, list):
        return resume
    today = _today or _dt.date.today()
    for entry in edus:
        if not isinstance(entry, dict):
            continue
        year_str = entry.get('year')
        if not isinstance(year_str, str) or not year_str.strip():
            continue
        if _EXPECTED_RE.search(year_str):
            continue
        y_match = _YEAR_RE.search(year_str)
        if not y_match:
            continue
        year = int(y_match.group(1))
        m_match = _DATE_TOKEN_RE.search(year_str)
        month_idx = _MONTH_INDEX.get(m_match.group(1).lower()) if m_match else None
        is_future = (
            year > today.year
            or (year == today.year and month_idx is not None and month_idx > today.month)
        )
        if is_future:
            entry['year'] = f"Expected {year_str.strip()}"
    return resume
