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
_COURSEWORK_MAX_WORDS = 7
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
    consolidated coursework list — short, no terminal punctuation, doesn't
    start with an action verb. Used to detect runs of these so we can
    fold them into one ``Coursework: ...`` line."""
    if not text:
        return False
    s = text.strip()
    if not s:
        return False
    # Multi-sentence (has a period followed by more text) — not a course name.
    if '.' in s.rstrip('.') or s.endswith(('!', '?', ':')):
        return False
    # Too long — not a course name.
    if len(s.split()) > _COURSEWORK_MAX_WORDS:
        return False
    # Starts with an action verb — it's a real achievement bullet.
    if _VERB_START_RE.match(s):
        return False
    return True


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
    """Drop any certification whose name doesn't appear in
    ``plan.certifications``. Caps the result at ``_CERT_CAP``. Skips
    when the plan has no certs (defensive — empty plan ≠ wipe the
    section)."""
    if plan is None or not getattr(plan, 'certifications', None):
        return resume
    pool_pretty = [c for c in plan.certifications if c]
    pool_canon = {_canonical(c) for c in pool_pretty}
    if not pool_canon:
        return resume
    certs = resume.get('certifications') or []
    kept: list[dict] = []
    dropped_names: list[str] = []
    for cert in certs:
        if not isinstance(cert, dict):
            continue
        name = (cert.get('name') or '').strip()
        if _fuzzy_in(name, pool_canon, pool_pretty):
            kept.append(cert)
        else:
            dropped_names.append(name)
    if len(kept) > _CERT_CAP:
        kept = kept[:_CERT_CAP]
    resume['certifications'] = kept
    if dropped_names:
        logger.info(
            "resume_normalizer: dropped %d certification(s) not in plan: %s",
            len(dropped_names), dropped_names,
        )
    return resume


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def normalize_resume(resume_content: Any, plan: Optional[Any] = None) -> dict:
    """Run every normalization rule in order. Always returns a new dict;
    the input is never mutated.

    Order matters:
      1. Title-case fixes — cheap, no dependencies.
      2. Soft-skill filter — must run before the hard cap so the cap
         doesn't count blocked entries.
      3. Hard cap — final size guard.
      4. Strip first-person — applied to descriptions and summary.
      5. Consolidate coursework — operates on (possibly first-person-
         stripped) bullet lists.
      6. Plan-driven trims — last, so the smaller / cleaner output is
         what ships.
    """
    if not isinstance(resume_content, dict):
        return resume_content
    resume = copy.deepcopy(resume_content)

    resume = normalize_titles(resume)
    resume = filter_soft_skills(resume)
    resume = enforce_skill_hard_cap(resume)
    resume = strip_first_person_from_resume(resume)
    resume = consolidate_coursework(resume)
    if plan is not None:
        resume = trim_projects_to_plan(resume, plan)
        resume = trim_certs_to_plan(resume, plan)
    return resume
