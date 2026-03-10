import logging
from profiles.services.llm_engine import get_llm_client, LLM_MODEL

logger = logging.getLogger(__name__)

def generate_negotiation_script(profile, job, current_offer, target_salary):
    """
    Generate a data-backed salary negotiation script using the LLM.
    """
    client = get_llm_client()
    
    if not client:
        logger.warning("LLM unavailable for salary negotiation")
        return "Please configure the LLM API key to generate a negotiation script."
        
    prompt = f"""You are an expert tech recruiter and salary negotiation coach. My user just received an offer and wants to negotiate for a higher salary.

USER PROFILE:
- Name: {profile.full_name}
- Top Skills: {', '.join(profile.skills[:5])}
- Experience Level: {len(profile.experiences)} past roles listed.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company or 'the company'}
- Description: {job.description[:500]}...

THE OFFER:
- Current Offer: {current_offer}
- Target Salary: {target_salary}

Generate a professional, polite, and persuasive email script they can send to the recruiter to ask for the target salary.
The argument should be firmly rooted in the specific skills they bring to the table (combining their profile with the job description) and market rate.

Do NOT include placeholder addresses. Just start with "Hi [Recruiter Name]," and end with a professional sign off. Output ONLY the email script."""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            timeout=45,
        )
        
        content = response.choices[0].message.content.strip()
        logger.info(f"Generated negotiation script for job {job.id}")
        return content
        
    except Exception as e:
        logger.exception(f"Salary negotiation failed: {e}")
        return "An error occurred while generating the script. Please try again."
