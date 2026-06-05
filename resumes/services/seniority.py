"""Honest-seniority helpers for the rendered job title.

Background — the seniority overclaim:
  The JD's raw title (e.g. ``"Mid Flutter Developer"``) reached the
  summary's ``role_hint`` and the v1 LLM's ``professional_title`` field
  via ``job.title`` → the LLM lifted the level prefix verbatim, stamping
  the JD's target seniority onto the candidate as a self-label. The
  classifier's profile-side seniority (``detect_role_seniority``) is
  computed but discarded in the merge (``classify_for_jd:393-398`` —
  JD wins). The candidate's actual stage never reached the renderer.

This module supplies the title-assembly path with a candidate-grounded
prefix, so what the recruiter reads matches the candidate's tenure
rather than the JD's target. The classifier's ``seniority`` field is
untouched — it still drives ``build_plan``'s cap calibration via
``kb_integration.seniority_calibration`` (a legitimate JD-target
optimisation that has nothing to do with identity claims).

API surface:

  * ``compute_candidate_stage(experiences) -> (stage, confident)`` —
    pure tenure math from the ``experiences[]`` JSON shape on
    ``profile.data_content``. Excludes internships / training /
    apprentice-style engagements from the professional count; treats
    ``Contract`` as professional-countable. Conservative on the
    overclaim side: when uncertain whether an entry is professional,
    excludes it (under-credit, never over-credit).

  * ``display_seniority_prefix(experiences) -> str`` — the user-facing
    title prefix when confident; ``""`` when not (the strip fallback).

  * ``strip_seniority_prefix(title) -> str`` — strip a leading
    seniority word from a JD title so the bare role name remains
    (``"Mid Flutter Developer"`` → ``"Flutter Developer"``).

  * ``honest_job_title(raw_job_title, experiences) -> str`` — the
    fused result the dispatcher passes downstream as ``job_title``.

All four are general (no profile-specific values; data-driven word
sets). The defaults are conservative — when we can't compute, we strip
the prefix entirely rather than guess.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Reuse the existing month-precision date parser used elsewhere in the
# codebase so date-format coverage stays consistent (mixed formats:
# "2024", "Mar 2026", "March 2026", "2024-05", etc.).
from profiles.services.experience_math import _parse_date


# ---------------------------------------------------------------------------
# Tenure classification — data-driven exclusion rules for internships /
# training / volunteer engagements. Match the trace's general rule:
#
#   employment_type tag (when present) → "Internship/Trainee/Apprentice/Volunteer"
#     marks the entry as training and excludes it from the professional count.
#   Title regex \b(intern|trainee|apprentice|fellow)\b catches what the
#     tag misses.
#   Title fallback \b(internship|training|bootcamp)\b fires ONLY when
#     employment_type is absent — handles hand-typed entries whose tag
#     never got populated.
#
# Contract is intentionally NOT in the exclusion set — short contracts
# count toward professional tenure per the trace's decision (they reflect
# real engagements, even when brief). They contribute to professional
# months but, given typical contract length, rarely move the threshold.
# ---------------------------------------------------------------------------

_TRAINING_EMPLOYMENT_TYPES = {
    "internship", "trainee", "apprentice", "volunteer",
}
_TRAINING_TITLE_TOKENS = re.compile(
    r"\b(intern|trainee|apprentice|fellow)\b", re.IGNORECASE,
)
_TRAINING_TITLE_FALLBACK = re.compile(
    r"\b(internship|training|bootcamp)\b", re.IGNORECASE,
)


def _classify_entry(exp: dict) -> str:
    """Return ``'training'``, ``'professional'``, or ``'unknown'``.

    Conservative: any entry whose category cannot be confidently
    determined is ``'unknown'`` — and the tenure sum excludes
    ``'unknown'`` entries entirely (under-credit).
    """
    if not isinstance(exp, dict):
        return "unknown"
    etype_raw = (exp.get("employment_type") or "")
    etype = etype_raw.strip().lower() if isinstance(etype_raw, str) else ""
    title = (exp.get("title") or "")
    title = title.strip() if isinstance(title, str) else ""
    # Rule 1 — explicit employment_type tag identifying training.
    if etype and etype in _TRAINING_EMPLOYMENT_TYPES:
        return "training"
    # Rule 2 — title pattern naming the role as training.
    if title and _TRAINING_TITLE_TOKENS.search(title):
        return "training"
    # Rule 3 — title fallback fires only when employment_type is absent.
    if not etype and title and _TRAINING_TITLE_FALLBACK.search(title):
        return "training"
    # Otherwise: if employment_type is present (Full-time, Part-time,
    # Contract, Permanent, etc.), treat as professional. If it's
    # absent and the title doesn't give a training signal, we cannot
    # tell → unknown (the conservative case the trace called for).
    if etype:
        return "professional"
    return "unknown"


def _entry_months(exp: dict) -> int:
    """Months of credit for one professional entry. Returns 0 when the
    dates can't be parsed or the role is single-month.

    Inclusive month math: Mar 2026 → Apr 2026 = 2 months. (Counted as
    "two months of Contract engagement", matching how the trace
    described the Turing entry.)
    """
    if not isinstance(exp, dict):
        return 0
    start = _parse_date(exp.get("start_date"))
    if start is None:
        return 0
    end = _parse_date(exp.get("end_date"))
    if end is None:
        # Empty end_date — treat as single-month (under-credit).
        # We deliberately do NOT auto-extend to today even if the
        # role might be ongoing; the trace's conservative rule wins.
        end = start
    if end < start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return max(0, months)


# ---------------------------------------------------------------------------
# Stage thresholds. Months of professional tenure → display stage.
# Per the trace's product decision: entry-level and junior collapse to
# a single "Junior" label (a positive identity-statement rather than
# "Entry-level" which reads as a disclaimer).
# ---------------------------------------------------------------------------

_STAGE_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (24, "Junior"),     # < 24 months
    (60, "Mid"),        # [24, 60)
    (96, "Senior"),     # [60, 96)
    (10**9, "Staff"),   # >= 96
)


def compute_candidate_stage(
    experiences: Iterable[dict] | None,
) -> tuple[str, bool]:
    """Compute the candidate's displayable seniority stage from raw
    ``experiences[]`` JSON.

    Returns ``(stage, confident)``:

      * ``stage`` is one of ``"Junior" | "Mid" | "Senior" | "Staff"``
        based on the professional-months bucket (training/internship
        engagements excluded).
      * ``confident`` is ``True`` only when at least one entry has both
        a parseable ``start_date`` AND a definite category (the entry
        is either training or professional — not ``'unknown'``).
        ``False`` when we can't tell — e.g. all dates unparseable, or
        all ``employment_type`` ``None`` with no training-title signal.

    Conservative: when uncertain whether an entry is professional, the
    entry is excluded from the tenure count (under-credit, never
    over-credit). Callers should treat ``confident=False`` as "drop the
    prefix entirely" — the safer label is no level claim.
    """
    if not experiences:
        return "Junior", False
    total_months = 0
    classifiable_with_date = False
    for exp in experiences:
        category = _classify_entry(exp)
        date_parsed = (
            isinstance(exp, dict)
            and _parse_date(exp.get("start_date")) is not None
        )
        if category != "unknown" and date_parsed:
            classifiable_with_date = True
        if category == "professional":
            total_months += _entry_months(exp)
    # Map professional_months to a stage label using the threshold table.
    stage = "Junior"
    for upper, label in _STAGE_THRESHOLDS:
        if total_months < upper:
            stage = label
            break
    return stage, classifiable_with_date


def display_seniority_prefix(
    experiences: Iterable[dict] | None,
) -> str:
    """Return the title-prefix the renderer should use, or ``""`` when
    we can't confidently classify the candidate's stage.

    ``""`` is the strip-fallback signal — the caller should render just
    the bare role name with no level claim ("Flutter Developer"),
    which is the safe default per the trace's fail-safe spine.
    """
    stage, confident = compute_candidate_stage(experiences)
    if not confident:
        return ""
    return stage


# ---------------------------------------------------------------------------
# JD-title prefix stripping. Data-driven — the word set is the
# industry-standard seniority vocabulary; no profile-specific tuning.
# Case-insensitive; tolerates one or more whitespace chars between
# prefix and bare role; handles hyphenated variants ("Entry-Level",
# "Mid-Level") explicitly.
# ---------------------------------------------------------------------------

_SENIORITY_PREFIX_WORDS: tuple[str, ...] = (
    # Two-token / hyphenated variants first so they win the longest-match
    # ordering at regex-alternation time.
    "Entry-Level", "Entry Level",
    "Mid-Level", "Mid Level",
    # Single-token variants.
    "Senior", "Sr.", "Sr",
    "Junior", "Jr.", "Jr",
    "Principal",
    "Staff",
    "Lead",
    "Mid",
    "Intern", "Trainee",
)

# Build a leading-prefix matcher. Sort by descending length so longer
# variants ("Entry-Level") match before shorter ones ("Entry") would
# (re-alternation in Python regex is leftmost, not longest-by-default).
_PREFIX_ALT = "|".join(
    re.escape(w)
    for w in sorted(_SENIORITY_PREFIX_WORDS, key=len, reverse=True)
)
_PREFIX_RE = re.compile(
    r"^\s*(?:" + _PREFIX_ALT + r")\s+",
    re.IGNORECASE,
)


def strip_seniority_prefix(title: str) -> str:
    """Strip a leading seniority word from ``title`` and return the bare
    role. Titles without a leading seniority word are returned unchanged
    (modulo leading-whitespace trim).

    Examples:
      ``"Mid Flutter Developer"`` → ``"Flutter Developer"``
      ``"Senior Software Engineer"`` → ``"Software Engineer"``
      ``"Sr. Backend Engineer"`` → ``"Backend Engineer"``
      ``"Entry-Level Designer"`` → ``"Designer"``
      ``"Flutter Developer"`` → ``"Flutter Developer"``
      ``"Engineering Manager"`` → ``"Engineering Manager"``
    """
    if not isinstance(title, str):
        return ""
    return _PREFIX_RE.sub("", title, count=1).strip()


def honest_job_title(
    raw_job_title: str,
    experiences: Iterable[dict] | None,
) -> str:
    """The dispatcher-facing entry point.

    Strips the JD's seniority prefix from ``raw_job_title`` to get the
    bare role, then either prepends the candidate's confident stage
    (option (c) — "Junior Flutter Developer") or returns the bare role
    alone (option (a) — "Flutter Developer") when we can't confidently
    classify.

    Never uses the JD's original seniority word. Always conservative
    on the overclaim side.
    """
    bare = strip_seniority_prefix(raw_job_title or "")
    if not bare:
        # Defensive — JD with no role nucleus (just the seniority prefix,
        # or empty). Return whatever the strip produced; an empty title
        # is the right downstream signal that the role_hint can't be
        # personalised.
        return bare
    prefix = display_seniority_prefix(experiences)
    if not prefix:
        return bare
    return f"{prefix} {bare}"
