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

    return "\n\n".join(sections)


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

=== OUTPUT ===
Return ONLY the structured JSON via the provided function. No preamble."""

        structured_llm = get_structured_llm(GapAnalysisResult, temperature=0.1, max_tokens=2000)
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
