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


def generate_outreach_for_target(profile, job, target) -> dict:
    """Per-target message drafts for the outreach automation feature.

    `target` is anything with `.name`, `.role`, `.handle` attributes (typically
    a `jobs.services.people_finder.Target`). Returns:
        {"connect_message": str (≤300 chars), "follow_up_message": str}

    Uses the same OutreachCampaignResult schema as `generate_outreach_campaign`
    and inherits its anti-hallucination rule. Each call is one LLM invocation.
    """
    target_name = getattr(target, 'name', '') or getattr(target, 'handle', '')
    target_role = getattr(target, 'role', '') or 'someone at the company'

    prompt = f"""You are an expert tech recruiter and networking coach. My user wants to reach out to a specific person on LinkedIn about a job they care about.

USER PROFILE:
- Name: {profile.full_name}
- Top Skills: {', '.join(_get_skill_names(profile.skills)[:5])}
- Current Summary: {profile.data_content.get('summary', 'Not provided')}

TARGET ROLE:
- Title: {job.title}
- Company: {job.company or 'the target company'}

TARGET PERSON (the one we are reaching out to):
- Name: {target_name}
- Their role: {target_role}

Generate two messages addressed personally to this target:
1. linkedin_message: A short LinkedIn connection-request note (max 300 characters). Address them by first name. Reference their role in a natural way. End with a low-friction question or hook tied to the user's relevant background.
2. cold_email_body: A 3-paragraph cold email the user can send if they get the target's email later. cold_email_subject must be 8 words or fewer.

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent, add, or imply skills, keywords, achievements, metrics, job titles, or any other content not present in the original USER PROFILE.
- Never invent details about the target person beyond the name and role given above.
- Only construct messages based on what already exists."""

    try:
        structured_llm = get_structured_llm(OutreachCampaignResult, temperature=0.7, max_tokens=1024)
        result = structured_llm.invoke(prompt)
        data = result.model_dump()
        connect = (data.get('linkedin_message') or '').strip()[:300]
        follow_up = (data.get('cold_email_body') or '').strip()
        logger.info("Generated per-target outreach for %s -> %s", job.id, target_name)
        return {'connect_message': connect, 'follow_up_message': follow_up}
    except Exception as exc:
        logger.exception("Per-target outreach generation failed: %s", exc)
        return {'connect_message': '', 'follow_up_message': ''}
