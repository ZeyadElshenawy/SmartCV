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
    
    # Build a slim version of CV data to save tokens — drop raw_text and empty fields
    slim_cv = {k: v for k, v in filtered_cv.items()
               if k != 'raw_text' and v and k not in ('normalized_summary', 'objective')}

    prompt = f"""You are an EXPERT resume optimization strategist. Create a PROFESSIONAL, ATS-optimized resume tailored for this job.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company}
- Required Skills: {', '.join(job.extracted_skills or [])}
- Job Description: {job.description[:1000]}

COMPLETE CV DATA:
{json.dumps(slim_cv, indent=2)}

MATCHED SKILLS (high priority): {', '.join(gap_analysis.matched_skills if hasattr(gap_analysis, 'matched_skills') else [])}

=== FIELD MAPPING (CRITICAL — the CV data uses different field names than the output schema) ===
- CV `experiences[].highlights` array → output `experience[].description` array (rewrite each bullet)
- CV `experiences[].start_date` / `end_date` → output `experience[].duration` (combine as "Aug 2025 - Present")
- CV `experiences[].title` → output `experience[].title`
- CV `education[].graduation_year` → output `education[].year`
- CV `education[].degree` + `field` → output `education[].degree` (combine as "Bachelor of Computer Science")
- CV `certifications[].url` → output `certifications[].url` (PRESERVE all certification URLs exactly)
- CV `projects[].description` or `highlights` → output `projects[].description` array (rewrite as bullets)
- Include ALL certifications from the CV data — do NOT truncate or omit any.

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

Make it PROFESSIONAL and ATS-OPTIMIZED."""

    try:
        structured_llm = get_structured_llm(ResumeContentResult, temperature=0.7, max_tokens=8192)
        result = structured_llm.invoke(prompt)

        resume_content = result.model_dump()
        # Guarantee data integrity — fill in anything the LLM left empty or
        # mis-mapped from the profile. The LLM is good at rewriting but often
        # drops sections or uses wrong field names (e.g. graduation_year vs year).
        resume_content = _ensure_profile_data_preserved(resume_content, raw_cv_data)
        logger.info(f"✓ Generated tailored resume with sections: {list(resume_content.keys())}")
        return resume_content

    except Exception as e:
        logger.exception(f"Resume generation error: {e}")
        fallback = {
            "professional_title": job.title,
            "professional_summary": f"Experienced professional seeking {job.title} position at {job.company}.",
            "skills": job.extracted_skills[:10] if job.extracted_skills else [],
            "experience": [],
            "education": [],
            "projects": [],
            "certifications": [],
            "languages": [],
        }
        return _ensure_profile_data_preserved(fallback, raw_cv_data)


def _ensure_profile_data_preserved(resume_content: dict, profile_data: dict) -> dict:
    """
    Map profile fields to resume schema as a guaranteed fallback.

    The LLM is supposed to restructure profile data into ResumeContentResult,
    but it sometimes returns empty sections or keeps profile field names
    (e.g. `graduation_year` instead of `year`, `highlights` instead of
    `description`). This function fills the gaps so the edit page always
    renders populated fields.
    """
    if not profile_data:
        return resume_content

    # --- Experience ---
    if not resume_content.get('experience') and profile_data.get('experiences'):
        resume_content['experience'] = []
        for exp in profile_data['experiences']:
            start = exp.get('start_date') or ''
            end = exp.get('end_date') or ''
            duration = f"{start} - {end}".strip(' -') if (start or end) else ''
            description = exp.get('highlights') or exp.get('achievements') or exp.get('description') or []
            if isinstance(description, str):
                description = [line.strip() for line in description.split('\n') if line.strip()]
            resume_content['experience'].append({
                'title': exp.get('title', ''),
                'company': exp.get('company', ''),
                'duration': duration,
                'description': description,
            })

    # --- Education ---
    if profile_data.get('education'):
        existing_edu = resume_content.get('education') or []
        # If LLM returned education but left `year` blank, patch from profile
        for i, edu in enumerate(existing_edu):
            if not edu.get('year') and i < len(profile_data['education']):
                src = profile_data['education'][i]
                edu['year'] = src.get('graduation_year') or src.get('year') or ''
                if not edu.get('degree') and src.get('field'):
                    edu['degree'] = f"{src.get('degree', '')} of {src['field']}".strip(' of')
        # If LLM returned nothing, rebuild from profile
        if not existing_edu:
            existing_edu = []
            for edu in profile_data['education']:
                degree = edu.get('degree', '')
                field = edu.get('field', '')
                full_degree = f"{degree} of {field}".strip(' of') if field else degree
                existing_edu.append({
                    'degree': full_degree,
                    'institution': edu.get('institution', ''),
                    'year': edu.get('graduation_year') or edu.get('year') or '',
                })
        resume_content['education'] = existing_edu

    # --- Projects ---
    if not resume_content.get('projects') and profile_data.get('projects'):
        resume_content['projects'] = []
        for proj in profile_data['projects']:
            description = proj.get('description') or proj.get('highlights') or []
            if isinstance(description, str):
                description = [line.strip() for line in description.split('\n') if line.strip()]
            resume_content['projects'].append({
                'name': proj.get('name', ''),
                'description': description,
                'url': proj.get('url') or '',
            })

    # --- Certifications ---
    if not resume_content.get('certifications') and profile_data.get('certifications'):
        resume_content['certifications'] = []
        for cert in profile_data['certifications']:
            resume_content['certifications'].append({
                'name': cert.get('name', ''),
                'issuer': cert.get('issuer') or '',
                'date': cert.get('date') or '',
                'url': cert.get('url') or '',
            })

    # --- Languages (spoken only) ---
    if not resume_content.get('languages') and profile_data.get('languages'):
        langs = profile_data['languages']
        if isinstance(langs, list):
            resume_content['languages'] = [l if isinstance(l, str) else l.get('name', '') for l in langs]

    # --- Skills ---
    if not resume_content.get('skills') and profile_data.get('skills'):
        resume_content['skills'] = [
            s.get('name', '') if isinstance(s, dict) else str(s)
            for s in profile_data['skills']
        ]

    return resume_content


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
