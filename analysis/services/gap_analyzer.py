import logging
import json
import difflib
import re
from profiles.services.llm_engine import get_structured_llm
from profiles.services.profile_sanitizer import _canonical
from profiles.services.schemas import (
    GapAnalysisResult,
    MatchedSkill,
    MissingSkill,
    TieredGapAnalysisResult,
)
from analysis.services.skill_score import (
    avg_proximity as _avg_proximity,
    compute_match_score,
    match_band,
)
# Shared variant-aware skill matcher (canonical alias table + trailing-noun
# strip + difflib typo fallback). Same matcher the planner's JD-relevance uses,
# so a skill named by a variant ("RESTful APIs" <- JD "REST API integration")
# is credited identically at both sites.
from jobs.services.skill_extractor import skills_match

logger = logging.getLogger(__name__)

def _enrich_skill_payload(skills):
    enriched = []
    for s in skills:
        if isinstance(s, dict):
            name = s.get('name', 'Unknown Skill')
            years = s.get('years', '')
            prof = s.get('proficiency', '')
            parts = [name]
            if years:
                parts.append(f"{years} years")
            if prof:
                parts.append(f"({prof})")
            enriched.append(" - ".join(parts).strip(" -"))
        else:
            enriched.append(str(s))
    return enriched


def _build_full_candidate_context(profile):
    """
    Build a comprehensive candidate context string that includes
    skills, experience highlights, project details, AND certifications.
    This ensures the LLM sees the FULL picture for accurate matching.
    """
    sections = []

    # --- Skills ---
    if profile.skills:
        skill_names = []
        for s in profile.skills:
            if isinstance(s, dict):
                skill_names.append(s.get('name', ''))
            else:
                skill_names.append(str(s))
        sections.append("CANDIDATE SKILLS: " + ", ".join(skill_names))

    # --- Experience ---
    if profile.experiences:
        lines = ["WORK EXPERIENCE:"]
        for exp in (profile.experiences or [])[:5]:
            if not exp:
                continue
            title = exp.get('title', '')
            company = exp.get('company', '')
            # PR 3b: description canonical (List[str]). The schema's
            # coerce_to_canonical folded highlights/achievements into
            # description, and migrate_profile_schema brought legacy
            # rows into the same shape.
            bullets = exp.get('description') or []
            if isinstance(bullets, str):
                bullets = [bullets] if bullets.strip() else []

            line = f"- {title} at {company}"
            if bullets:
                hl_text = "; ".join(str(b) for b in bullets[:4])
                line += f": {hl_text[:300]}"
            lines.append(line)
        sections.append("\n".join(lines))

    # --- Projects ---
    if profile.projects:
        lines = ["PROJECTS:"]
        for proj in (profile.projects or [])[:5]:
            if not proj:
                continue
            name = proj.get('name', '')
            # PR 3b: description canonical (List[str]).
            bullets = proj.get('description') or []
            if isinstance(bullets, str):
                bullets = [bullets] if bullets.strip() else []
            techs = proj.get('technologies', [])

            line = f"- {name}"
            if bullets:
                hl_text = "; ".join(str(b) for b in bullets[:3])
                line += f": {hl_text[:200]}"
            if techs:
                line += f" [Technologies: {', '.join(techs)}]"
            lines.append(line)
        sections.append("\n".join(lines))

    # --- Certifications ---
    if profile.certifications:
        lines = ["CERTIFICATIONS & TRAINING:"]
        for cert in (profile.certifications or [])[:10]:
            if not cert:
                continue
            if isinstance(cert, dict):
                name = cert.get('name', '')
                issuer = cert.get('issuer', '')
                line = f"- {name}"
                if issuer:
                    line += f" ({issuer})"
                lines.append(line)
            else:
                lines.append(f"- {cert}")
        sections.append("\n".join(lines))

    # --- Education ---
    if profile.education:
        lines = ["EDUCATION:"]
        for edu in (profile.education or [])[:3]:
            if not edu:
                continue
            degree = edu.get('degree', '')
            field = edu.get('field', '')
            institution = edu.get('institution', '')
            lines.append(f"- {degree} in {field} from {institution}")
        sections.append("\n".join(lines))

    # --- External signal blocks (corroborates skills with public evidence) ---
    for builder in (_format_github_activity, _format_scholar_activity, _format_kaggle_activity):
        block = builder(profile)
        if block:
            sections.append(block)

    return "\n\n".join(sections)


def _signals(profile, key: str):
    """Return profile.data_content[key] if it's a non-error dict, else None."""
    data = getattr(profile, 'data_content', None) or {}
    if not isinstance(data, dict):
        return None
    snap = data.get(key)
    if not snap or not isinstance(snap, dict) or snap.get('error'):
        return None
    return snap


def _format_scholar_activity(profile) -> str:
    """Build the GOOGLE SCHOLAR block (citations, h-index, top publications)."""
    snap = _signals(profile, 'scholar_signals')
    if not snap:
        return ''
    lines = ["GOOGLE SCHOLAR (academic publications + citation impact):"]
    name = snap.get('name') or snap.get('user_id') or 'unknown'
    affil = snap.get('affiliation') or ''
    line = f"- {name}"
    if affil:
        line += f" ({affil})"
    lines.append(line)
    lines.append(
        f"- Citations: {snap.get('total_citations') or 0} total · "
        f"h-index: {snap.get('h_index') or 0} · i10: {snap.get('i10_index') or 0}"
    )
    pubs = snap.get('top_publications') or []
    for pub in pubs[:5]:
        if not isinstance(pub, dict):
            continue
        title = (pub.get('title') or '').strip()
        if not title:
            continue
        venue = pub.get('venue') or ''
        year = pub.get('year') or ''
        cites = pub.get('citations') or 0
        bits = [title]
        if venue:
            bits.append(venue)
        if year:
            bits.append(year)
        suffix = f" — {cites} citations" if cites else ''
        lines.append(f"- {' · '.join(bits)}{suffix}")
    return "\n".join(lines)


