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
            years = s.get('years', 'Unknown')
            prof = s.get('proficiency', '')
            enriched.append(f"{name} - {years} years ({prof})".strip(" ()"))
        else:
            enriched.append(str(s))
    return enriched

def _format_experience_and_projects(profile):
    context = []
    if profile.experiences:
        context.append("EXPERIENCE HIGHLIGHTS:")
        for exp in profile.experiences[:3]:
            title = exp.get('title', '')
            hl = exp.get('highlights', [])
            if title and hl:
                context.append(f"- {title}: " + "; ".join(hl[:2]))
                
    if profile.projects:
        context.append("PROJECT HIGHLIGHTS:")
        for proj in profile.projects[:3]:
            name = proj.get('name', '')
            desc = proj.get('description', '')
            hl = proj.get('highlights', [])
            summary = desc or ("; ".join(hl[:2]) if hl else "")
            if name and summary:
                context.append(f"- {name}: {summary}")
                
    return "\n".join(context)

def compute_gap_analysis(profile, job):
    """
    Pure-LLM gap analysis. No local embeddings required.
    The LLM evaluates skill matches, gaps, and returns a similarity score directly.
    """
    job_skills = job.extracted_skills
    user_skills = _enrich_skill_payload(profile.skills or [])
    applied_context = _format_experience_and_projects(profile)
    
    try:
        prompt = f"""Compare the Candidate's Profile against the Job Requirements.
        
JOB REQUIREMENTS: {json.dumps(job_skills)}
CANDIDATE SKILLS: {json.dumps(user_skills, default=str)}

CANDIDATE APPLIED EXPERIENCE & PROJECTS:
{applied_context}

Task:
1. Identify CRITICAL MISSING SKILLS (Hard technical skills the user clearly lacks).
2. Identify SOFT SKILL GAPS (e.g. Leadership, Communication if required and missing).
3. Identify MATCHED SKILLS (Skills the user has that match requirements).
4. Provide a SIMILARITY SCORE from 0.0 to 1.0 representing overall job fit.
   - Consider: skill coverage, experience relevance, seniority alignment.
   - 0.0 = no match at all, 1.0 = perfect candidate.

=== VERY IMPORTANT OUTPUT RULES ===
- DO NOT OUTPUT ANY PREAMBLE OR EXPLANATION TEXT.
- IMMEDIATELY call the provided function to return the JSON layout.
- You must consider the candidate's applied experience when determining if they have a skill, even if it's not explicitly in the CANDIDATE SKILLS list.
- STRICT DIRECTIONAL MATCHING: Allow specific tools to satisfy broader category requirements. If the job requires a broad category (e.g., 'Data Visualization' or 'SQL'), specific tools natively belonging to that category in the candidate's profile (e.g., 'Matplotlib', 'Power BI' or 'MySQL') firmly count as a MATCH. However, if the job requires a specific tool (e.g., 'React'), a broad category in the candidate profile (e.g., 'Frontend') DOES NOT MATCH."""

        structured_llm = get_structured_llm(GapAnalysisResult, temperature=0.1, max_tokens=512)
        result = structured_llm.invoke(prompt)
        
        # Clamp similarity score to valid range
        score = max(0.0, min(1.0, result.similarity_score))
        
        return {
            'matched_skills': result.matched_skills,
            'missing_skills': result.critical_missing_skills,
            'partial_skills': [],
            'soft_skill_gaps': result.soft_skill_gaps,
            'critical_missing_skills': result.critical_missing_skills,
            'seniority_mismatch': None,
            'similarity_score': round(score, 2),
            'analysis_method': 'llm'
        }
            
    except Exception as e:
        logger.error(f"LLM Gap Analysis failed: {e}. Falling back to set difference.")

    # Fallback to Set Difference and Fuzzy Match
    user_skills_list = []
    for s in profile.skills or []:
        if isinstance(s, dict):
            name = s.get('name', '')
            if name: user_skills_list.append(name.lower().strip())
        elif isinstance(s, str):
            user_skills_list.append(s.lower().strip())
    
    matched_skills = []
    missing_skills = []
    
    for js in job.extracted_skills:
        js_clean = js.lower().strip()
        matches = difflib.get_close_matches(js_clean, user_skills_list, n=1, cutoff=0.85)
        if matches:
            matched_skills.append(js)
        else:
            missing_skills.append(js)
    
    total = max(len(job.extracted_skills), 1)
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
