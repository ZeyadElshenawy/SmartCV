import logging
import json
import difflib
from profiles.services.llm_engine import get_structured_llm
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
            desc = exp.get('description', '')
            highlights = exp.get('highlights', [])

            line = f"- {title} at {company}"
            if desc:
                line += f": {desc[:300]}"
            if highlights:
                hl_text = "; ".join(str(h) for h in highlights[:4])
                line += f" | Highlights: {hl_text}"
            lines.append(line)
        sections.append("\n".join(lines))

    # --- Projects ---
    if profile.projects:
        lines = ["PROJECTS:"]
        for proj in (profile.projects or [])[:5]:
            if not proj:
                continue
            name = proj.get('name', '')
            desc = proj.get('description', '')
            highlights = proj.get('highlights', [])
            techs = proj.get('technologies', [])

            line = f"- {name}"
            if desc:
                line += f": {desc[:200]}"
            if highlights:
                hl_text = "; ".join(str(h) for h in highlights[:3])
                line += f" | {hl_text}"
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
'multiple'}}. Provide a ≤140-char evidence_quote that proves the match.

RULE 2 — DIRECTIONAL SPECIFICITY:
- BROAD JD (e.g. "SQL", "Cloud") + SPECIFIC CV (e.g. "MySQL", "AWS") = MATCH
- SPECIFIC JD (e.g. "Tableau") + BROAD CV ("Data Viz") = NOT a match

RULE 3 — NO DUPLICATES, EXACT SPELLING:
- Each JD skill appears in EXACTLY ONE list. Never both matched_* and missing_*.
- Use the exact spelling from the JD list (case-insensitive equality).

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
Return ONLY the structured JSON via the provided function. No preamble.
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
        # Most common failure mode: the proximity<1.0 validator rejected the
        # LLM's response. Retry ONCE with the error appended so the model
        # learns the constraint and re-routes any 1.0 skills.
        msg = str(exc).lower()
        is_proximity = 'proximity' in msg or 'less than 1' in msg
        if is_proximity:
            retry_prompt = base_prompt + (
                "\n\n=== PRIOR ATTEMPT FAILED ===\n"
                "Your last response was rejected because at least one missing "
                "skill had proximity == 1.0. Skills at 1.0 belong in matched_*, "
                "NOT missing_*. Either re-route that skill into the matched "
                "tier with an evidence_quote, or lower its proximity to 0.8 "
                "(meaning: exact skill present in CV but thin evidence).\n"
            )
            try:
                result = _invoke(retry_prompt, attempt_label='retry')
                logger.info("Gap analyzer recovered from proximity=1.0 on retry")
            except Exception as exc2:
                logger.error("Gap analyzer retry also failed: %s", exc2)
                return _fallback_gap_analysis(profile, job, must_skills, nice_skills)
        else:
            logger.error("LLM gap analysis failed: %s. Falling back to fuzzy set match.", exc)
            return _fallback_gap_analysis(profile, job, must_skills, nice_skills)

    # ---- Phase 2: Tier-aware reconciliation ----
    result = _reconcile_tier(result, must_skills, nice_skills)

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


def _reconcile_tier(result, must_skills: list, nice_skills: list):
    """Ensure every JD skill is accounted for in exactly one tier list.

    For each JD must-have not seen in matched_must_have or missing_must_have,
    append a MissingSkill with proximity=0.0 and the honest stub reason.
    Same for nice-to-have. Cross-tier dedupe: a skill in both matched_* and
    missing_* keeps the matched_* entry only.
    """
    def _norm(s: str) -> str:
        return (s or '').lower().strip()

    matched_must = list(result.matched_must_have)
    matched_nice = list(result.matched_nice_to_have)
    missing_must = list(result.missing_must_have)
    missing_nice = list(result.missing_nice_to_have)

    matched_must_keys = {_norm(m.name) for m in matched_must}
    matched_nice_keys = {_norm(m.name) for m in matched_nice}

    missing_must = [m for m in missing_must if _norm(m.name) not in matched_must_keys]
    missing_nice = [m for m in missing_nice if _norm(m.name) not in matched_nice_keys]

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