def _format_kaggle_activity(profile) -> str:
    """Build the KAGGLE block (tier, competitions/datasets/notebooks counts + medals)."""
    snap = _signals(profile, 'kaggle_signals')
    if not snap:
        return ''
    lines = ["KAGGLE (data-science platform — competitions, notebooks, datasets):"]
    handle = snap.get('display_name') or snap.get('username') or 'unknown'
    tier = snap.get('overall_tier') or 'Novice'
    lines.append(f"- @{snap.get('username', handle)} ({handle}) — overall tier: {tier}")

    def fmt_cat(label: str, cat) -> str | None:
        if not isinstance(cat, dict):
            return None
        count = cat.get('count') or 0
        if not count:
            return None
        m = cat.get('medals') or {}
        gold = m.get('gold', 0); silver = m.get('silver', 0); bronze = m.get('bronze', 0)
        medal_str = ''
        if gold or silver or bronze:
            medal_str = f" · medals 🥇{gold} 🥈{silver} 🥉{bronze}"
        cat_tier = cat.get('tier')
        tier_str = f" · {cat_tier}" if cat_tier else ''
        return f"- {label}: {count}{tier_str}{medal_str}"

    for label, key in [('Competitions', 'competitions'), ('Datasets', 'datasets'),
                        ('Notebooks', 'notebooks'), ('Discussion', 'discussion')]:
        line = fmt_cat(label, snap.get(key))
        if line:
            lines.append(line)
    return "\n".join(lines)


def _format_github_activity(profile) -> str:
    """Build the GITHUB ACTIVITY block from cached snapshot, if any.

    The snapshot lives in profile.data_content['github_signals'], populated
    by profiles.services.github_aggregator. Returns an empty string when no
    snapshot is cached, the snapshot has an error, or the user has no
    profile data attribute (e.g., a SimpleNamespace stub in tests).
    """
    data_content = getattr(profile, 'data_content', None) or {}
    snap = data_content.get('github_signals') if isinstance(data_content, dict) else None
    if not snap or not isinstance(snap, dict) or snap.get('error'):
        return ''

    lines = ["GITHUB ACTIVITY (public, evidence-corroborates skills):"]
    username = snap.get('username') or 'unknown'
    public_repos = snap.get('public_repos') or 0
    total_stars = snap.get('total_stars') or 0
    recent = snap.get('recent_commit_count') or 0
    lines.append(
        f"- @{username} — {public_repos} public repos, {total_stars} total stars, "
        f"{recent} commits in last 90 days"
    )

    # Languages — strong signal for skill corroboration.
    langs = snap.get('language_breakdown') or []
    if langs:
        # Each entry is (language, repo_count) — accept dict-shaped too just in case.
        formatted = []
        for entry in langs[:8]:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                formatted.append(f"{entry[0]} ({entry[1]} repos)")
            elif isinstance(entry, dict) and 'name' in entry:
                formatted.append(f"{entry.get('name')} ({entry.get('count', '?')} repos)")
        if formatted:
            lines.append(f"- Primary languages by repo count: {', '.join(formatted)}")

    # Top repos — give the LLM concrete evidence per project.
    top_repos = snap.get('top_repos') or []
    for repo in top_repos[:5]:
        if not isinstance(repo, dict):
            continue
        name = repo.get('name') or repo.get('full_name', '?')
        lang = repo.get('language') or ''
        stars = repo.get('stars') or 0
        desc = (repo.get('description') or '').strip()
        line = f"- {name}"
        if lang:
            line += f" [{lang}]"
        if stars:
            line += f" — {stars}★"
        if desc:
            line += f": {desc[:160]}"
        lines.append(line)

    return "\n".join(lines)


_PROXIMITY_RUBRIC = """=== PROXIMITY ASSESSMENT (CRITICAL — applies to missing_must_have AND missing_nice_to_have) ===

For every skill you place in missing_must_have or missing_nice_to_have, you MUST
assess proximity by scanning ALL evidence sources — resume skills, experience
bullets, project tech stacks, certifications, GitHub language stats, Scholar
publications, Kaggle competitions. Adjacent != matched. Adjacent means there is
transfer-learning value the candidate could lean on.

Use this anchor scale (verbatim, do not reinterpret):

  0.0 — no related evidence anywhere in the CV, GitHub, Scholar, Kaggle, or projects
  0.2 — vaguely adjacent domain knowledge only (e.g., JD wants Kubernetes, CV has
        "deployed apps" with no container detail)
  0.4 — one clearly adjacent skill present (e.g., JD wants TensorFlow, CV has
        PyTorch)
  0.6 — multiple adjacent skills OR the same skill at coursework / hobby level
        (e.g., JD wants NoSQL, CV has SQL + a class project using MongoDB)
  0.8 — the exact skill is present but evidence is thin (mentioned once in a
        project, no professional context) OR the candidate has the underlying
        capability at a LOWER SENIORITY than the JD asks
  1.0 — FORBIDDEN. If proximity would be 1.0, the skill belongs in matched_must_have
        or matched_nice_to_have. Do NOT return proximity == 1.0 for a missing
        skill. Re-route the skill, or lower the value to 0.8.

WORKED EXAMPLE 1 (mid range):
  JD asks for: TensorFlow (must-have)
  Candidate has: PyTorch in 2 projects, scikit-learn, NumPy
  -> proximity: 0.4
     proximity_reason: "PyTorch experience transfers; TF API differs but concepts identical"
     bridge_hint: "1-2 weeks to port a PyTorch project to TF"

WORKED EXAMPLE 2 (low range):
  JD asks for: Kubernetes (must-have)
  Candidate has: Django web apps deployed to Heroku, no container or orchestration mentions
  -> proximity: 0.2
     proximity_reason: "Some deployment experience but no container/orchestration evidence"
     bridge_hint: "Start with Docker fundamentals before K8s"

HONESTY CONSTRAINT:
  Do not inflate proximity to make the candidate feel better. A 0.2 is more useful
  than a fake 0.6 because it tells the user what to focus on. bridge_hint can be
  omitted (left null) if you have nothing concrete to suggest — do not invent one.

  This honesty applies to the matched_* lists too: never list a skill as
  matched on adjacency or a shared word — only on direct evidence of THAT
  exact skill. If you are crediting it because it is "close", it is missing_*.
"""


