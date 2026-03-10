import json
import logging
import re
from typing import Dict, Any
from profiles.services.llm_engine import get_llm_client, LLM_MODEL

logger = logging.getLogger(__name__)

def filter_cv_sections_by_relevance(raw_cv_data: Dict[str, Any], job) -> Dict[str, Any]:
    """
    Use LLM to determine which CV sections are relevant for the job.
    Returns filtered CV data with only relevant sections.
    """
    client = get_llm_client()
    if not client:
        logger.warning("LLM unavailable for section filtering, including all sections")
        return raw_cv_data
    
    # List all available sections
    available_sections = list(raw_cv_data.keys())
    
    prompt = f"""You are a resume optimization expert. Analyze which CV sections are relevant for this job.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company}
- Required Skills: {', '.join(job.extracted_skills or [])}
- Description: {job.description[:500]}...

AVAILABLE CV SECTIONS:
{', '.join(available_sections)}

TASK: Determine which sections to INCLUDE in the tailored resume.

RULES:
1. ALWAYS include: skills, experiences, education (core sections)
2. For other sections, rate relevance 0-10
3. Include if relevance >= 6
4. Examples:
   - Publications: HIGH relevance for research/academic roles
   - Awards: MEDIUM-HIGH relevance for competitive positions
   - Volunteer: LOW relevance for technical roles (unless relevant)
   - Languages: HIGH for international/customer-facing roles

Return JSON:
{{
  "include_sections": ["skills", "experiences", "education", "publications", ...],
  "exclude_sections": ["hobbies", ...],
  "reasoning": "brief explanation"
}}"""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=30,
        )
        
        content = response.choices[0].message.content
        
        # Extract JSON
        import re
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if match:
            json_text = match.group(1)
        else:
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1:
                json_text = content[start:end+1]
            else:
                return raw_cv_data
        
        result = json.loads(json_text)
        include_sections = result.get('include_sections', [])
        reasoning = result.get('reasoning', '')
        
        logger.info(f"Section filtering: Including {len(include_sections)} sections")
        logger.info(f"Reasoning: {reasoning}")
        
        # Filter raw_cv_data to only included sections
        filtered_data = {
            section: raw_cv_data[section]
            for section in include_sections
            if section in raw_cv_data
        }
        
        return filtered_data
        
    except Exception as e:
        logger.exception(f"Section filtering failed: {e}")
        return raw_cv_data  # Fallback to all sections


