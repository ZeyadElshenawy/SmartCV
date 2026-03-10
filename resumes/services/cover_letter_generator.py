import logging
from profiles.services.llm_engine import get_llm_client, LLM_MODEL

logger = logging.getLogger(__name__)


def _get_skill_names(skills) -> list:
    """Safely extract skill name strings from a list that may contain dicts or strings."""
    names = []
    for s in (skills or []):
        if isinstance(s, dict):
            name = s.get('name', '').strip()
            if name:
                names.append(name)
        elif isinstance(s, str) and s.strip():
            names.append(s.strip())
    return names


def generate_cover_letter_content(profile, job):
    """
    Generate a highly personalized cover letter linking the user's profile to the job description.
    """
    client = get_llm_client()
    
    if not client:
        logger.warning("LLM unavailable for cover letter generation")
        return "Please configure the LLM API key to generate a tailored cover letter."
        
    prompt = f"""You are an expert career agent and copywriter. Write a highly tailored, professional cover letter for the following user and job.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company or 'the company'}
- Description: {job.description[:1500]}

USER PROFILE:
- Name: {profile.full_name}
- Skills: {', '.join(_get_skill_names(profile.skills))}
- Summary: {profile.data_content.get('summary', '')}
- Recent Experiences: {str(profile.experiences[:2]) if profile.experiences else 'None provided'}

RULES for the Cover Letter:
1. Make it exactly 3 paragraphs long.
2. Opening paragraph: State the role applied for and hook the reader with the most relevant achievement.
3. Body paragraph: Connect a maximum of two past experiences/skills directly to solving the core problem identified in the job description. Do not just list skills. Give concrete examples.
4. Closing paragraph: Express enthusiasm for the value they can bring to {job.company or 'the team'}, and include a professional call to action.
5. Tone: Confident, professional, clear, and concise. No fluff or generic buzzwords.
6. Do not include placeholder addresses at the top (like [Company Address]). Just start with "Dear Hiring Manager," or "Dear [Company Name] Team,".

Output ONLY the text of the cover letter, nothing else."""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,  # Or whichever default model the engine uses
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            timeout=45,
        )
        
        content = response.choices[0].message.content.strip()
        logger.info(f"Generated cover letter for job {job.id}")
        return content
        
    except Exception as e:
        logger.exception(f"Cover letter generation failed: {e}")
        return "An error occurred while generating the cover letter. Please try again."