def _job_tiers(job):
    """Pull must-have / nice-to-have skill lists from a Job model.

    Prefers the v2 `extracted_skills_tiers` JSONField; falls back to
    `extracted_skills` (flat list) treated as must-have-only when the tier
    dict is missing (legacy rows or jobs created before the v2 extractor).
    """
    tiers = getattr(job, "extracted_skills_tiers", None) or {}
    must = list(tiers.get("must_have") or [])
    nice = list(tiers.get("nice_to_have") or [])
    if not must and not nice:
        # Legacy fallback: treat all extracted skills as must-have.
        must = list(job.extracted_skills or [])
    return must, nice


def _empty_result(method: str, must: list, nice: list) -> dict:
    """Return shape for the early-exit / failure paths.

    Keeps the new tier-aware fields and the legacy flat union populated so
    downstream readers (template, drag-drop endpoint, benchmarks) all see
    consistent data.
    """
    flat_missing = list(must) + list(nice)
    return {
        # Tier-aware fields (new)
        'matched_must_have': [],
        'matched_nice_to_have': [],
        'missing_must_have': [
            {'name': s, 'source_quote': '', 'proximity': 0.0,
             'proximity_reason': 'No related evidence found in profile',
             'bridge_hint': None}
            for s in must
        ],
        'missing_nice_to_have': [
            {'name': s, 'source_quote': '', 'proximity': 0.0,
             'proximity_reason': 'No related evidence found in profile',
             'bridge_hint': None}
            for s in nice
        ],
        'soft_skill_gaps': [],
        'similarity_score': 0.0,
        'match_band': match_band(0.0),
        'avg_proximity': None,
        'analysis_method': method,
        # Legacy flat fields (back-compat)
        'matched_skills': [],
        'missing_skills': flat_missing,
        'partial_skills': [],
        'critical_missing_skills': list(must)[:5] if must else flat_missing[:5],
        'seniority_mismatch': None,
    }


