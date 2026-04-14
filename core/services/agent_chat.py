"""Global career agent chat.

The existing profiles.chatbot handles job-specific interview prep. This
service powers a *general* chat — users can ask their agent about career
strategy, evaluating offers, switching tracks, skill priorities, etc. —
without having to pick a specific job first.

The system prompt is assembled from the user's real context (profile +
stage + external signals + recent applications) so the agent answers
grounded in *their* situation, not generic career advice.

Public API:
  - chat(user, history, user_message) → {'reply': str, 'error': Optional[str]}
  - build_system_prompt(user) → the prompt string (exposed for testing)
"""
from __future__ import annotations

import logging
from typing import Optional, TypedDict

logger = logging.getLogger(__name__)


class ChatTurn(TypedDict):
    role: str   # 'user' | 'assistant'
    content: str


class ChatResult(TypedDict):
    reply: str
    error: Optional[str]


def _profile_summary(profile) -> str:
    """Compact text summary of the profile — skills / last role / education."""
    bits = []
    name = getattr(profile, 'full_name', None) or 'the user'
    bits.append(f"Name: {name}")
    location = getattr(profile, 'location', None)
    if location:
        bits.append(f"Location: {location}")

    # Skills (first 15)
    skills = getattr(profile, 'skills', None) or []
    skill_names = []
    for s in skills[:15]:
        if isinstance(s, dict):
            n = s.get('name')
            if n:
                skill_names.append(n)
        elif s:
            skill_names.append(str(s))
    if skill_names:
        bits.append(f"Skills: {', '.join(skill_names)}")

    # Most recent experience
    experiences = getattr(profile, 'experiences', None) or []
    if experiences and isinstance(experiences[0], dict):
        exp = experiences[0]
        title = exp.get('title') or exp.get('position') or ''
        company = exp.get('company') or ''
        if title or company:
            bits.append(f"Most recent role: {title} at {company}".strip())

    # Education
    education = getattr(profile, 'education', None) or []
    if education and isinstance(education[0], dict):
        e = education[0]
        degree = e.get('degree') or ''
        institution = e.get('institution') or ''
        if degree or institution:
            bits.append(f"Education: {degree} · {institution}".strip())

    return "\n".join(f"- {b}" for b in bits) if bits else "- (profile is empty)"


def _signals_summary(profile) -> str:
    """One-line-each summary of connected external signals."""
    data = getattr(profile, 'data_content', None) or {}
    if not isinstance(data, dict):
        return ''

    lines = []

    gh = data.get('github_signals') or {}
    if isinstance(gh, dict) and not gh.get('error') and (gh.get('public_repos') or 0) > 0:
        langs = gh.get('language_breakdown') or []
        top_langs = ", ".join(l[0] for l in langs[:4] if isinstance(l, (list, tuple)))
        lines.append(
            f"GitHub @{gh.get('username')}: {gh.get('public_repos')} repos, "
            f"{gh.get('total_stars') or 0} stars"
            + (f", top languages {top_langs}" if top_langs else "")
        )

    sc = data.get('scholar_signals') or {}
    if isinstance(sc, dict) and not sc.get('error'):
        if (sc.get('total_citations') or 0) > 0 or sc.get('top_publications'):
            lines.append(
                f"Scholar: {sc.get('total_citations') or 0} citations, "
                f"h-index {sc.get('h_index') or 0}"
            )

    kg = data.get('kaggle_signals') or {}
    if isinstance(kg, dict) and not kg.get('error'):
        cats = []
        for k in ('competitions', 'datasets', 'notebooks', 'discussion'):
            c = kg.get(k)
            if isinstance(c, dict) and (c.get('count') or 0) > 0:
                cats.append(f"{k} {c['count']}")
        if cats:
            lines.append(
                f"Kaggle @{kg.get('username')} ({kg.get('overall_tier') or 'Novice'}): "
                + ", ".join(cats)
            )

    return "\n".join(f"- {l}" for l in lines)


def _applications_summary(user) -> str:
    """One line per status bucket: how many jobs the user has in each state."""
    try:
        from jobs.models import Job
    except Exception:
        return ''

    try:
        jobs = list(Job.objects.filter(user=user))
    except Exception:
        return ''
    if not jobs:
        return ''

    counts: dict[str, int] = {}
    for j in jobs:
        counts[j.application_status] = counts.get(j.application_status, 0) + 1

    parts = []
    for status in ('saved', 'applied', 'interviewing', 'offer', 'rejected'):
        if counts.get(status):
            parts.append(f"{counts[status]} {status}")
    return f"- Application pipeline: {', '.join(parts)}" if parts else ''


def build_system_prompt(user) -> str:
    """Assemble the agent's system prompt from the user's real context.

    Exposed for testing — all the context gathering lives here, not inside
    the LLM-call wrapper.
    """
    from profiles.models import UserProfile
    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        profile = None

    if profile is None:
        context_block = "CONTEXT: The user hasn't built a profile yet. Ask what they're working toward and suggest they upload a CV when the moment fits."
    else:
        sections = [f"CANDIDATE PROFILE:\n{_profile_summary(profile)}"]
        signals = _signals_summary(profile)
        if signals:
            sections.append(f"EXTERNAL SIGNALS (use as evidence):\n{signals}")
        apps = _applications_summary(user)
        if apps:
            sections.append(f"APPLICATIONS:\n{apps}")
        context_block = "\n\n".join(sections)

    return f"""You are the SmartCV career agent — a warm, direct, evidence-first career advisor.

You answer questions about this specific person's career: tailoring résumés,
deciding between offers, prepping interviews, switching tracks, prioritizing
skills to learn. You never give generic career advice — you ground every
answer in the concrete context below, and ask short clarifying questions
when you don't have enough to give a sharp answer.

Keep replies concise (2–4 short paragraphs, or a tight list). Avoid buzzwords.
Prefer specific verbs. If the person hasn't given you enough to be useful,
say so and ask for exactly what you need.

{context_block}
"""


def chat(user, history: list[ChatTurn], user_message: str) -> ChatResult:
    """Send the conversation to the LLM and return its reply.

    history should already include previous turns (not the new user_message).
    user_message is the new message to respond to.
    """
    if not (user_message or '').strip():
        return ChatResult(reply='', error='Empty message.')

    try:
        from profiles.services.llm_engine import get_llm
    except Exception as e:
        logger.exception("LLM engine import failed: %s", e)
        return ChatResult(reply='', error='Agent unavailable right now.')

    system_prompt = build_system_prompt(user)

    # Build the LangChain message list.
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    msgs = [SystemMessage(content=system_prompt)]
    for turn in (history or []):
        role = (turn.get('role') or '').lower()
        content = turn.get('content') or ''
        if not content:
            continue
        if role == 'user':
            msgs.append(HumanMessage(content=content))
        elif role == 'assistant':
            msgs.append(AIMessage(content=content))
    msgs.append(HumanMessage(content=user_message))

    try:
        llm = get_llm(temperature=0.6, max_tokens=700)
        response = llm.invoke(msgs)
        text = getattr(response, 'content', None) or str(response)
        return ChatResult(reply=text.strip(), error=None)
    except Exception as e:
        logger.exception("Agent chat LLM call failed: %s", e)
        return ChatResult(reply='', error='Your agent hit a snag. Try again in a moment.')
