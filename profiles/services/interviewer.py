import logging
import json
import re
import os
import difflib
from typing import Dict, Any, Tuple, Optional, List
from django.conf import settings
from django.core.cache import cache
from profiles.models import UserProfile
from jobs.models import Job

logger = logging.getLogger(__name__)

# Conversation state — cached per (user, job) pair. Uses Django's cache
# backend so state survives process restarts and works across workers.
# Expires after 24h of inactivity.
_STATE_TTL_SECONDS = 60 * 60 * 24


def _state_key(user_id: int, job_id: str) -> str:
    return f"smartcv:chatbot_state:{user_id}:{job_id}"


def _load_state(user_id: int, job_id: str) -> Optional[Dict[str, Any]]:
    return cache.get(_state_key(user_id, job_id))


def _save_state(user_id: int, job_id: str, state: Dict[str, Any]) -> None:
    cache.set(_state_key(user_id, job_id), state, _STATE_TTL_SECONDS)


def _clear_state(user_id: int, job_id: str) -> None:
    cache.delete(_state_key(user_id, job_id))

# Standardized proficiency levels
VALID_PROFICIENCIES = ['Beginner', 'Intermediate', 'Advanced', 'Expert']

def _normalize_proficiency(raw: str) -> str:
    """Normalize freeform proficiency text to a standard level."""
    if not raw:
        return 'Intermediate'
    low = raw.lower().strip()
    # Direct match
    for level in VALID_PROFICIENCIES:
        if level.lower() == low:
            return level
    # Fuzzy / typo handling
    beginner_words = {'beginer', 'biggener', 'beginner', 'begginer', 'novice', 'basic', 'starter', 'entry', 'new', 'learning', 'familiar'}
    intermediate_words = {'intermediate', 'mid', 'moderate', 'average', 'decent', 'competent', 'working'}
    advanced_words = {'advanced', 'senior', 'strong', 'proficient', 'experienced', 'solid'}
    expert_words = {'expert', 'master', 'guru', 'specialist', 'authority'}
    
    for word in beginner_words:
        if word in low:
            return 'Beginner'
    for word in advanced_words:
        if word in low:
            return 'Advanced'
    for word in expert_words:
        if word in low:
            return 'Expert'
    for word in intermediate_words:
        if word in low:
            return 'Intermediate'
    
    return 'Intermediate'  # safe default


def _normalize_skill_name(name: str) -> str:
    """Capitalize skill names consistently."""
    if not name:
        return name
    # Known canonical forms
    CANONICAL = {
        'pyspark': 'PySpark', 'pytorch': 'PyTorch', 'tensorflow': 'TensorFlow',
        'javascript': 'JavaScript', 'typescript': 'TypeScript', 'postgresql': 'PostgreSQL',
        'mysql': 'MySQL', 'mongodb': 'MongoDB', 'graphql': 'GraphQL',
        'nodejs': 'Node.js', 'node.js': 'Node.js', 'reactjs': 'React',
        'vuejs': 'Vue.js', 'vue.js': 'Vue.js', 'nextjs': 'Next.js',
        'css': 'CSS', 'html': 'HTML', 'sql': 'SQL', 'api': 'API',
        'aws': 'AWS', 'gcp': 'GCP', 'ci/cd': 'CI/CD', 'devops': 'DevOps',
        'numpy': 'NumPy', 'pandas': 'Pandas', 'scikit-learn': 'scikit-learn',
        'keras': 'Keras', 'opencv': 'OpenCV', 'docker': 'Docker',
        'kubernetes': 'Kubernetes', 'git': 'Git', 'linux': 'Linux',
        'power bi': 'Power BI', 'powerbi': 'Power BI',
        'matplotlib': 'Matplotlib', 'seaborn': 'Seaborn',
        'nlp': 'NLP', 'cnn': 'CNN', 'cnns': 'CNNs', 'rnn': 'RNN', 'rnns': 'RNNs',
        'llm': 'LLM', 'langchain': 'LangChain', 'fastapi': 'FastAPI',
        'flask': 'Flask', 'django': 'Django', 'spark': 'Spark',
        'databricks': 'Databricks', 'airflow': 'Airflow',
        'dax': 'DAX', 'excel': 'Excel', 'tableau': 'Tableau',
        'machine learning': 'Machine Learning', 'deep learning': 'Deep Learning',
        'data science': 'Data Science', 'data engineering': 'Data Engineering',
        'computer vision': 'Computer Vision', 'natural language processing': 'NLP',
    }
    low = name.lower().strip()
    if low in CANONICAL:
        return CANONICAL[low]
    # Default: title case
    return name.strip()