def compute_gap_analysis(profile, job):
    """
    Tier-aware proximity-enriched gap analysis. One Groq call.

    Reads job.extracted_skills_tiers (must_have / nice_to_have lists) and asks
    the LLM to return four tier-split skill lists. Each missing skill carries
    a proximity score and a one-line proximity_reason explaining it.
    """
    must_skills, nice_skills = _job_tiers(job)
    candidate_context = _build_full_candidate_context(profile)

    if not must_skills and not nice_skills:
        logger.info("Gap analysis skipped: job has no extracted skills")
        return _empty_result('no_job_skills', [], [])

    if not candidate_context.strip():
        logger.info("Gap analysis skipped: profile is effectively empty")
        return _empty_result('empty_profile', must_skills, nice_skills)

    must_json = json.dumps(must_skills)
    nice_json = json.dumps(nice_skills)
    domain_hint = (getattr(job, 'domain', '') or '').strip() or 'Unknown'

    base_prompt = f"""You are an expert technical recruiter. Compare the candidate's FULL profile against the job requirements.

JOB TITLE: {job.title}
JOB COMPANY: {job.company or 'Unknown'}
JOB DOMAIN: {domain_hint}
JD MUST-HAVE SKILLS:   {must_json}
JD NICE-TO-HAVE SKILLS: {nice_json}

{candidate_context}

=== YOUR TASK ===
Produce FOUR tier-split skill lists plus optional soft-skill gap notes:

  1. matched_must_have    — JD must-have skills the candidate clearly HAS
  2. matched_nice_to_have — JD nice-to-have skills the candidate clearly HAS
  3. missing_must_have    — JD must-have skills the candidate does NOT have
  4. missing_nice_to_have — JD nice-to-have skills the candidate does NOT have
  5. soft_skill_gaps      — free-text observations (seniority gap, career
                            transition risk). NOT individual skills.

Each matched_* entry has {{name, evidence_source, evidence_quote}}.
Each missing_* entry has {{name, source_quote, proximity, proximity_reason,
bridge_hint}}.

=== CRITICAL MATCHING RULES ===

RULE 1 — HOLISTIC EVIDENCE:
A skill is MATCHED if the candidate demonstrates it ANYWHERE in their profile:
- Explicitly listed in CANDIDATE SKILLS
- Demonstrated in WORK EXPERIENCE highlights or descriptions
- Used in PROJECT highlights or technologies
- Covered by a CERTIFICATION or training course
- Corroborated by GITHUB ACTIVITY (multiple repos / dominant language)
- Corroborated by GOOGLE SCHOLAR (publications on the topic)
- Corroborated by KAGGLE (competition tier + medal counts)
- Is a foundational prerequisite of skills they already have

For each matched skill: pick ONE evidence_source from {{'skills', 'experience',
'projects', 'certifications', 'github', 'scholar', 'kaggle', 'education',
'multiple'}}. Provide an evidence_quote (≤140 characters total — KEEP IT
SHORT, the schema TRUNCATES anything longer).

HARD RULE — every matched_* entry MUST have a non-empty evidence_quote
pointing at something in the profile. If you cannot point at a specific
phrase, the skill is NOT matched: put it in missing_* with a proximity
score that reflects how close the candidate is. An empty evidence_quote
on a matched_* entry will be automatically demoted to missing_* with
proximity 0.0 — don't waste the slot.

HARD RULE — WHOLE-SKILL GROUNDING (no adjacency credit):
Do NOT credit a multi-word skill unless its DISTINCTIVE component is
literally in the profile. "Firebase Messaging" needs "Messaging" evidence
— bare "Firebase" is NOT enough; "GoRouter" / "Dio" need that exact
package by name. A skill that is merely ADJACENT to one the candidate has
(same ecosystem, shares one word, transfer-learning value) belongs in
missing_* with a proximity score — NEVER in matched_*. A deterministic
grounding check runs after you and auto-demotes any matched skill whose
name is absent from the profile, so an adjacency guess only loses the slot.

RULE 2 — DIRECTIONAL SPECIFICITY:
- BROAD JD (e.g. "SQL", "Cloud") + SPECIFIC CV (e.g. "MySQL", "AWS") = MATCH
- SPECIFIC JD (e.g. "Tableau") + BROAD CV ("Data Viz") = NOT a match

RULE 3 — NO DUPLICATES, EXACT SPELLING:
- Each JD skill appears in EXACTLY ONE list. NEVER place a skill in BOTH
  matched_must_have AND missing_must_have. If you list it as matched, do
  NOT also list it as missing. Pick one.
- Use the exact spelling from the JD list (case-insensitive equality).

RULE 3b — STRING LENGTH LIMITS (HARD — schema will reject longer):
- evidence_quote:    ≤140 characters
- source_quote:      ≤140 characters
- proximity_reason:  ≤120 characters
- bridge_hint:       ≤140 characters
- Use the EMPTY STRING "" if no value applies, NOT null/None. The only
  field that may be null is bridge_hint (omit it when you have no
  concrete suggestion).

RULE 4 — TIER FIDELITY:
- A must-have stays in matched_must_have OR missing_must_have. Never crossover
  into the nice-to-have lists. Same for nice-to-have.

RULE 5 — SOFT SKILL GAPS (separate field, not chips):
- If the title implies seniority (Senior / Staff / Lead / Principal) but the
  candidate has <3 years relevant experience, add "Seniority gap: …".
- If the candidate is switching domains (e.g. teaching → SWE), add
  "Career transition: …".
- ≤20 words each. Constructive, not blockers.

{_PROXIMITY_RUBRIC}

=== OUTPUT ===
Return ONLY the structured JSON object containing the 5 keys via the provided function. 

HARD SCHEMA RULE: You MUST output a single root JSON object `{{ "matched_must_have": [...], ... }}`. Do NOT output a JSON array `[` at the root. No preamble.
"""

    def _invoke(prompt_text: str, attempt_label: str = 'primary'):
        # Bigger token budget than v1 (1500 → 2400) — TieredGapAnalysisResult
        # has 4 lists of nested objects with proximity + bridge_hint strings,
        # easily 1800-2200 tokens on a 15-skill JD.
        structured_llm = get_structured_llm(
            TieredGapAnalysisResult,
            temperature=0.1,
            max_tokens=2400,
            task=f"gap_analyzer_v2_{attempt_label}",
        )
        return structured_llm.invoke(prompt_text)

    try:
        result = _invoke(base_prompt)
    except Exception as exc:
        # Two common failure modes — both recoverable with a corrective retry:
        #   (a) proximity == 1.0 leak (Pydantic-side validator)
        #   (b) Groq tool-call schema rejection (string too long, null where
        #       string expected) — Groq enforces some constraints BEFORE
        #       Pydantic sees the data, so we surface them as one big retry
        #       hint.
        # Anything else (network error, Groq downtime) drops to the no-LLM
        # fallback.
        raw_msg = str(exc)
        msg = raw_msg.lower()
        is_proximity = 'proximity' in msg or 'less than 1' in msg
        is_schema = any(s in msg for s in (
            'tool call validation failed', 'tool_use_failed',
            'expected string, but got null', 'length must be',
        ))
        if is_proximity or is_schema:
            corrections = []
            if is_proximity:
                corrections.append(
                    "- At least one missing skill had proximity == 1.0. Skills "
                    "at 1.0 belong in matched_*, NOT missing_*. Either re-route "
                    "that skill into the matched tier with an evidence_quote, "
                    "or lower its proximity to 0.8 (meaning: exact skill "
                    "present in CV but thin evidence)."
                )
            if is_schema:
                corrections.append(
                    "- Schema validation failed. Common causes and fixes:\n"
                    "    * You output a bare JSON array `[` instead of a root JSON object `{`. You MUST output a dictionary with the 5 tier lists as keys.\n"
                    "    * A string field came back as null. Every required "
                    "string field (evidence_source, evidence_quote, "
                    "source_quote, proximity_reason) MUST be a string — use "
                    "the empty string \"\" instead of null when you have "
                    "no value. ONLY bridge_hint may be null.\n"
                    "    * A string field exceeded its length limit. "
                    "evidence_quote / source_quote / bridge_hint are capped "
                    "at 140 chars; proximity_reason at 120. SHORTEN them — "
                    "do not over-explain."
                )
            retry_prompt = base_prompt + (
                "\n\n=== PRIOR ATTEMPT FAILED ===\n"
                + "\n".join(corrections) +
                "\nReturn a valid response that fixes the above and obeys "
                "ALL other rules from this prompt.\n"
            )
            try:
                result = _invoke(retry_prompt, attempt_label='retry')
                logger.info("Gap analyzer recovered on retry (was: %s)", raw_msg[:200])
            except Exception as exc2:
                logger.error("Gap analyzer retry also failed: %s", exc2)
                return _fallback_gap_analysis(profile, job, must_skills, nice_skills)
        else:
            logger.error("LLM gap analysis failed: %s. Falling back to fuzzy set match.", exc)
            return _fallback_gap_analysis(profile, job, must_skills, nice_skills)

    # ---- Phase 2a: Honesty enforcement (demote evidence-less "matches") ----
    # The LLM sometimes lists a JD skill as matched even when it can't quote
    # any supporting evidence — the Pharco/LSEG bug. Defensible only when the
    # skill genuinely is present and the LLM just couldn't find a tight quote,
    # but in practice it's hallucination. Treat empty evidence as
    # "actually missing" with proximity 0.0 — Phase 2b reconciliation then
    # carries it forward into the correct missing tier.
    result = _demote_evidenceless_matches(
        result, profile_data=(profile.data_content or {}),
    )

    # ---- Phase 2b: Tier-aware reconciliation ----
    result = _reconcile_tier(
        result, must_skills, nice_skills,
        profile_data=(profile.data_content or {}),
    )

    score = compute_match_score(
        result.matched_must_have, result.missing_must_have,
        result.matched_nice_to_have, result.missing_nice_to_have,
    )
    avg_p = _avg_proximity(result.missing_must_have, result.missing_nice_to_have)
    band = match_band(score)

    matched_flat = [m.name for m in result.matched_must_have + result.matched_nice_to_have]
    missing_flat = [m.name for m in result.missing_must_have + result.missing_nice_to_have]

    return {
        # Tier-aware (v2)
        'matched_must_have':    [m.model_dump() for m in result.matched_must_have],
        'matched_nice_to_have': [m.model_dump() for m in result.matched_nice_to_have],
        'missing_must_have':    [m.model_dump() for m in result.missing_must_have],
        'missing_nice_to_have': [m.model_dump() for m in result.missing_nice_to_have],
        'soft_skill_gaps':      list(result.soft_skill_gaps or []),
        'similarity_score':     score,
        'match_band':           band,
        'avg_proximity':        avg_p,
        # Legacy flat (back-compat with template + drag-drop endpoint readers)
        'matched_skills':       matched_flat,
        'missing_skills':       missing_flat,
        'partial_skills':       [],
        'critical_missing_skills': [m.name for m in result.missing_must_have],
        'seniority_mismatch':   None,
        'analysis_method':      'llm_v2',
    }


