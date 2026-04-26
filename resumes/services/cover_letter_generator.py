import logging
from profiles.services.llm_engine import get_llm
from profiles.services.prompt_guards import HUMAN_VOICE_RULE
from langchain_core.messages import HumanMessage

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


def _build_experience_summary(profile) -> str:
    """Build a readable summary of the user's top experiences for the LLM prompt.

    Previously this used str(profile.experiences[:2]), which dumped raw Python
    dict syntax into the prompt — hard for the LLM to parse and wasteful of
    tokens.
    """
    lines = []
    for exp in (profile.experiences or [])[:3]:
        if not isinstance(exp, dict):
            continue
        title = exp.get('title') or exp.get('role') or ''
        company = exp.get('company') or ''
        if not (title or company):
            continue
        header = f"{title} at {company}".strip(' at ')
        lines.append(f"- {header}")
        highlights = exp.get('highlights') or []
        if isinstance(highlights, list):
            for h in highlights[:2]:
                if isinstance(h, str) and h.strip():
                    lines.append(f"  • {h.strip()}")
    if not lines:
        return 'None provided'
    return '\n'.join(lines)


def generate_cover_letter_content(profile, job):
    """
    Generate a highly personalized cover letter using LangChain + Groq.
    """
    data_content = profile.data_content or {}
    # normalized_summary is the canonical field on the profile; 'summary'
    # was a dead reference that always returned ''.
    summary = data_content.get('normalized_summary') or data_content.get('objective') or ''
    experience_block = _build_experience_summary(profile)

    prompt = f"""You are an expert career agent and copywriter. Write a tailored, professional cover letter for the following user and job.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company or 'the company'}
- Description: {job.description[:1500]}

USER PROFILE:
- Name: {profile.full_name}
- Skills: {', '.join(_get_skill_names(profile.skills))}
- Profile summary: {summary or '(not provided — infer from experience below)'}
- Recent experience:
{experience_block}

RULES for the Cover Letter:
1. EXACTLY 3 paragraphs, 220-320 words total.
2. Opening paragraph: State the role applied for AND reference one specific detail from the job description or company. Hook the reader with the single most relevant achievement from the candidate's experience.
3. Body paragraph: Connect up to TWO specific past experiences/skills directly to the problems the job posting describes. Show — don't tell. Concrete examples with outcomes. No skill lists.
4. Closing paragraph: Express genuine enthusiasm for {job.company or 'the team'} specifically (use one detail from the job description). End with a professional call to action.
5. Tone: Confident, professional, clear, concise. No fluff, no generic buzzwords, no "I am writing to express my interest".
6. Do NOT include placeholder addresses or dates at the top. Start directly with "Dear Hiring Manager,".
7. Do NOT close with a signature line or name — the app appends the name separately.

=== LANGUAGE & STYLE RULES ===
- See the HUMAN VOICE block at the end for the full banned-word list and sentence-structure rules.
- Replace these words: Spearheaded -> Led, Leveraged -> Used/Applied, Utilized -> Used, Synergized -> Collaborated, Streamlined -> Simplified/Improved, Robust -> Strong, Demonstrated -> Showed/Proved, Facilitated -> Helped/Enabled.
- Vary sentence length. Avoid starting consecutive sentences with the same word.

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent skills, job titles, company names, certifications, or metrics not present in the candidate profile.
- If the candidate has NO relevant experience for a job requirement, address it honestly (transferable skills) rather than fabricating.
- Do not name specific hiring managers, interviewers, or team members unless the job description names them.

{HUMAN_VOICE_RULE}

Output ONLY the text of the cover letter, nothing else."""

    try:
        llm = get_llm(temperature=0.7, max_tokens=2048, task="cover_letter")
        result = llm.invoke([HumanMessage(content=prompt)])

        content = result.content.strip()
        logger.info(f"Generated cover letter for job {job.id}")
        return content

    except Exception as e:
        logger.exception(f"Cover letter generation failed: {e}")
        return "An error occurred while generating the cover letter. Please try again."
