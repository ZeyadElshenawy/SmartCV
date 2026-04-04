import logging
from profiles.services.llm_engine import get_llm
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

def generate_negotiation_script(profile, job, current_offer, target_salary):
    """
    Generate a data-backed salary negotiation script using LangChain + Groq.
    """
    prompt = f"""You are an expert tech recruiter and salary negotiation coach. My user just received an offer and wants to negotiate for a higher salary.

USER PROFILE:
- Name: {profile.full_name}
- Top Skills: {', '.join([s.get('name', str(s)) if isinstance(s, dict) else str(s) for s in profile.skills[:5]])}
- Experience Level: {len(profile.experiences)} past roles listed.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company or 'the company'}
- Description: {job.description[:500]}...

THE OFFER:
- Current Offer: {current_offer}
- Target Salary: {target_salary}

Generate a professional, polite, and persuasive email script they can send to the recruiter to ask for the target salary.
The argument should be firmly rooted in the specific skills they bring to the table and market rate.

=== LANGUAGE & STYLE RULES ===
- Replace these words: Spearheaded -> Led, Leveraged -> Used/Applied, Utilized -> Used.
- Remove completely: Dynamic, Innovative, Passionate, Results-driven.

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent, add, or imply skills, keywords, achievements, metrics, job titles, or any other content not present in the original USER PROFILE.

Do NOT include placeholder addresses. Just start with "Hi [Recruiter Name]," and end with a professional sign off. Output ONLY the email script."""

    try:
        llm = get_llm(temperature=0.7, max_tokens=2048)
        result = llm.invoke([HumanMessage(content=prompt)])
        
        content = result.content.strip()
        logger.info(f"Generated negotiation script for job {job.id}")
        return content
        
    except Exception as e:
        logger.exception(f"Salary negotiation failed: {e}")
        return "An error occurred while generating the script. Please try again."