def _demote_evidenceless_matches(result, profile_data=None):
    """Demote matched skills that lack real profile evidence into missing_*
    with proximity 0.0.

    Two gates, both demote:
      1. Empty ``evidence_quote`` — the LLM couldn't quote anything.
      2. Profile-grounding (only when ``profile_data`` is supplied) — the
         skill's NAME is not grounded in the profile via either an exact
         structured hit (tech array / skills array / cert name — the PR 3e
         path, unchanged) OR a whole-phrase prose mention. This catches the
         LLM's adjacency over-claims (e.g. "Firebase Messaging" off bare
         "Firebase", "GoRouter"/"Dio" with no package evidence) which gate 1
         misses because the LLM supplies a plausible but unverifiable quote.

    An honest match must point at SOMETHING in the profile;
    if the LLM can't quote anything, the skill is more honestly described as
    "we don't know the candidate has this".

    The LLM sometimes also emits the same skill in BOTH matched_* and
    missing_* lists (Pharco regression: 'Hadoop' appeared in matched_must_have
    with empty evidence AND in missing_must_have with real proximity). In
    that case we just drop the matched entry — the missing entry already
    captures the right truth, no need to add a synthetic 0.0 duplicate.
    """
    def _ev_empty(m):
        return not (getattr(m, 'evidence_quote', '') or '').strip()

    def _norm(s): return (s or '').lower().strip()

    # Profile-grounding pass (the real backstop). When ``profile_data`` is
    # supplied we additionally demote any matched skill whose NAME is not
    # grounded in the profile — catching the LLM's holistic/adjacency
    # over-claims (e.g. "Firebase Messaging" credited off bare "Firebase").
    # When ``profile_data`` is None (callers/tests that don't pass it) only
    # the legacy empty-quote check runs, preserving prior behaviour.
    ground = profile_data is not None
    prose = _grounding_prose_corpus(profile_data or {}) if ground else ""

    def _demotion_reason(m):
        """Reason string if this matched skill should be demoted, else None.
        A non-empty evidence_quote is necessary but NOT sufficient — the
        skill must also be grounded in the profile when grounding is on."""
        if _ev_empty(m):
            return 'LLM marked matched without specific evidence'
        if ground and not _skill_is_grounded(m.name, profile_data, prose):
            return 'No profile evidence for this skill (grounding check)'
        return None

    existing_missing_must = {_norm(m.name) for m in result.missing_must_have}
    existing_missing_nice = {_norm(m.name) for m in result.missing_nice_to_have}

    real_must, demoted_must = [], []
    for m in result.matched_must_have:
        reason = _demotion_reason(m)
        if reason:
            if _norm(m.name) in existing_missing_must:
                logger.info("Dropped duplicate ungrounded match '%s' (already in missing_must_have)", m.name)
                continue
            demoted_must.append(MissingSkill(
                name=m.name, source_quote='', proximity=0.0,
                proximity_reason=reason,
                bridge_hint=None,
            ))
        else:
            real_must.append(m)

    real_nice, demoted_nice = [], []
    for m in result.matched_nice_to_have:
        reason = _demotion_reason(m)
        if reason:
            if _norm(m.name) in existing_missing_nice:
                logger.info("Dropped duplicate ungrounded match '%s' (already in missing_nice_to_have)", m.name)
                continue
            demoted_nice.append(MissingSkill(
                name=m.name, source_quote='', proximity=0.0,
                proximity_reason=reason,
                bridge_hint=None,
            ))
        else:
            real_nice.append(m)

    if demoted_must or demoted_nice:
        logger.info(
            "Demoted %d unevidenced/ungrounded matched skill(s) → missing (must=%s nice=%s)",
            len(demoted_must) + len(demoted_nice),
            [m.name for m in demoted_must],
            [m.name for m in demoted_nice],
        )

    return TieredGapAnalysisResult(
        matched_must_have=real_must,
        matched_nice_to_have=real_nice,
        missing_must_have=list(result.missing_must_have) + demoted_must,
        missing_nice_to_have=list(result.missing_nice_to_have) + demoted_nice,
        soft_skill_gaps=list(result.soft_skill_gaps or []),
    )