def compare_cv_with_job(cv_skills: List, job_skills: List) -> Dict[str, List]:
    """Smart comparison of CV skills with job requirements."""
    cv_skill_names = set()
    for skill in cv_skills:
        if isinstance(skill, dict):
            cv_skill_names.add(skill.get('name', '').lower().strip())
        else:
            cv_skill_names.add(str(skill).lower().strip())
    
    exact_matches = []
    missing = []
    
    for job_skill in job_skills:
        if job_skill.lower().strip() in cv_skill_names:
            exact_matches.append(job_skill)
        else:
            missing.append(job_skill)
    
    return {
        'exact_matches': exact_matches,
        'missing': missing
    }


def _get_contextual_nudge(user_reply: str, skills_to_probe: list, job=None) -> str:
    """Generate a warm, varied nudge when the user gives a minimal answer."""
    import random
    low = (user_reply or '').lower().strip()
    
    if low in {'no', 'nope', 'nah', 'none', 'nothing'}:
        nudges = [
            "No worries! Even a little exposure counts — have you seen it used in a project or a tutorial?",
            "That's totally fine! Let's see if there's something else that's more your wheelhouse.",
            "Got it — not every skill is a fit. Let's explore some others that might be closer to your experience.",
        ]
    elif low in {'yes', 'yeah', 'yep', 'ok'}:
        nudges = [
            "Awesome! Could you tell me a bit more — like what you've built with it or how long you've used it?",
            "Great to hear! Give me a quick example of how you've used it — even something small counts.",
            "Nice! What level would you put yourself at — beginner, intermediate, or pretty experienced?",
        ]
    else:
        nudges = [
            "Could you add a bit more detail? Even one sentence about your experience level would help a lot.",
            "I want to make sure I capture this accurately — could you elaborate just a little?",
            "Tell me a bit more — what's your comfort level with this? Any projects or courses come to mind?",
        ]
    
    return random.choice(nudges)


