import json
import logging
import re

from profiles.services.llm_engine import get_llm, get_structured_llm
from profiles.services.prompt_guards import HUMAN_VOICE_RULE
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


def _extract_failed_generation_payload(exc) -> dict | None:
    """Groq returns tool_use_failed with the raw model output in
    `error.failed_generation`. The model sometimes wraps the tool call in a
    list: `[{"name": "...", "parameters": {...}}]`. Try to recover the
    parameters dict so one good call isn't wasted on a 400.
    """
    body = getattr(exc, 'body', None) or {}
    err = body.get('error', {}) if isinstance(body, dict) else {}
    raw = err.get('failed_generation')
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]
    if isinstance(parsed, dict) and isinstance(parsed.get('parameters'), dict):
        return parsed['parameters']
    if isinstance(parsed, dict):
        return parsed
    return None


_JSON_FENCE_RE = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.DOTALL)


def _parse_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of a model's plain-text reply."""
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1)
    else:
        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end == -1 or end < start:
            return None
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def _fallback_plaintext_json(prompt: str) -> dict | None:
    """Retry with plain-text output and strict JSON instructions — bypasses
    Groq's tool-use serializer, which is the source of the wrapping bug.
    """
    json_prompt = (
        prompt
        + "\n\nReturn ONLY a single JSON object with the exact keys: "
        '"linkedin_message" (string, ≤300 chars), '
        '"cold_email_subject" (string, ≤8 words), '
        '"cold_email_body" (string). '
        "No prose, no markdown, no arrays — just the JSON object."
    )
    try:
        llm = get_llm(temperature=0.7, max_tokens=1024, task="outreach")
        reply = llm.invoke(json_prompt)
        text = getattr(reply, 'content', None) or str(reply)
        return _parse_json_object(text)
    except Exception:
        logger.exception("Outreach JSON fallback failed")
        return None


def _invoke_with_fallback(prompt: str) -> dict:
    """Run the structured call; on Groq tool-use failure, try to recover the
    payload from `failed_generation`, then fall back to a plain-text JSON call.
    Returns a dict matching OutreachCampaignResult fields (possibly empty).
    """
    try:
        structured_llm = get_structured_llm(OutreachCampaignResult, temperature=0.7, max_tokens=1024, task="outreach")
        result = structured_llm.invoke(prompt)
        return result.model_dump()
    except Exception as exc:
        logger.warning("Structured outreach call failed, attempting recovery: %s", exc)
        recovered = _extract_failed_generation_payload(exc)
        if recovered:
            logger.info("Recovered outreach payload from failed_generation")
            return recovered
        fallback = _fallback_plaintext_json(prompt)
        if fallback:
            logger.info("Recovered outreach payload via plaintext JSON fallback")
            return fallback
        raise


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
- Only construct messages based on what already exists.

{HUMAN_VOICE_RULE}"""

    try:
        campaign = _invoke_with_fallback(prompt)
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
- Only construct messages based on what already exists.

{HUMAN_VOICE_RULE}"""

    try:
        data = _invoke_with_fallback(prompt)
        connect = (data.get('linkedin_message') or '').strip()[:300]
        follow_up = (data.get('cold_email_body') or '').strip()
        logger.info("Generated per-target outreach for %s -> %s", job.id, target_name)
        return {'connect_message': connect, 'follow_up_message': follow_up}
    except Exception as exc:
        logger.exception("Per-target outreach generation failed: %s", exc)
        return {'connect_message': '', 'follow_up_message': ''}