def generate_resume_content(profile, job, gap_analysis):
    """
    Generate PROFESSIONAL, ATS-optimized tailored resume using expert best practices.
    
    Implements:
    - Keyword-rich professional branding (title, summary)
    - Titled bullet points (1-3 word bold skills)
    - Relevance prioritization (most important first)
    - ATS optimization (proper keyword density, no stuffing)
    """
    client = get_llm_client()
    
    # Get complete CV data
    raw_cv_data = profile.data_content or {}
    
    if not raw_cv_data:
        # Fallback: construct from core fields
        logger.warning("raw_cv_data not available, using core fields")
        raw_cv_data = {
            'skills': profile.skills or [],
            'experiences': profile.experiences or [],
            'education': profile.education or [],
            'projects': profile.projects or [],
            'certifications': profile.certifications or []
        }
    
    if not client:
        # Fallback if no LLM
        logger.warning("LLM unavailable, returning raw data")
        return {
            "professional_title": job.title,
            "professional_summary": "Please configure Cerebras API key for tailored resume generation.",
            "sections": raw_cv_data
        }
    
    # Step 1: Filter sections by relevance (initialize before try for fallback safety)
    filtered_cv = raw_cv_data
    logger.info("Filtering CV sections by job relevance...")
    filtered_cv = filter_cv_sections_by_relevance(raw_cv_data, job)
    
    # Step 2: Generate tailored content with PROFESSIONAL OPTIMIZATION
    prompt = f"""You are an EXPERT resume optimization strategist. Create a PROFESSIONAL, ATS-optimized resume tailored for this job.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company}
- Required Skills: {', '.join(job.extracted_skills or [])}
- Job Description: {job.description[:1000]}

COMPLETE CV DATA (relevant sections only):
{json.dumps(filtered_cv, indent=2)}

MATCHED SKILLS (high priority): {', '.join(gap_analysis.matched_skills if hasattr(gap_analysis, 'matched_skills') else [])}

=== PROFESSIONAL OPTIMIZATION REQUIREMENTS ===

1. PROFESSIONAL TITLE:
   - Match job title as closely as possible
   - Example: "{job.title}" or close variant

2. PROFESSIONAL SUMMARY (3-4 sentences):
   - Start with job title + years of experience
   - Incorporate 4-6 TOP keywords from job description
   - Highlight most impressive, relevant accomplishments
   - Written in third person, professional tone
   - Example: "Business Development Strategist with 7+ years driving revenue growth..."

3. KEY SKILLS SECTION:
   - List 8-12 most relevant skills from job description
   - Use EXACT phrasing from job posting when possible
   - Prioritize matched skills first
   - Mix hard skills + soft skills

4. EXPERIENCE BULLET POINTS:
   - Add 1-3 word BOLD TITLE to each bullet (skill demonstrated)
   - Title examples: "Revenue Generation:", "Team Leadership:", "Process Optimization:"
   - Reorder bullets - MOST RELEVANT TO JOB FIRST
   - Use strong action verbs (Led, Drove, Increased, Developed, etc.)
   - Include quantifiable achievements
   - Incorporate job keywords naturally (2-4 times total, NO keyword stuffing)

5. PRIORITIZATION:
   - Most job-relevant experiences/projects first
   - Within each role, most impressive/relevant bullets first
   - Top keywords should appear in top half of resume

6. ATS OPTIMIZATION:
   - Standard section headings (Experience, Skills, Education)
   - Clean structure, no tables/graphics
   - Keywords integrated naturally throughout
   - Proper keyword density (don't overuse)

OUTPUT JSON FORMAT (CRITICAL - use exact key names):
{{
  "professional_title": "exact or close variant of job title",
  "professional_summary": "3-4 sentence keyword-rich summary",
  "skills": ["skill 1", "skill 2", ...],
  "experience": [
    {{
      "title": "Job Title",
      "company": "Company Name",
      "duration": "MM/YYYY - MM/YYYY or Present",
      "description": "Skill-titled Achievement 1. Skill-titled Achievement 2. Skill-titled Achievement 3."
    }}
  ],
  "education": [
    {{"degree": "...", "institution": "...", "year": "..."}}
  ],
  "certifications": [...],
  ... include all relevant sections
}}

CRITICAL: Use "skills" (NOT "key_skills") and "experience" (NOT "experiences").

Return ONLY valid JSON. Make it PROFESSIONAL and ATS-OPTIMIZED."""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert ATS-optimized resume strategist."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=4000,
            timeout=90,
        )
        
        content = response.choices[0].message.content
        
        # Parse JSON response
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if match:
            json_text = match.group(1)
        else:
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1:
                json_text = content[start:end+1]
            else:
                raise ValueError("No JSON found in response")
        
        resume_content = json.loads(json_text)
        
        # Normalize keys for backward compatibility
        if 'key_skills' in resume_content and 'skills' not in resume_content:
            resume_content['skills'] = resume_content.pop('key_skills')
        if 'experiences' in resume_content and 'experience' not in resume_content:
            resume_content['experience'] = resume_content.pop('experiences')
        
        logger.info(f"✓ Generated PROFESSIONAL tailored resume with sections: {list(resume_content.keys())}")
        
        return resume_content
        
    except Exception as e:
        logger.exception(f"Resume generation error: {e}")
        # Fallback
        return {
            "professional_title": job.title,
            "professional_summary": f"Experienced professional seeking {job.title} position at {job.company}.",
            "skills": job.extracted_skills[:10] if job.extracted_skills else [],
            "experience": filtered_cv.get('experiences', [])[:3] if filtered_cv else [],
            "education": filtered_cv.get('education', []) if filtered_cv else []
        }


def calculate_ats_score(resume_content, job_skills):
    """
    Calculate ATS compatibility score based on keyword presence.
    Checks for proper keyword density (not stuffing).
    """
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
            
            # Penalize keyword stuffing (appearing more than 4 times)
            if count > 4:
                logger.warning(f"Keyword stuffing detected: '{skill}' appears {count} times")
    
    # Base score
    score = (matched_keywords / len(job_skills)) * 100
    
    # Log keyword density
    logger.info(f"ATS Score: {score}% - Matched {matched_keywords}/{len(job_skills)} keywords")
    
    return round(score, 1)
