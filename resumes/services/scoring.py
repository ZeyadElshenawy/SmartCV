"""ATS scoring + evidence confidence.

Two scoring functions used across the app:

- compute_ats_breakdown(content, job_skills) → rich dict with score,
  matched_count, total_count, stuffed_skills, and in_context bonus.
  Penalizes keyword stuffing (same word repeated >4 times across the
  resume) and rewards keywords that appear in experience descriptions
  rather than only in the skills list.

- compute_evidence_confidence(profile) → 0–3 confidence rating based on
  how many external signal sources (GitHub / Scholar / Kaggle) are
  connected and non-trivial. Used to decorate the gap-analysis verdict
  with a "this score is well-evidenced" indicator.

The legacy resumes.services.resume_generator.calculate_ats_score wrapper
delegates here so existing callers (tasks.py, views.py) keep working.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TypedDict


def _count_skill_occurrences(text: str, skill: str) -> int:
    """Word-boundary-aware substring count with plural/suffix tolerance.

    Plain ``text.count(skill)`` is unsafe for short / single-character
    skills: counting the literal letter "r" inside a JSON dump of a
    resume produces ~600 false positives ("role", "ferry", "JavaScript",
    every URL with 'r' in it, etc.) and the stuffing detector then
    erroneously knocks 5 points off the score for "R".

    We anchor with lookbehind/lookahead that exclude an adjacent word
    character — this works for:
      - single-letter languages: ``R``, ``C``, ``D`` (won't match inside
        ``role``, ``data``)
      - punctuation-bearing names: ``C++``, ``C#``, ``Node.js``, ``.NET``
        (re.escape preserves the punctuation; the `\\w` lookarounds only
        care about the very first / last character)
      - multi-word skills: ``Power BI``, ``Machine Learning`` (still
        single regex; spaces inside are matched literally).

    Plural / suffix tolerance — additive, never subtractive:
      On top of the exact word-boundary match, we ALSO count two trivial
      variants so a JD listing "REST API" (singular) doesn't miss a
      resume bullet that says "REST APIs" (plural), and vice-versa:

        forward direction:  skill + ``s|es`` at the boundary
          → "Microservice" matches "Microservices"; "API" matches "APIs".
        reverse direction:  skill with a trailing alpha-``s`` stripped
          → "Microservices" matches "Microservice"; "REST APIs" matches
            "REST API".

      Both branches are guarded so the existing safety stays intact:
        * last char must be alphabetic — skips ``C++``, ``Node.js``,
          ``.NET``, ``C#`` (their trailing punctuation already
          differentiates and we don't want ``C++s`` / ``.NETs`` matches).
        * last alpha-token must be ≥3 chars — skips ``R``, ``C``, ``D``
          (no ``Rs`` / ``Cs`` convention for these) and ``Go`` (where
          the English word ``Goes`` would otherwise false-positive
          through ``Go`` + ``es``).
        * reverse-strip additionally requires last alpha-token ≥4 chars
          so de-pluralising ``AWS`` → ``AW`` (false-positive vector on
          bare 2-letter token) doesn't trigger.

      No synonym / concept mapping is added here — we strictly stay on
      lexical variants of the same root term. "state management" still
      won't match "BLoC" alone; that would be a different, riskier
      change with semantic-classifier characteristics.
    """
    if not text or not skill:
        return 0
    # Always: exact word-boundary match (preserves byte-for-byte the
    # prior behaviour for every skill — the variant branches below are
    # purely additive).
    base_pattern = rf"(?<!\w){re.escape(skill)}(?!\w)"
    try:
        count = len(re.findall(base_pattern, text, flags=re.IGNORECASE))
    except re.error:
        # Defensive — re.escape should make this unreachable.
        return text.lower().count(skill.lower())

    last_char = skill[-1]
    last_token = re.search(r"\w+$", skill)
    last_token_len = len(last_token.group(0)) if last_token else 0
    if not (last_char.isalpha() and last_token_len >= 3):
        return count

    # Forward: skill="REST API" → also hit "REST APIs" / "REST APIes".
    plural_pattern = rf"(?<!\w){re.escape(skill)}(?:es|s)(?!\w)"
    try:
        count += len(re.findall(plural_pattern, text, flags=re.IGNORECASE))
    except re.error:
        pass

    # Reverse: skill="REST APIs" → also hit "REST API".
    # Only when the skill itself ends in an alpha 's' (a likely plural
    # inflection) AND removing it leaves a ≥4-char alpha token (guards
    # AWS / iOS — short acronyms where stripping the 's' produces a
    # bare 2-letter token that risks false matches).
    if skill[-1].lower() == "s" and last_token_len >= 4:
        singular = skill[:-1]
        singular_pattern = rf"(?<!\w){re.escape(singular)}(?!\w)"
        try:
            count += len(re.findall(singular_pattern, text, flags=re.IGNORECASE))
        except re.error:
            pass

    return count

logger = logging.getLogger(__name__)

# A keyword appearing more than this many times across the whole resume is
# treated as stuffing and penalized rather than rewarded.
STUFFING_THRESHOLD = 4
STUFFING_PENALTY_PER_SKILL = 5.0  # points knocked off the raw score per stuffed keyword
IN_CONTEXT_BONUS_PER_SKILL = 2.0  # bonus when a keyword shows up in experience text, capped


class AtsBreakdown(TypedDict):
    score: float                # final 0–100 score after bonuses & penalties
    raw_score: float            # matched_count / total_count * 100, before adjustments
    base: float                 # tier-weighted base BEFORE bonus/penalty, pre-clamp
                                # (== raw_score when tiers is None). Surfaced so a
                                # breakdown UI can show base + bonus − penalty = score
                                # whenever the [0,100] clamp didn't bind. Purely
                                # additive metadata — never affects `score`.
    matched_count: int
    total_count: int
    in_context_count: int       # how many keywords appeared in experience descriptions
    in_context_bonus: float     # actual bonus applied (capped)
    stuffed_skills: list[str]   # keywords that appeared > STUFFING_THRESHOLD times
    stuffing_penalty: float     # actual penalty applied
    keyword_counts: dict[str, int]  # per-skill count, useful for debug/UI
    # --- Fix (b): additive structured breakdown ----------------------------
    # The nine flat keys above are preserved byte-for-byte (ats_eval.py and
    # the test suite read them by name and assume their scalar types). The
    # keys below expose the scorer's per-skill working so a breakdown UI is a
    # pure rendering job, and the number can never disagree with its
    # justification — both come from one computation.
    #
    # Naming note: the structured in-context / stuffing views are named
    # ``in_context`` / ``stuffing`` (not ``in_context_bonus`` / ``stuffing_
    # penalty``) precisely because those latter names are the reserved flat
    # float keys above — same-name collision would break the float-typed
    # back-compat consumers.
    must_have: dict             # {matched: [...], missed: [...], coverage: float}
    nice_to_have: dict          # {matched: [...], missed: [...], coverage: float}
    in_context: dict            # {points: float, skills: [...]} — structured bonus view
    stuffing: dict              # {points: float, skills: [...]} — structured penalty view


class EvidenceConfidence(TypedDict):
    score: int                  # 0–4 stars
    label: str                  # "Strong" / "Moderate" / "Limited" / "Untested"
    sources: list[str]          # ["github", "scholar", "kaggle", "linkedin"] — only those that contributed
    detail: str                 # one-sentence explanation for the UI


def compute_ats_breakdown(
    resume_content: dict,
    job_skills: list[str],
    tiers: dict | None = None,
) -> AtsBreakdown:
    """Score a tailored resume against the job's required skills.

    ``job_skills`` is the flat union (must ∪ nice) and remains the
    match-scan list. ``tiers`` — shape ``{"must_have": [...],
    "nice_to_have": [...]}`` — is optional and only partitions that scan
    for weighting and the structured breakdown. When ``tiers is None``
    (the legacy call signature) the whole flat list is treated as one
    must-have tier, so the weighted formula degrades to the exact
    pre-(b) ``matched / total × 100`` behaviour and existing callers /
    tests are unaffected.

    Algorithm:
    1. Count occurrences of each job_skill across the full resume JSON,
       bucketing each into its must / nice tier.
    2. Fix (b) — tier-weighted base: must-have coverage weighted 0.75,
       nice-to-have 0.25, renormalised onto whichever tiers are actually
       present (an empty tier must not waste its slice — a perfect
       must-have score with no nice-to-haves reaches 100, not 75).
    3. Apply stuffing penalty: for each skill that appears > 4 times in
       prose, knock 5 points off.
    4. Apply in-context bonus: for each skill that appears in any
       experience/project description, award 2 points, capped at +10.
    5. Clamp to [0, 100], round to one decimal.
    """
    if not job_skills:
        return AtsBreakdown(
            score=0.0, raw_score=0.0, base=0.0, matched_count=0, total_count=0,
            in_context_count=0, in_context_bonus=0.0,
            stuffed_skills=[], stuffing_penalty=0.0, keyword_counts={},
            must_have={"matched": [], "missed": [], "coverage": 0.0},
            nice_to_have={"matched": [], "missed": [], "coverage": 0.0},
            in_context={"points": 0.0, "skills": []},
            stuffing={"points": 0.0, "skills": []},
        )

    # Fix (b): build case-insensitive tier membership sets. ``must_set is
    # None`` is the sentinel for "no tiers supplied" — every skill then
    # buckets to must-have and the formula falls back to the flat score.
    if tiers is None:
        must_set: set[str] | None = None
        nice_set: set[str] = set()
    else:
        must_set = {
            s.lower().strip() for s in (tiers.get("must_have") or [])
            if isinstance(s, str) and s.strip()
        }
        nice_set = {
            s.lower().strip() for s in (tiers.get("nice_to_have") or [])
            if isinstance(s, str) and s.strip()
        }

    full_text = json.dumps(resume_content).lower()

    # Evidence text — the bullets that demonstrate a skill in context.
    # Fix (d): include ``projects[].description`` alongside
    # ``experience[].description``. For juniors / interns whose primary
    # evidence lives in projects, the prior experience-only scope gave a
    # structural zero on the in-context bonus even when every keyword
    # showed up in their project bullets.
    evidence_parts: list[str] = []
    for section_key in ("experience", "projects"):
        for item in (resume_content.get(section_key) or []):
            if not isinstance(item, dict):
                continue
            desc = item.get('description')
            if isinstance(desc, list):
                evidence_parts.extend(str(b) for b in desc)
            elif isinstance(desc, str):
                evidence_parts.append(desc)
    evidence_text = " ".join(evidence_parts).lower()

    # Prose text — what counts as "stuffing-able" content. Fix (c):
    # narrow the stuffing scan to free-form prose (summary + bullets) so
    # legitimate structured tagging — one skills-line entry per skill,
    # one ``technologies`` array entry per project — doesn't push a
    # candidate's strongest skill past the threshold. Genuine stuffing
    # is "Python Python Python" inside a bullet; correct tagging is
    # "Python" in the skills line and "Python" in three projects'
    # tech arrays.
    prose_parts: list[str] = list(evidence_parts)
    summary = resume_content.get('professional_summary')
    if isinstance(summary, str):
        prose_parts.append(summary)
    prose_text = " ".join(prose_parts).lower()

    keyword_counts: dict[str, int] = {}
    matched_count = 0
    in_context_count = 0
    stuffed_skills: list[str] = []

    # Fix (b): per-tier matched/missed split + in-context skill names.
    # All three were derivable from the existing loop but discarded; here
    # we just collect them. ``in_context_skills`` is gathered at the same
    # ``skill_lower in evidence_text`` branch that already increments the
    # counter — nearly free.
    must_matched: list[str] = []
    must_missed: list[str] = []
    nice_matched: list[str] = []
    nice_missed: list[str] = []
    in_context_skills: list[str] = []

    for skill in job_skills:
        skill_lower = skill.lower().strip()
        if not skill_lower:
            continue
        count = _count_skill_occurrences(full_text, skill_lower)
        keyword_counts[skill] = count
        is_matched = count > 0
        if is_matched:
            matched_count += 1
            if skill_lower in evidence_text:
                in_context_count += 1
                in_context_skills.append(skill)
        # Tier bucketing. A skill present in must-have (or in neither
        # list, or when no tiers were supplied) counts as must-have;
        # nice-have only when it is exclusively in the nice tier. This
        # keeps total_must + total_nice == number of scanned skills.
        is_nice = (
            must_set is not None
            and skill_lower in nice_set
            and skill_lower not in must_set
        )
        if is_nice:
            (nice_matched if is_matched else nice_missed).append(skill)
        else:
            (must_matched if is_matched else must_missed).append(skill)
        # Stuffing is detected against prose only, NOT the full JSON
        # (see fix (c) comment above). Threshold and per-skill penalty
        # are unchanged; only the scan window narrows.
        prose_count = _count_skill_occurrences(prose_text, skill_lower)
        if prose_count > STUFFING_THRESHOLD:
            stuffed_skills.append(skill)
            logger.warning(
                "Keyword stuffing detected: '%s' appears %d times in prose",
                skill, prose_count,
            )

    # Flat key — preserved exactly: matched / total × 100 over the whole list.
    raw_score = (matched_count / len(job_skills)) * 100.0

    total_must = len(must_matched) + len(must_missed)
    total_nice = len(nice_matched) + len(nice_missed)
    must_cov = (len(must_matched) / total_must) if total_must else 0.0
    nice_cov = (len(nice_matched) / total_nice) if total_nice else 0.0

    # Fix (b): tier-weighted base with renormalisation onto present tiers.
    # No tiers supplied → fall back to the exact flat score (regression
    # guard for legacy callers). Both tiers present → 0.75 / 0.25 split.
    # Only one tier present → that tier takes the full weight (the absent
    # tier's slice is not wasted — a perfect must-have with no nice-to-haves
    # reaches 100, not 75).
    if tiers is None:
        base = raw_score
    elif total_must and total_nice:
        base = (must_cov * 0.75 + nice_cov * 0.25) * 100.0
    elif total_must:
        base = must_cov * 100.0
    elif total_nice:
        base = nice_cov * 100.0
    else:
        base = 0.0

    in_context_bonus = min(in_context_count * IN_CONTEXT_BONUS_PER_SKILL, 10.0)
    stuffing_penalty = len(stuffed_skills) * STUFFING_PENALTY_PER_SKILL

    final = base + in_context_bonus - stuffing_penalty
    final = max(0.0, min(100.0, final))

    return AtsBreakdown(
        score=round(final, 1),
        raw_score=round(raw_score, 1),
        base=round(base, 1),
        matched_count=matched_count,
        total_count=len(job_skills),
        in_context_count=in_context_count,
        in_context_bonus=round(in_context_bonus, 1),
        stuffed_skills=stuffed_skills,
        stuffing_penalty=round(stuffing_penalty, 1),
        keyword_counts=keyword_counts,
        # Additive structured breakdown — derived from the same pass.
        must_have={
            "matched": must_matched,
            "missed": must_missed,
            "coverage": round(must_cov, 3),
        },
        nice_to_have={
            "matched": nice_matched,
            "missed": nice_missed,
            "coverage": round(nice_cov, 3),
        },
        in_context={
            "points": round(in_context_bonus, 1),
            "skills": in_context_skills,
        },
        stuffing={
            "points": round(stuffing_penalty, 1),
            "skills": list(stuffed_skills),
        },
    )


def compute_evidence_confidence(profile) -> EvidenceConfidence:
    """Rate how well the candidate's profile is corroborated by external signals.

    Counts signals that meaningfully exist (non-error, non-trivial):
    - GitHub:   at least 1 public repo
    - Scholar:  at least 1 publication OR any citations
    - Kaggle:   at least 1 entry in any category
    - LinkedIn: a parsed profile URL/username (and no error) — even a
                link-only connection counts because recruiters can verify
                identity from the link alone.

    Returns 0–4 confidence with a label + one-sentence detail.
    """
    data = getattr(profile, 'data_content', None) or {}
    if not isinstance(data, dict):
        data = {}

    sources: list[str] = []

    # GitHub
    gh = data.get('github_signals') or {}
    if isinstance(gh, dict) and not gh.get('error') and (gh.get('public_repos') or 0) > 0:
        sources.append('github')

    # Scholar — needs either a publication or any citations to count as real evidence
    sc = data.get('scholar_signals') or {}
    if isinstance(sc, dict) and not sc.get('error'):
        has_pubs = bool(sc.get('top_publications'))
        has_cites = (sc.get('total_citations') or 0) > 0
        if has_pubs or has_cites:
            sources.append('scholar')

    # Kaggle — any non-zero category counts as engagement
    kg = data.get('kaggle_signals') or {}
    if isinstance(kg, dict) and not kg.get('error'):
        any_activity = any(
            isinstance(kg.get(k), dict) and (kg[k].get('count') or 0) > 0
            for k in ('competitions', 'datasets', 'notebooks', 'discussion')
        )
        if any_activity:
            sources.append('kaggle')

    # LinkedIn — link-only snapshots still produce a profile_url + username.
    # A fully-scraped snapshot adds name/headline/about/experience. We accept
    # either as a signal: the recruiter can verify identity from the link, and
    # an unblocked, error-free LinkedIn entry on the profile is itself meaningful.
    li = data.get('linkedin_signals') or {}
    if (
        isinstance(li, dict)
        and not li.get('error')
        and (li.get('profile_url') or li.get('username'))
    ):
        sources.append('linkedin')

    score = len(sources)
    # Label bucketing: 1 signal is thin; 2 is corroborated; 3+ is comprehensive.
    # Keeps backward compat with the prior 3-signal "Strong" assertion in the
    # ComputeEvidenceConfidenceTests fixture.
    label = {0: 'Untested', 1: 'Limited', 2: 'Moderate', 3: 'Strong', 4: 'Strong'}.get(score, 'Untested')

    # Pretty display names — `.capitalize()` mangles GitHub/LinkedIn.
    _DISPLAY = {'github': 'GitHub', 'scholar': 'Scholar', 'kaggle': 'Kaggle', 'linkedin': 'LinkedIn'}

    if score == 0:
        detail = "No external signals connected — connect GitHub, Scholar, Kaggle, or LinkedIn to corroborate skills."
    else:
        names = ", ".join(_DISPLAY.get(s, s.capitalize()) for s in sources)
        detail = f"Backed by {names}."

    return EvidenceConfidence(
        score=score, label=label, sources=sources, detail=detail,
    )


def calculate_ats_score(
    resume_content: dict,
    job_skills: list[str],
    tiers: dict | None = None,
) -> float:
    """Backwards-compat wrapper: returns just the final score float.

    Kept as the canonical API for resumes.tasks and resumes.views (which
    store it on resume.ats_score). ``tiers`` is optional and forwarded to
    compute_ats_breakdown() for tier-weighted scoring; when omitted the
    flat (pre-(b)) score is returned. New code should prefer
    compute_ats_breakdown() for transparency.
    """
    return compute_ats_breakdown(resume_content, job_skills, tiers)['score']