# ---------------------------------------------------------------------------
# PR 3e — Deterministic evidence collector
# ---------------------------------------------------------------------------
# The LLM-driven matcher scans bullet text but routinely misses skills that
# only appear in:
#   * project ``technologies`` arrays
#   * certification names (e.g. "Natural Language Processing in TensorFlow"
#     evidences both NLP and TensorFlow)
#   * the candidate's ``skills`` array (when corroborated elsewhere)
#
# Empirically (Zeyad audit, 2026-05-16): TensorFlow / PyTorch / scikit-learn /
# Natural Language Processing all landed in ``missing_must_have`` despite
# being in project tech stacks AND cert names. The fix is a deterministic
# safety net that runs after the LLM pass to RESCUE skills with verifiable
# profile evidence — symmetric to the existing ``_demote_evidenceless_matches``
# which REMOVES claims without evidence.

# Skill canonical form must have at least this many chars before we consider
# substring-in-cert matches. Prevents "AI" / "ML" / "QA" false-positives
# matching unrelated cert names. Word-boundary matches on full strings (project
# tech tags, skills array) ignore this floor — only the cert SUBSTRING check.
_CERT_SUBSTRING_MIN_CANON = 4


def _collect_profile_evidence(skill_name: str, profile_data: dict) -> list[dict]:
    """Scan profile fields the LLM systematically misses and return any
    deterministic evidence anchors for ``skill_name``.

    Sources checked, in priority order:
      1. project_tech — exact canonical match of skill in a project's
         ``technologies`` array.
      2. certification — skill canon (≥4 chars) is a substring of a
         certification's name canon. "Natural Language Processing in
         TensorFlow" evidences both NLP-via-cert and TensorFlow-via-cert.
      3. experience_tech — exact canonical match in experience's tech /
         technologies list when present.
      4. skills_array — exact canonical match in the candidate's skills,
         but ONLY surfaced when corroborating evidence exists in 1/2/3.
         This preserves the anti-claim-stuffing guard for skills listed
         without substantiation.

    Returns an empty list when no evidence is found.
    """
    if not skill_name or not isinstance(profile_data, dict):
        return []
    skill_canon = _canonical(skill_name)
    if len(skill_canon) < 2:
        return []

    evidence: list[dict] = []

    # 1. Project technologies arrays.
    for proj in (profile_data.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        techs = (
            proj.get('technologies')
            or proj.get('tech_stack')
            or proj.get('tech')
            or []
        )
        if isinstance(techs, str):
            # Comma-separated string is the CV-parser's other shape.
            techs = [t.strip() for t in re.split(r"[,;|]", techs) if t.strip()]
        for tech in techs:
            if skills_match(skill_name, tech):
                proj_name = proj.get('name') or proj.get('title') or ''
                evidence.append({
                    'source': 'projects',
                    'ref': proj_name,
                    'snippet': f"Listed in {proj_name} tech stack"[:140],
                })
                break  # one match per project is enough

    # 2. Certification names (substring, ≥4-char canon).
    if len(skill_canon) >= _CERT_SUBSTRING_MIN_CANON:
        for cert in (profile_data.get('certifications') or []):
            if not isinstance(cert, dict):
                continue
            cert_name = cert.get('name') or cert.get('title') or ''
            cert_canon = _canonical(cert_name)
            if skill_canon and skill_canon in cert_canon:
                evidence.append({
                    'source': 'certifications',
                    'ref': cert_name,
                    'snippet': f"Earned: {cert_name}"[:140],
                })

    # 3. Experience tech tags (when present).
    for exp in (profile_data.get('experiences') or []):
        if not isinstance(exp, dict):
            continue
        techs = (
            exp.get('technologies')
            or exp.get('tech_stack')
            or exp.get('tech')
            or []
        )
        if isinstance(techs, str):
            techs = [t.strip() for t in re.split(r"[,;|]", techs) if t.strip()]
        for tech in techs:
            if skills_match(skill_name, tech):
                title = exp.get('title') or ''
                company = exp.get('company') or ''
                ref = f"{title} @ {company}".strip(' @')
                evidence.append({
                    'source': 'experience',
                    'ref': ref,
                    'snippet': f"Used at {ref}"[:140],
                })
                break

    # 4. Skills array (only when corroborated by 1/2/3).
    if evidence:
        for entry in (profile_data.get('skills') or []):
            name = entry.get('name', '') if isinstance(entry, dict) else str(entry or '')
            if name and skills_match(skill_name, name):
                corroborator = evidence[0]['source']
                evidence.append({
                    'source': 'skills',
                    'ref': name,
                    'snippet': f"Listed in skills (corroborated by {corroborator})"[:140],
                })
                break

    return evidence


def _build_matched_from_evidence(skill: str, evidence: list[dict]) -> 'MatchedSkill':
    """Materialise an evidence list into a MatchedSkill. Picks the
    strongest single source per the existing schema convention; uses
    'multiple' when ≥2 distinct source types fire."""
    sources = {e['source'] for e in evidence}
    evidence_source = next(iter(sources)) if len(sources) == 1 else 'multiple'
    # Prefer the first snippet — it's the highest-priority source per
    # _collect_profile_evidence's ordering.
    evidence_quote = evidence[0]['snippet'][:140]
    return MatchedSkill(
        name=skill,
        evidence_source=evidence_source,
        evidence_quote=evidence_quote,
    )


def _grounding_prose_corpus(profile_data: dict) -> str:
    """Free-text corpus for whole-phrase skill grounding: professional
    summary + experience/project description bullets.

    This is DISTINCT from _collect_profile_evidence's structured corpus
    (project/experience ``technologies`` arrays, ``skills`` array, cert
    names). Together they let a matched skill ground EITHER via an exact
    structured hit (the PR 3e path) OR via a whole-phrase prose mention —
    so a skill legitimately described only in a bullet/summary (e.g.
    "clean architecture") is not wrongly demoted, while a phantom
    multi-word skill whose distinctive component is absent everywhere is.
    """
    if not isinstance(profile_data, dict):
        return ""
    parts: list[str] = []
    for key in ("professional_summary", "normalized_summary", "summary", "objective"):
        v = profile_data.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    for section in ("experiences", "projects"):
        for item in (profile_data.get(section) or []):
            if not isinstance(item, dict):
                continue
            for field in ("description", "bullets", "summary"):
                val = item.get(field)
                if isinstance(val, list):
                    parts.extend(str(b) for b in val)
                elif isinstance(val, str) and val.strip():
                    parts.append(val)
    return " \n ".join(parts).lower()


def _phrase_in_prose(skill_name: str, prose_lower: str) -> bool:
    """Whole-skill-phrase word-boundary match — NOT substring/token.

    "firebase messaging" must appear as a contiguous phrase; "firebase"
    sharing a token with "Firebase Messaging" does NOT count. This is the
    guard against the exact over-claim the LLM makes (crediting a skill
    because it shares ONE word with real evidence).
    """
    s = re.sub(r"\s+", " ", (skill_name or "").strip().lower())
    if not s or not prose_lower:
        return False
    try:
        return bool(re.search(rf"(?<!\w){re.escape(s)}(?!\w)", prose_lower))
    except re.error:
        return s in prose_lower


def _skill_in_declared_skills(skill_name: str, profile_data: dict) -> bool:
    """Exact canonical match against the candidate's declared ``skills[]``.

    A skill the user explicitly listed is legitimate grounding — the
    candidate claims it. This is the GROUNDING direction and is
    deliberately UNGATED, distinct from ``_collect_profile_evidence``'s
    skills-array path, which is corroboration-gated for the RESCUE
    direction (anti-claim-stuffing) and is left unchanged. The effect:
    genuine declared skills stay matched, while skills the LLM invented
    out of adjacency (absent from skills[] AND everywhere else) demote.
    """
    if not isinstance(profile_data, dict) or not skill_name:
        return False
    for entry in (profile_data.get("skills") or []):
        name = entry.get("name", "") if isinstance(entry, dict) else str(entry or "")
        if name and skills_match(skill_name, name):
            return True
    return False


def _skill_is_grounded(skill_name: str, profile_data: dict, prose_lower: str) -> bool:
    """True iff a matched skill has REAL profile evidence.

    Grounds if ANY of:
      (PR 3e / structured path) ``_collect_profile_evidence`` finds an
        exact hit — project/experience ``technologies`` array, corroborated
        ``skills`` array, or a ≥4-char cert-name substring; OR
      (declared-skill path) an exact canonical match in ``skills[]`` —
        the candidate explicitly listed it; OR
      (prose path) the whole skill phrase appears word-boundary in the
        summary / experience / project bullets.

    Reusing ``_collect_profile_evidence`` verbatim is what keeps PR-3e-
    credited tech-array skills passing — its exact-match logic is
    unchanged here.
    """
    if not skill_name:
        return False
    if _collect_profile_evidence(skill_name, profile_data):
        return True
    if _skill_in_declared_skills(skill_name, profile_data):
        return True
    return _phrase_in_prose(skill_name, prose_lower)


def _reconcile_tier(result, must_skills: list, nice_skills: list,
                    profile_data: dict | None = None):
    """Ensure every JD skill is accounted for in exactly one tier list.

    For each JD must-have not seen in matched_must_have or missing_must_have,
    append a MissingSkill with proximity=0.0 and the honest stub reason.
    Same for nice-to-have. Cross-tier dedupe: a skill in both matched_* and
    missing_* keeps the matched_* entry only.

    PR 3e — Before giving up on an unaccounted-for skill OR leaving a
    LLM-marked-missing skill alone, run the deterministic evidence
    collector against profile fields the LLM systematically misses
    (project tech arrays, cert names, skills array with corroboration).
    A rescue PROMOTES the skill into matched_* with an explicit evidence
    source so downstream consumers (planner, UI) see it correctly.
    """
    def _norm(s: str) -> str:
        return (s or '').lower().strip()

    profile_data = profile_data or {}

    matched_must = list(result.matched_must_have)
    matched_nice = list(result.matched_nice_to_have)
    missing_must = list(result.missing_must_have)
    missing_nice = list(result.missing_nice_to_have)

    matched_must_keys = {_norm(m.name) for m in matched_must}
    matched_nice_keys = {_norm(m.name) for m in matched_nice}

    # Cross-tier dedupe: drop missing entries the LLM also marked matched.
    missing_must = [m for m in missing_must if _norm(m.name) not in matched_must_keys]
    missing_nice = [m for m in missing_nice if _norm(m.name) not in matched_nice_keys]

    # PR 3e — promote LLM-marked-missing skills that have deterministic
    # profile evidence. This is the Zeyad case: LLM put TensorFlow in
    # missing_must_have, but it's in the Brain Tumor project tech stack
    # AND the NLP-in-TensorFlow cert. Promote rather than leave missing.
    def _rescue_missing(missing_list, matched_list, matched_keys, tier_label: str):
        rescued = []
        still_missing = []
        for m in missing_list:
            ev = _collect_profile_evidence(m.name, profile_data)
            if ev:
                matched_entry = _build_matched_from_evidence(m.name, ev)
                matched_list.append(matched_entry)
                matched_keys.add(_norm(m.name))
                rescued.append((m.name, [f"{e['source']}: {e['ref']}" for e in ev]))
            else:
                still_missing.append(m)
        if rescued:
            for name, srcs in rescued:
                logger.info(
                    "gap_analyzer: rescued %s '%s' from missing -> matched via %s",
                    tier_label, name, srcs,
                )
        return still_missing

    missing_must = _rescue_missing(missing_must, matched_must, matched_must_keys, 'must-have')
    missing_nice = _rescue_missing(missing_nice, matched_nice, matched_nice_keys, 'nice-to-have')

    missing_must_keys = {_norm(m.name) for m in missing_must}
    missing_nice_keys = {_norm(m.name) for m in missing_nice}

    def _is_accounted(skill: str, missing_keys: set, matched_keys: set) -> bool:
        k = _norm(skill)
        if k in missing_keys or k in matched_keys:
            return True
        # Fuzzy: LLM may have used a slightly different spelling for a match.
        return bool(difflib.get_close_matches(k, list(matched_keys), n=1, cutoff=0.85))

    for js in must_skills:
        if _is_accounted(js, missing_must_keys, matched_must_keys):
            continue
        # PR 3e — Before dropping into missing, try deterministic rescue.
        ev = _collect_profile_evidence(js, profile_data)
        if ev:
            matched_must.append(_build_matched_from_evidence(js, ev))
            matched_must_keys.add(_norm(js))
            logger.info(
                "gap_analyzer: matched unaccounted must-have '%s' via %s",
                js, [f"{e['source']}: {e['ref']}" for e in ev],
            )
            continue
        logger.info("Reconciled unaccounted must-have '%s' -> missing_must_have", js)
        missing_must.append(MissingSkill(
            name=js, source_quote='', proximity=0.0,
            proximity_reason='No related evidence found in profile',
            bridge_hint=None,
        ))
        missing_must_keys.add(_norm(js))

    for js in nice_skills:
        if _is_accounted(js, missing_nice_keys, matched_nice_keys):
            continue
        ev = _collect_profile_evidence(js, profile_data)
        if ev:
            matched_nice.append(_build_matched_from_evidence(js, ev))
            matched_nice_keys.add(_norm(js))
            logger.info(
                "gap_analyzer: matched unaccounted nice-to-have '%s' via %s",
                js, [f"{e['source']}: {e['ref']}" for e in ev],
            )
            continue
        logger.info("Reconciled unaccounted nice-to-have '%s' -> missing_nice_to_have", js)
        missing_nice.append(MissingSkill(
            name=js, source_quote='', proximity=0.0,
            proximity_reason='No related evidence found in profile',
            bridge_hint=None,
        ))
        missing_nice_keys.add(_norm(js))

    return TieredGapAnalysisResult(
        matched_must_have=matched_must,
        matched_nice_to_have=matched_nice,
        missing_must_have=missing_must,
        missing_nice_to_have=missing_nice,
        soft_skill_gaps=list(result.soft_skill_gaps or []),
    )


def _fallback_gap_analysis(profile, job, must_skills, nice_skills):
    """No-LLM fallback. Fuzzy-match each JD skill against the candidate's
    flat skill list; everything that doesn't fuzzy-match lands in missing_*
    with proximity=0.0 and the honest stub reason."""
    user_skills_list = []
    for s in profile.skills or []:
        if isinstance(s, dict):
            name = s.get('name', '')
            if name:
                user_skills_list.append(name.lower().strip())
        elif isinstance(s, str):
            user_skills_list.append(s.lower().strip())

    def _bucket(jd_skills):
        matched, missing = [], []
        for js in jd_skills:
            close = difflib.get_close_matches(js.lower().strip(), user_skills_list, n=1, cutoff=0.8)
            if close:
                matched.append(MatchedSkill(
                    name=js, evidence_source='skills', evidence_quote=close[0][:140],
                ))
            else:
                missing.append(MissingSkill(
                    name=js, source_quote='', proximity=0.0,
                    proximity_reason='No related evidence found in profile (fallback path)',
                    bridge_hint=None,
                ))
        return matched, missing

    mmh, miss_must = _bucket(must_skills)
    mnh, miss_nice = _bucket(nice_skills)

    score = compute_match_score(mmh, miss_must, mnh, miss_nice)
    avg_p = _avg_proximity(miss_must, miss_nice)
    band = match_band(score)

    return {
        'matched_must_have':    [m.model_dump() for m in mmh],
        'matched_nice_to_have': [m.model_dump() for m in mnh],
        'missing_must_have':    [m.model_dump() for m in miss_must],
        'missing_nice_to_have': [m.model_dump() for m in miss_nice],
        'soft_skill_gaps':      [],
        'similarity_score':     score,
        'match_band':           band,
        'avg_proximity':        avg_p,
        'matched_skills':       [m.name for m in mmh + mnh],
        'missing_skills':       [m.name for m in miss_must + miss_nice],
        'partial_skills':       [],
        'critical_missing_skills': [m.name for m in miss_must],
        'seniority_mismatch':   None,
        'analysis_method':      'fallback',
    }
