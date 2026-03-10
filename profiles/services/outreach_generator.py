import logging
import json
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


def generate_outreach_campaign(profile, job):
    """
    Generate tailored cold outreach templates (LinkedIn & Email) for networking.
    """
    client = get_llm_client()
    
    if not client:
        logger.warning("LLM unavailable for outreach generation")
        return {}
        
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
2. A slightly longer Cold Email (max 3 paragraphs) to a Hiring Manager. It should state interest, highlight 1 specific relevant achievement, and end with a low-friction call to action (e.g., asking for a 10-minute chat or brief feedback).

Output MUST be strictly valid JSON in the following format:
{{
    "linkedin_message": "...",
    "cold_email_subject": "...",
    "cold_email_body": "..."
}}

Return ONLY JSON. Do not include markdown blocks like ```json around the output."""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            timeout=45,
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON
        import re
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if match:
             json_text = match.group(1)
        else:
            json_text = content
            
        campaign = json.loads(json_text)
        logger.info(f"Generated outreach campaign for {job.id}")
        return campaign
        
    except Exception as e:
        logger.exception(f"Outreach generation failed: {e}")
        return {}
