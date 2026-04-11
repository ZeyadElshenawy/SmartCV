import json
import logging
import re
from typing import Dict, Any
from profiles.services.llm_engine import get_structured_llm, get_llm
from profiles.services.schemas import ResumeContentResult

logger = logging.getLogger(__name__)


def generate_resume_content(profile, job, gap_analysis):
    """
    Generate PROFESSIONAL, ATS-optimized tailored resume using LangChain structured output.
    """
    raw_cv_data = profile.data_content or {}
    
    if not raw_cv_data:
        logger.warning("raw_cv_data not available, using core fields")
        raw_cv_data = {
            'skills': profile.skills or [],
            'experiences': profile.experiences or [],
            'education': profile.education or [],
            'projects': profile.projects or [],
            'certifications': profile.certifications or []
        }
    
    filtered_cv = raw_cv_data
    
    prompt = f"""You are an EXPERT resume optimization strategist. Create a PROFESSIONAL, ATS-optimized resume tailored for this job.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company}
- Required Skills: {', '.join(job.extracted_skills or [])}
- Job Description: {job.description[:1000]}

COMPLETE CV DATA (relevant sections only):
{json.dumps(filtered_cv, indent=2)}

MATCHED SKILLS (high priority): {', '.join(gap_analysis.matched_skills if hasattr(gap_analysis, 'matched_skills') else [])}

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent, add, or imply skills, keywords, achievements, metrics, job titles, or any other content not present in the original resume.
- Only rewrite and restructure what already exists.

=== REMOVE FROM RESUMES ===
- Street/home address (city and country are fine)
- Objective statements
- Graduation year if the degree is more than 10 years old
- Work experience older than 15 years (20 years max for executive roles)
- High school experience
- GPA or university grades
- Headshot or photo references
- Employment type labels (contract, part-time, etc.)
- Salary expectations
- First-person "I" statements

=== LANGUAGE & STYLE ===
- Replace these words: Spearheaded -> Led, Leveraged -> Used/Applied, Utilized -> Used, Synergized -> Collaborated, Streamlined -> Simplified/Improved, Robust -> Strong, Demonstrated -> Showed/Proved, Facilitated -> Helped/Enabled.
- Remove completely: Dynamic, Innovative, Passionate, Results-driven.
- Replace em dashes (—) with a comma or delete them.
- Avoid repetitive sentence structure across bullet points.

=== REWRITE & STRUCTURING ===
1. PROFESSIONAL SUMMARY: Replace objective statement with a professional summary written in third person (no "I" statements). Reflect ONLY experience already present in the resume.
2. SKILLS SECTION: Remove ALL soft skills. Keep ONLY hard/technical skills explicitly listed.
3. EXPERIENCE BULLETS: Start each bullet with a strong action verb. Use XYZ structure where possible.

=== THEME MIRRORING ===
1. Identify 3 key themes from the job posting.
2. Mirror those themes in the title, summary, and bullet point headings.
3. CRITICAL: ONLY mirror themes genuinely supported by existing experience.

Make it PROFESSIONAL and ATS-OPTIMIZED.

=== CRITICAL JSON REQUIREMENT ===
You MUST output your response by calling the provided tool/function with a valid JSON payload matching the schema. DO NOT output conversational text directly."""

    try:
        structured_llm = get_structured_llm(ResumeContentResult, temperature=0.7, max_tokens=4000)
        result = structured_llm.invoke(prompt)
        
        resume_content = result.model_dump()
        logger.info(f"✓ Generated PROFESSIONAL tailored resume with sections: {list(resume_content.keys())}")
        return resume_content
        
    except Exception as e:
        logger.exception(f"Resume generation error: {e}")
        return {
            "professional_title": job.title,
            "professional_summary": f"Experienced professional seeking {job.title} position at {job.company}.",
            "skills": job.extracted_skills[:10] if job.extracted_skills else [],
            "experience": filtered_cv.get('experiences', [])[:3] if filtered_cv else [],
            "education": filtered_cv.get('education', []) if filtered_cv else []
        }


def calculate_ats_score(resume_content, job_skills):
    """Calculate ATS compatibility score based on keyword presence."""
    resume_text = json.dumps(resume_content).lower()
    
    matched_keywords = 0
    keyword_counts = {}
    
    if not job_skills:
        return 0
        
    for skill in job_skills:
        skill_lower = skill.lower()
        count = resume_text.count(skill_lower)
        keyword_counts[skill] = count
        
        if count > 0:
            matched_keywords += 1
            if count > 4:
                logger.warning(f"Keyword stuffing detected: '{skill}' appears {count} times")
    
    score = (matched_keywords / len(job_skills)) * 100
    logger.info(f"ATS Score: {score}% - Matched {matched_keywords}/{len(job_skills)} keywords")
    return round(score, 1)
