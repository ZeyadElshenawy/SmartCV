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
import logging
import re
from difflib import SequenceMatcher
from typing import Any, Optional

from profiles.services.profile_sanitizer import (
    _SOFT_SKILL_BLOCKLIST_CANON,
    _canonical,
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
    any pool entry. Pool is the plan's project / cert names."""
    c = _canonical(name)
    if not c:
        return False
    if c in pool_canon:
        return True
    low = name.lower()
    for candidate in pool_pretty:
        score = SequenceMatcher(None, low, candidate.lower()).ratio()
        if score >= _FUZZY_MATCH_CUTOFF:
            return True
    return False


def _is_coursework_bullet(text: str) -> bool:
    """Heuristic: this bullet looks like a leftover course-name from a
    consolidated coursework list — short, no terminal punctuation,
    doesn't start with an action verb, no quantified outcomes.

    Round 1.5.2 — tightened to fix the DevOps regression where capstone
    metrics like "3 Grafana dashboards (19 panels)" got mis-classified
    as coursework and folded into a fake "Coursework included: ..."
    line. Course titles are almost never quantified; achievement
    bullets almost always are.

    Rejection rules (any one disqualifies the bullet):
      - Empty / whitespace-only.
      - Multi-sentence (mid-string period) or ends with !/?/:.
      - Longer than _COURSEWORK_MAX_WORDS words.
      - Contains any DIGIT — almost all real coursework titles are
        clean noun phrases without numbers; bullets like
        "3 Grafana dashboards (19 panels)" or "15-min RTO" are
        capstone deliverables, not course names.
      - Contains a colon mid-bullet — "X: Y" structure is the
        capstone-section header pattern, not a course name.
      - For 4+ word bullets: starts with a known action verb
        (_VERB_START_RE) OR a past-tense verb (-ed-suffix word not
        in the noun/adjective whitelist).
    """
    if not text:
        return False
    s = text.strip()
    if not s:
        return False
    # Multi-sentence (has a period followed by more text) — not a course name.
    if '.' in s.rstrip('.') or s.endswith(('!', '?', ':')):
        return False
    # Digit anywhere = capstone metric, not coursework.
    if any(c.isdigit() for c in s):
        return False
    # Mid-bullet colon = "Section: detail" pattern from capstone notes.
    if ':' in s:
        return False
    words = s.split()
    if not words:
        return False
    if len(words) > _COURSEWORK_MAX_WORDS:
        return False
    # Action-verb rejection only kicks in for 4+-word bullets so we
    # don't lose short course titles that happen to start with an -ed
    # word ("Supervised Learning", "Advanced Statistics").
    if len(words) >= 4:
        if _VERB_START_RE.match(s):
            return False
        if _starts_with_past_tense_verb(s):
            return False
    return True


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
        if _canonical(name) in _SOFT_SKILL_BLOCKLIST_CANON:
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


def _consolidate_bullet_list(bullets: list) -> list:
    """Collapse runs of 2+ consecutive coursework-like bullets into one
    ``Coursework: A, B, C.`` entry. Operates on the bullet list in
    order, preserving non-course bullets verbatim."""
    if not bullets:
        return bullets
    out: list[Any] = []
    buffer: list[str] = []

    def _flush():
        if len(buffer) >= 2:
            out.append(f"Coursework included: {', '.join(buffer)}.")
        else:
            out.extend(buffer)
        buffer.clear()

    for b in bullets:
        if isinstance(b, str) and _is_coursework_bullet(b):
            buffer.append(b.strip())
        else:
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
    r"^\s*(?:Highly motivated|Results-driven|Detail-oriented|"
    r"Passionate|Dedicated|Hard-working|Self-motivated|"
    r"Energetic|Goal-oriented|Dynamic and|Innovative and)\s+",
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


def clean_summary_phrasing(resume: dict) -> dict:
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


def _is_near_duplicate_skill(existing_canon: set[str], new_canon: str) -> bool:
    """Detect near-duplicate skills the canonical-key dedup misses.

    Catches three patterns:
      1. Trailing tokens: "CI/CD" (canon='cicd') vs "CI/CD tools"
         (canon='cicdtools') — prefix match.
      2. Acronym-with-expansion: "CI/CD" (canon='cicd') vs
         "Continuous Integration and Continuous Delivery (CI/CD)"
         (canon='continuousintegrationandcontinuousdeliverycicd') —
         the short acronym appears as a suffix of the verbose form.
      3. Acronym-in-parentheses: ditto, where the parenthesized
         acronym is the canonical name.

    First-seen wins.
    """
    for ec in existing_canon:
        if not ec or not new_canon:
            continue
        if ec == new_canon:
            return True
        # Prefix dedup: "cicd" matches "cicdtools".
        if len(ec) >= 3 and new_canon.startswith(ec):
            return True
        if len(new_canon) >= 3 and ec.startswith(new_canon):
            return True
        # Acronym-suffix dedup: a short canonical (≤ 8 chars, the
        # typical max acronym length even with slashes stripped) that
        # appears as the SUFFIX of a longer canonical is the
        # verbose-vs-acronym pattern. Cap the short side so we don't
        # false-positive on common substrings (e.g. "ml" inside
        # "machinelearning").
        if 3 <= len(ec) <= 8 and len(new_canon) > len(ec) + 4 and new_canon.endswith(ec):
            return True
        if 3 <= len(new_canon) <= 8 and len(ec) > len(new_canon) + 4 and ec.endswith(new_canon):
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

    kept_canon: set[str] = set()
    kept: list[str] = []
    dropped_soft: list[str] = []
    dropped_dup: list[str] = []

    def _try_add(name: str) -> bool:
        nonlocal kept, kept_canon
        if not name:
            return False
        c = _canonical(name)
        if not c:
            return False
        if c in _SOFT_SKILL_BLOCKLIST_CANON:
            dropped_soft.append(name)
            return False
        if _is_near_duplicate_skill(kept_canon, c):
            dropped_dup.append(name)
            return False
        kept.append(name.strip())
        kept_canon.add(c)
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
    plan membership any more.

    Round 1.5 (DevOps audit): the previous "drop certs not in plan"
    filter was over-aggressive — it removed certs that the recruiter
    would clearly want to see (e.g. "Introduction to Software
    Testing" on a DevOps resume). The auditor's recommendation was
    explicit: "Include ALL certifications from JSON". Just keep the
    candidate's full cert list, capped at 8.

    ``plan`` is still accepted for signature compatibility with
    ``normalize_resume``'s call site but is unused here.
    """
    certs = resume.get('certifications') or []
    if not isinstance(certs, list):
        return resume
    kept = [c for c in certs if isinstance(c, dict) and (c.get('name') or '').strip()]
    if len(kept) > _CERT_CAP:
        kept = kept[:_CERT_CAP]
    resume['certifications'] = kept
    return resume


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
    resume = backfill_summary(resume, job=job)
    # Round 1.5.2: strip recruiter-jargon openers ("Highly motivated…")
    # and unsupported YoE claims that survive the LLM-side prompt rule.
    resume = clean_summary_phrasing(resume)
    return resume
