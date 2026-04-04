import logging
from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import OutreachCampaignResult

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


def generate_outreach_campaign(profile, job):
    """
    Generate tailored cold outreach templates (LinkedIn & Email) using structured output.
    """
    prompt = f"""You are an expert tech recruiter and networking coach. My user wants to reach out to a Hiring Manager or Senior Engineer at the company they are applying to.

USER PROFILE:
- Name: {profile.full_name}
- Top Skills: {', '.join(_get_skill_names(profile.skills)[:5])}
- Current Summary: {profile.data_content.get('summary', 'Not provided')}

TARGET ROLE:
- Title: {job.title}
- Company: {job.company or 'the target company'}

Generate two variations of networking messages:
1. A short, punchy LinkedIn connection request (max 300 characters). It must be highly personalized and ask an insightful question about the role or company.
2. A slightly longer Cold Email (max 3 paragraphs) to a Hiring Manager. It should state interest, highlight 1 specific relevant achievement, and end with a low-friction call to action.

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent, add, or imply skills, keywords, achievements, metrics, job titles, or any other content not present in the original USER PROFILE.
- Only construct messages based on what already exists."""

    try:
        structured_llm = get_structured_llm(OutreachCampaignResult, temperature=0.7, max_tokens=1024)
        result = structured_llm.invoke(prompt)
        
        campaign = result.model_dump()
        logger.info(f"Generated outreach campaign for {job.id}")
        return campaign
        
    except Exception as e:
        logger.exception(f"Outreach generation failed: {e}")
        return {}
