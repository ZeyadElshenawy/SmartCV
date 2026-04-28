import logging
import json
import difflib
from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import GapAnalysisResult

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


def compute_gap_analysis(profile, job):
    """
    Pure-LLM gap analysis. No local embeddings required.
    The LLM evaluates skill matches, gaps, and returns a similarity score directly.
    """
    job_skills = job.extracted_skills or []
    candidate_context = _build_full_candidate_context(profile)

    # Early exits: when there's nothing to compare, don't waste an LLM call
    # and don't return a misleading 0% score.
    if not job_skills:
        logger.info("Gap analysis skipped: job has no extracted skills")
        return {
            'matched_skills': [],
            'missing_skills': [],
            'partial_skills': [],
            'soft_skill_gaps': [],
            'critical_missing_skills': [],
            'seniority_mismatch': None,
            'similarity_score': 0.0,
            'analysis_method': 'no_job_skills',
        }

    if not candidate_context.strip():
        logger.info("Gap analysis skipped: profile is effectively empty")
        return {
            'matched_skills': [],
            'missing_skills': list(job_skills),
            'partial_skills': [],
            'soft_skill_gaps': [],
            'critical_missing_skills': list(job_skills)[:5],
            'seniority_mismatch': None,
            'similarity_score': 0.0,
            'analysis_method': 'empty_profile',
        }

    try:
        prompt = f"""You are an expert technical recruiter. Compare the candidate's FULL profile against the job requirements.

JOB TITLE: {job.title}
JOB COMPANY: {job.company or 'Unknown'}
JOB REQUIRED SKILLS: {json.dumps(job_skills)}

{candidate_context}

=== YOUR TASK ===
1. Identify MATCHED SKILLS — skills the candidate demonstrably has (from skills list, experience, projects, OR certifications).
2. Identify CRITICAL MISSING SKILLS — hard technical skills the candidate clearly lacks.
3. Identify SOFT SKILL GAPS — soft skills required but missing.
4. Provide a similarity_score from 0.0 to 1.0 representing overall job fit.

=== CRITICAL MATCHING RULES ===

RULE 1 — HOLISTIC EVIDENCE:
A skill is MATCHED if the candidate demonstrates it ANYWHERE in their profile:
- Explicitly listed in CANDIDATE SKILLS
- Demonstrated in WORK EXPERIENCE highlights or descriptions
- Used in PROJECT highlights or technologies
- Covered by a CERTIFICATION or training course
- Corroborated by GITHUB ACTIVITY — a language with multiple public repos OR a top repo using that tech is strong evidence of working knowledge
- Corroborated by GOOGLE SCHOLAR — published work in a topic implies deep knowledge of the methods/tools mentioned in the paper titles
- Corroborated by KAGGLE — competition tier (Expert/Master/Grandmaster) + medal counts in a category prove practical skill in that domain (notebooks → coding, competitions → modeling)
- Is a foundational prerequisite of skills they already have (e.g., someone with "Regression" and "Classification" has implicit knowledge of "Statistics" and "Probabilities")

RULE 2 — DIRECTIONAL SPECIFICITY (very important):
- If the job requires a BROAD category (e.g., "SQL", "Data Visualization", "Cloud"), and the candidate has a SPECIFIC tool in that category (e.g., "MySQL"/"PostgreSQL" for SQL, "Matplotlib"/"Power BI" for Data Visualization, "Azure" for Cloud), that is a MATCH.
- If the job requires a SPECIFIC tool (e.g., "Tableau"), a broad category (e.g., "Data Visualization") alone is NOT a match.

RULE 3 — NO DUPLICATES:
- Each required skill must appear in EXACTLY ONE list: either matched_skills OR critical_missing_skills. Never both.
- Use the EXACT spelling from the JOB REQUIRED SKILLS list for consistency.

RULE 4 — CASE-INSENSITIVE:
- "PySpark" and "Pyspark" and "pyspark" are the SAME skill. Do not list them separately.

RULE 5 — SENIORITY & CAREER-SWITCH SIGNALS (soft_skill_gaps):
- If the job title implies a seniority (Senior, Staff, Lead, Principal) but the candidate has <3 years of relevant experience, add a concise note to soft_skill_gaps like "Seniority gap: job asks for Senior; candidate reads as mid-level".
- If the candidate's experience is in a different domain than the target role (e.g., teaching background applying to SWE), add "Career transition: limited direct industry experience in [target domain]".
- These should be CONSTRUCTIVE observations, not blockers. Keep each under 20 words.

=== SIMILARITY SCORE RUBRIC (CRITICAL) ===

Compute the similarity_score from the matched/missing breakdown YOU produced above. Do NOT pull a number from intuition — anchor it to the ratio.

Let M = len(matched_skills), X = len(critical_missing_skills), T = M + X (total accounted JD skills).

Base score = M / T (rounded to nearest 0.05).

Then APPLY adjustments (cumulative, but final score must stay in [0.0, 1.0]):
- Subtract 0.05 per soft_skill_gaps entry (cap −0.15 total). Soft gaps lower the score modestly; they don't dominate it.
- Add 0.05 if M >= 0.7 * T AND the GitHub/Scholar/Kaggle blocks corroborate at least 2 of the matched skills (strong evidence bonus).
- If T == 0 (job has no required skills), return 0.0.

Examples:
- 18 matched, 3 missing, 0 soft gaps → base 0.86 → score 0.85 (rounded).
- 14 matched, 7 missing, 1 soft gap → base 0.67, −0.05 → score 0.60.
- 5 matched, 16 missing, 0 soft gaps → base 0.24 → score 0.25.
- 0 matched, 14 missing → base 0.0 → score 0.0.

DO NOT score below the base ratio because the candidate "feels junior" — express that in soft_skill_gaps, not the headline score. A candidate who matches 14 of 21 must-have skills should always score ~0.65, never 0.10.

=== OUTPUT ===
Return ONLY the structured JSON via the provided function. No preamble."""

        # max_tokens trimmed 2000 → 1500. Typical structured responses for
        # gap analysis land around 800-1200 tokens (matched + missing lists +
        # 2-3 soft-gap notes + a similarity score). 2000 was conservative and
        # measurably extended LLM response latency. 1500 leaves a 25% safety
        # margin without burning generation time on dead headroom.
        structured_llm = get_structured_llm(GapAnalysisResult, temperature=0.1, max_tokens=1500, task="gap_analyzer")
        result = structured_llm.invoke(prompt)

        # Clamp similarity score to valid range
        score = max(0.0, min(1.0, result.similarity_score))

        # ---- Phase 2: Programmatic Reconciliation ----
        # Ensure every job skill is accounted for in exactly one list.
        matched_set = {s.lower().strip() for s in result.matched_skills}
        missing_set = {s.lower().strip() for s in result.critical_missing_skills}

        # Deduplicate: remove anything that's in both
        deduped_missing = [s for s in result.critical_missing_skills if s.lower().strip() not in matched_set]
        missing_set = {s.lower().strip() for s in deduped_missing}

        # Reconcile: find skills the LLM forgot to categorize
        all_accounted = matched_set | missing_set
        for job_skill in job_skills:
            js_lower = job_skill.lower().strip()
            if js_lower in all_accounted:
                continue
            # Fuzzy check: did the LLM match it under a slightly different name?
            close = difflib.get_close_matches(js_lower, matched_set, n=1, cutoff=0.85)
            if close:
                # LLM matched it with a variant spelling — count as matched
                logger.debug("Reconciled '%s' as matched (fuzzy: '%s')", job_skill, close[0])
                continue
            # Not accounted for anywhere — conservatively add to missing
            logger.info("Reconciled unaccounted skill '%s' → missing", job_skill)
            deduped_missing.append(job_skill)

        return {
            'matched_skills': result.matched_skills,
            'missing_skills': deduped_missing,
            'partial_skills': [],
            'soft_skill_gaps': result.soft_skill_gaps,
            'critical_missing_skills': deduped_missing,
            'seniority_mismatch': None,
            'similarity_score': round(score, 2),
            'analysis_method': 'llm'
        }

    except Exception as e:
        logger.error(f"LLM Gap Analysis failed: {e}. Falling back to set difference.")

    # ---- Fallback: fuzzy set matching (no LLM) ----
    user_skills_list = []
    for s in profile.skills or []:
        if isinstance(s, dict):
            name = s.get('name', '')
            if name:
                user_skills_list.append(name.lower().strip())
        elif isinstance(s, str):
            user_skills_list.append(s.lower().strip())

    matched_skills = []
    missing_skills = []

    for js in job_skills:
        js_clean = js.lower().strip()
        matches = difflib.get_close_matches(js_clean, user_skills_list, n=1, cutoff=0.8)
        if matches:
            matched_skills.append(js)
        else:
            missing_skills.append(js)

    total = max(len(job_skills), 1)
    fallback_score = round(len(matched_skills) / total, 2)

    return {
        'matched_skills': matched_skills,
        'missing_skills': missing_skills,
        'partial_skills': [],
        'soft_skill_gaps': [],
        'critical_missing_skills': missing_skills[:5],
        'seniority_mismatch': None,
        'similarity_score': fallback_score,
        'analysis_method': 'fallback'
    }