def process_chat_turn(user_id: int, job_id: str, user_reply: str, conversation_history: List[Dict]) -> Dict[str, Any]:
    """
    Process an entire chat turn in a single LLM call using LangChain structured output.
    """
    try:
        job = Job.objects.get(id=job_id)
        profile = UserProfile.objects.get(user_id=user_id)
    except (Job.DoesNotExist, UserProfile.DoesNotExist):
        return {'error': 'Profile or Job not found'}
        
    state = _load_state(user_id, job_id)
    if state is None:
        comparison = compare_cv_with_job(profile.skills or [], job.extracted_skills or [])
        state = {
            'covered_skills': [],
            'mentioned_skills': [],
            'turn_count': 0,
            'last_question': '',
            'validated_skills': comparison['exact_matches'],
        }
    state['turn_count'] += 1
    
    # Removed manual string intercept code so the LLM manages flow state organically
             
    # Prepare skills to probe — CASE-INSENSITIVE filtering
    comparison = compare_cv_with_job(profile.skills or [], job.extracted_skills or [])
    covered_lower = {s.lower().strip() for s in state['covered_skills']}
    mentioned_lower = {s.lower().strip() for s in state['mentioned_skills']}
    matched_lower = {s.lower().strip() for s in comparison['exact_matches']}
    all_excluded_lower = covered_lower | mentioned_lower | matched_lower
    
    skills_to_probe = [s for s in comparison['missing'] if s.lower().strip() not in all_excluded_lower]
    
    max_turns = 10
    if state['turn_count'] > max_turns or not skills_to_probe:
        # Rich, personalized completion message
        user_name = (profile.data_content or {}).get('full_name', '').split()[0] if (profile.data_content or {}).get('full_name') else ''
        skill_count = len(profile.skills or [])
        if not skills_to_probe:
            completion_msg = f"{'Great work, ' + user_name + '! ' if user_name else 'Great work! '}Your profile now covers all the key skills for **{job.title}**. You have **{skill_count} skills** on record — that's a strong foundation. Let's move on to generating your tailored resume!"
        else:
            completion_msg = f"{'Thanks, ' + user_name + '! ' if user_name else 'Thanks! '}We've covered a lot of ground. Your profile is looking solid with **{skill_count} skills** for the **{job.title}** role. Ready to put it all together!"
        _save_state(user_id, job_id, state)
        return {
             'needs_clarification': False,
             'extracted_skills': [],
             'profile_updated': False,
             'next_question': completion_msg,
             'next_topic': 'completion',
             'is_complete': True
        }

    try:
        from .llm_engine import get_structured_llm
        from .schemas import ChatTurnResult

        # Context for prompt
        current_skills = profile.skills or []
        data_content = profile.data_content or {}
        user_name = data_content.get('full_name', '').split()[0] if data_content.get('full_name') else 'there'
        
        skills_in_cv = ', '.join(comparison['exact_matches'])
        skills_covered = ', '.join(state['covered_skills'])
        skills_missing = ', '.join(skills_to_probe[:5])
        
        # Build rich user context from profile data
        experiences_summary = ''
        raw_experiences = data_content.get('experiences', [])
        if raw_experiences:
            exp_lines = []
            for exp in raw_experiences[:3]:
                title = exp.get('title', exp.get('role', ''))
                company = exp.get('company', '')
                highlights = exp.get('highlights', [])[:2]
                line = f"  - {title} at {company}"
                if highlights:
                    line += f" (highlights: {'; '.join(highlights)})"
                exp_lines.append(line)
            experiences_summary = '\n'.join(exp_lines)
        
        education_summary = ''
        raw_education = data_content.get('education', [])
        if raw_education:
            edu_lines = [f"  - {e.get('degree', '')} from {e.get('institution', '')}" for e in raw_education[:2]]
            education_summary = '\n'.join(edu_lines)
        
        projects_summary = ''
        raw_projects = data_content.get('projects', [])
        if raw_projects:
            proj_lines = [f"  - {p.get('name', p.get('title', ''))}" for p in raw_projects[:3]]
            projects_summary = '\n'.join(proj_lines)
        
        history_text = "\n".join([
            f"{msg['role'].capitalize()}: {msg['content']}"
            for msg in (conversation_history[-6:] if conversation_history else [])
        ])

        system_prompt = f"""You are **SmartCV Career Agent** — a warm, perceptive career coach having a genuine conversation with {user_name}.

== YOUR VOICE ==
- You sound like a real human mentor, not a chatbot. Each message MUST feel structurally DIFFERENT from the last.
- NEVER start two messages the same way. Vary your openings — use the user's name sometimes, start with an observation sometimes, start with a reaction sometimes.
- NEVER use the pattern "You've worked with X. Have you also explored Y?" more than once in a conversation. If you already used it, try completely different phrasings like:
  * "That connects nicely to [skill] — any experience there?"
  * "Speaking of [related topic], how comfortable are you with [skill]?"
  * "One thing that often pairs with [previous skill] is [new skill]. Have you gotten your hands on that?"
  * "I'm curious about [skill] — does it come up in your day-to-day work?"
  * A casual observation + question combo
- Keep messages 2-4 sentences. Sound like you're genuinely thinking about their career, not reading from a checklist.

== CRITICAL: HANDLING SHORT ANSWERS ==
When the user says just "yes" or "yes I have" or "yeah":
- Do NOT immediately move to the next skill. This is your chance to learn more!
- Ask a follow-up about DEPTH: "Nice — what have you used it for?" or "Cool, at what scale?" or "How long have you been working with it?"
- Only move to a new skill AFTER you've gotten at least one substantive detail about the current one.

When the user says "no" or "not really":
- Don't dwell. Briefly acknowledge ("No worries!") and pivot to the next skill with energy.

== CRITICAL: EXTRACT ALL SKILLS MENTIONED ==
If the user mentions ANY technology/tool/skill ANYWHERE in their reply — even buried in a long answer about something else (e.g., mentioning "Git", "DVC", "Docker", "Version Control", "CI/CD") — you MUST extract it in skills_to_add.
Don't just extract the main topic being discussed — scan the ENTIRE reply for every technology name.

== CONTEXT: THE USER ==
Name: {data_content.get('full_name', 'Unknown')}
Target Job: **{job.title}** at **{job.company or 'a company'}**

Skills already on profile ({len(current_skills)}): {skills_in_cv or 'None yet'}
{f'Work Experience:\\n{experiences_summary}' if experiences_summary else 'Work Experience: Not provided yet'}
{f'Education:\\n{education_summary}' if education_summary else ''}
{f'Projects:\\n{projects_summary}' if projects_summary else ''}

== SKILL GAP STATUS ==
Skills the job requires that are MISSING from their profile: {skills_missing}
Skills already discussed (DO NOT ask about these again): {skills_covered or 'None yet — this is the opening message'}

== CONVERSATION SO FAR ==
{history_text or '(This is the start of the conversation)'}

User just said: "{user_reply or '(No reply yet — generate your opening message)'}"

== INSTRUCTIONS ==
1. ANALYZE the user's reply:
   - Extract EVERY skill/technology/tool mentioned (Git, Docker, DVC, CI/CD, Version Control, etc.) — don't miss any
   - Accept all levels of experience — even "I took a course" or "I've seen it" counts
   - Set is_valid=true for ANY clear user intent, including "No", "I don't know", or explicitly wanting to SKIP. ONLY set is_valid=false if the input is absolute gibberish.
   - EXPERIENCE BULLET EXTRACTION (Action vs Exposure Threshold): If (and ONLY if) the user describes a quantifiable or hands-on achievement (e.g. "I used X to build Y"), extract 1 formal STAR-method resume bullet point into 'new_experience_bullets'. If they just say "Yes I used it" or "I saw a tutorial", DO NOT extract a bullet.

2. Generate your next message (the "question" field):
   - OPENING MESSAGE (no user reply yet): Greet by name. Mention one specific thing from their profile ("I noticed your work on [project name]" or "Your experience at [company] caught my eye"). Then ask about the first missing skill, connecting it to their background.
   - AFTER A SHORT "YES": Don't move on yet! Ask for a specific detail — how they used it, what project, what level.
   - AFTER A DETAILED ANSWER: React to something SPECIFIC they said (not generic "that's great"). Then transition naturally to the next missing skill.
   - AFTER A SKIP/NO: Quick empathetic pivot to next missing skill.
   - ANTI-REPETITION: If you previously referenced a specific project or job (e.g., "END-TO-END DATA PIPELINE" or "Almansour"), DO NOT use it as the opening hook again. Find a different project, or just ask the question directly.

3. BANNED PHRASES & BEHAVIORS (never do these):
   - "Let's move on to..."
   - "Let's explore that further"
   - "Have you also explored..."
   - "I've noted that..."
   - Repeating the same question format (e.g., "how do you currently handle X in your projects?")
   - Starting with "You've [done X]." for the third+ time
   - "That's great to hear about your experience with..."

4. Use **bold** for skill names.

5. CRITICAL JSON REQUIREMENT: You MUST output your response by calling the provided tool/function with a valid JSON payload matching the schema. DO NOT output conversational text directly."""

        structured_llm = get_structured_llm(ChatTurnResult, temperature=0.5, max_tokens=800)
        data = structured_llm.invoke(system_prompt)
        
        reply_analysis = data.reply_analysis
        next_gen = data.next_question_generation
        
        # Handle invalid reply
        if user_reply and not reply_analysis.is_valid:
             return {
                 'needs_clarification': True,
                 'clarification_prompt': reply_analysis.clarification_prompt or 'Could you elaborate?',
                 'extracted_skills': [],
                 'profile_updated': False,
                 'is_complete': False
             }
             
        # Process skills — with normalization
        skills_to_add = []
        for s in reply_analysis.skills_to_add:
            skill_dict = s.model_dump()
            skill_dict['name'] = _normalize_skill_name(skill_dict.get('name', ''))
            skill_dict['proficiency'] = _normalize_proficiency(skill_dict.get('proficiency', ''))
            if skill_dict.get('years') and not isinstance(skill_dict['years'], (int, float)):
                try:
                    skill_dict['years'] = float(str(skill_dict['years']).replace('+', '').strip())
                except (ValueError, TypeError):
                    skill_dict['years'] = None
            skills_to_add.append(skill_dict)
        
        quality_score = reply_analysis.quality_score
        all_mentioned = reply_analysis.all_technologies_mentioned
        
        if all_mentioned:
            for tech in all_mentioned:
                tech_lower = tech.lower().strip()
                if tech_lower not in covered_lower and tech_lower not in mentioned_lower:
                    state['mentioned_skills'].append(tech)
                    
        # Save skills: lowered threshold from 5 to 2 so "I took a course" answers get saved
        profile_updated = False
        if quality_score >= 2 and skills_to_add:
            current_skill_names = {s.get('name', '').lower() for s in current_skills if isinstance(s, dict)}
            for skill in skills_to_add:
                s_name = skill.get('name', '').strip()
                if s_name and s_name.lower() not in current_skill_names:
                    current_skills.append({
                        'name': s_name,
                        'proficiency': skill.get('proficiency', 'Intermediate'),
                        'years': skill.get('years', None)
                    })
                    profile_updated = True
                    current_skill_names.add(s_name.lower())
                    
            if profile_updated:
                 profile.skills = current_skills

        # Process new experience bullets
        new_bullets = getattr(reply_analysis, 'new_experience_bullets', [])
        if new_bullets:
            import re
            def simplify_name(name):
                return re.sub(r'[^a-z0-9]', '', str(name).lower())
            
            exps = getattr(profile, 'experiences', [])
            projs = getattr(profile, 'projects', [])
            
            for eb in new_bullets:
                target_name = simplify_name(eb.company_or_project_name)
                best_match = None
                
                # Check experiences first
                for exp in exps:
                    co_name = simplify_name(exp.get('company', ''))
                    role_name = simplify_name(exp.get('role', exp.get('title', '')))
                    if (target_name and co_name and (target_name in co_name or co_name in target_name)) or \
                       (target_name and role_name and target_name in role_name):
                        best_match = exp
                        break
                        
                # Check projects if no experience match
                if not best_match:
                    for proj in projs:
                        p_name = simplify_name(proj.get('name', proj.get('title', '')))
                        if target_name and p_name and (target_name in p_name or p_name in target_name):
                            best_match = proj
                            break
                            
                # Fallback to the first active experience if general or unmatched (but don't force it)
                if not best_match and target_name == 'general' and exps:
                    best_match = exps[0]
                    
                if best_match:
                    if 'highlights' not in best_match:
                        best_match['highlights'] = []
                    if eb.bullet_point not in best_match['highlights']:
                        best_match['highlights'].append(eb.bullet_point)
                        profile_updated = True
                        
            if profile_updated:
                 profile.data_content['experiences'] = exps
                 profile.data_content['projects'] = projs
        
        if profile_updated:
             profile.save()
                 
        # Mark the topic as covered so we NEVER ask about it again
        next_topic = next_gen.topic_skill or 'general'
        if next_topic:
            topic_lower = next_topic.lower().strip()
            if topic_lower not in covered_lower:
                state['covered_skills'].append(next_topic)
                covered_lower.add(topic_lower)
        
        # Also mark whatever skill was being discussed (from user's reply context)
        # by checking what the previous question was about
        if user_reply and skills_to_add:
            for s in skills_to_add:
                sn = s.get('name', '').lower().strip()
                if sn and sn not in covered_lower:
                    state['covered_skills'].append(s.get('name', ''))
                    covered_lower.add(sn)

        # Loop detection — if the LLM just repeated the previous question
        # verbatim, force completion rather than trap the user in a loop.
        final_question = next_gen.question or f"I'd love to hear more about your experience, {user_name}. What tools or technologies do you use most often?"
        if state.get('last_question') and final_question.strip() == state['last_question'].strip():
            logger.warning("Chatbot loop detected (identical question repeated) — forcing completion")
            _save_state(user_id, job_id, state)
            return {
                'needs_clarification': False,
                'extracted_skills': skills_to_add,
                'profile_updated': profile_updated,
                'next_question': "We've covered a lot together! Let's put this into a tailored resume.",
                'next_topic': 'completion',
                'is_complete': True,
            }
        state['last_question'] = final_question

        # Persist state for the next turn
        _save_state(user_id, job_id, state)

        return {
            'needs_clarification': False,
            'extracted_skills': skills_to_add,
            'profile_updated': profile_updated,
            'next_question': final_question,
            'next_topic': next_topic,
            'is_complete': False
        }

    except Exception as e:
        logger.exception(f"Chat turn failed: {e}")
        # Differentiate first-turn failure from mid-conversation failure.
        # Referencing user_name here crashed before because it was only defined
        # inside the try block.
        safe_name = ''
        try:
            safe_name = (profile.data_content or {}).get('full_name', '').split()[0]
        except Exception:
            pass
        is_first_turn = not conversation_history
        if is_first_turn:
            opener = (
                f"Hi{' ' + safe_name if safe_name else ''}! I'm your SmartCV Career Agent. "
                f"I've looked through your profile for the **{job.title}** role — "
                "tell me a bit about your recent work experience and what tools you use day-to-day."
            )
            return {
                'needs_clarification': False,
                'extracted_skills': [],
                'profile_updated': False,
                'next_question': opener,
                'next_topic': 'general',
                'is_complete': False,
                'error_kind': 'soft_failure_first_turn',
            }
        # Mid-conversation failure — signal it clearly so the client can show retry.
        return {
            'needs_clarification': False,
            'extracted_skills': [],
            'profile_updated': False,
            'error': 'Our AI hit a snag. Please try sending your message again.',
            'recoverable': True,
            'is_complete': False,
        }


def reset_conversation_state(user_id: int, job_id: str):
    """Clear conversation state."""
    _clear_state(user_id, job_id)
