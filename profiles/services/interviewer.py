import logging
import json
import re
import os
from typing import Dict, Any, Tuple, Optional, List
from django.conf import settings
from profiles.models import UserProfile
from jobs.models import Job
from google import genai

logger = logging.getLogger(__name__)

# Conversation state tracking
_conversation_state = {}

# Dynamic question templates for variety
QUESTION_TEMPLATES = [
    "Tell me about your experience with {skill}.",
    "Have you worked with {skill}? If so, in what capacity?",
    "I see this role needs {skill}. How have you used it in past projects?",
    "What's your comfort level with {skill}?",
    "Can you describe a project where you used {skill}?",
    "How much experience do you have with {skill}?",
    "Walk me through your {skill} background.",
    "This position requires {skill} - do you have that experience?",
    "{skill} is a key requirement. What have you built with it?",
    "Share your experience working with {skill}.",
]

def compare_cv_with_job(cv_skills: List, job_skills: List) -> Dict[str, List]:
    """Smart comparison of CV skills with job requirements."""
    cv_skill_names = set()
    for skill in cv_skills:
        if isinstance(skill, dict):
            cv_skill_names.add(skill.get('name', '').lower().strip())
        else:
            cv_skill_names.add(str(skill).lower().strip())
    
    job_skill_names = {s.lower().strip() for s in job_skills}
    
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


def extract_mentioned_skills(text: str, potential_skills: List[str]) -> List[str]:
    """
    Extract skills mentioned in user's response.
    Returns list of skills found in the text.
    """
    text_lower = text.lower()
    mentioned = []

    for skill in potential_skills:
        skill_lower = skill.lower()
        # Check for whole word matches
        if re.search(r'\b' + re.escape(skill_lower) + r'\b', text_lower):
            mentioned.append(skill)

    return mentioned


def validate_response_quality(response: str) -> Tuple[bool, str]:
    """Validate if response is substantive and appropriate."""
    response = response.strip()
    
    # 1. Basic Length/Content Checks
    if len(response) < 3:
        return False, "That's too brief. Can you please provide more details?"
    
    if len(response.replace(' ', '').replace('.', '').replace(',', '')) < 5:
        return False, "Please provide a meaningful response."
        
    non_answers = {'no', 'nope', 'nah', 'idk', 'dont know', "don't know", 
                  'nothing', 'none', 'haha', 'lol', 'gg', 'ok', 'yes', 'yeah'}
    if response.lower() in non_answers:
        return False, "I need a bit more detail. Can you elaborate?"
    
    # 2. LLM Guardrail (Pre-flight)
    try:
        from .llm_engine import get_llm_client
        client = get_llm_client()
        
        if client:
            guard_prompt = f"""Analyze this interview response.
            Response: "{response}"
            
            Check for:
            1. Gibberish (e.g. "asdf", "jkl", "akjDJLK")
            2. Profanity/Abuse
            3. Dismissive/Non-answers
            Is this response:
            1. Valid english (or reasonable text)?
            2. Meaningful?
            3. NOT nonsense/spam (like "asdf" or "jkl")?
            
            Return ONLY a valid JSON object: {{ "valid": boolean, "reason": "short explanation" }}
            """
            
            res = client.chat.completions.create(
                model=LLM_MODEL, 
                messages=[{"role": "user", "content": guard_prompt}],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            content = res.choices[0].message.content
            data = json.loads(content)
            if not data.get('valid'):
                return False, f"I didn't quite catch that. {data.get('reason', 'Could you clarify?')}"
    except Exception as e:
        logger.warning(f"Guardrail check failed: {e}")
        pass
        
    no_experience_patterns = [
        "don't have", "do not have", "no experience", 
        "never worked", "never used", "not familiar"
    ]
    if any(pattern in response.lower() for pattern in no_experience_patterns):
        return True, "understood_as_no_experience"
    
    return True, "valid"


def process_chat_turn(user_id: int, job_id: str, user_reply: str, conversation_history: List[Dict]) -> Dict[str, Any]:
    """
    Process an entire chat turn in a single LLM call:
    1. Validates the user's reply
    2. Extracts skills
    3. Generates the next question
    This turns 5 sequential LLM calls into 1.
    """
    try:
        job = Job.objects.get(id=job_id)
        profile = UserProfile.objects.get(user_id=user_id)
    except (Job.DoesNotExist, UserProfile.DoesNotExist):
        return {'error': 'Profile or Job not found'}
        
    state_key = f"{user_id}_{job_id}"
    if state_key not in _conversation_state:
        comparison = compare_cv_with_job(profile.skills or [], job.extracted_skills or [])
        _conversation_state[state_key] = {
            'covered_skills': [],
            'mentioned_skills': [],
            'turn_count': 0,
            'validated_skills': comparison['exact_matches']
        }
    
    state = _conversation_state[state_key]
    state['turn_count'] += 1
    
    # 1. Quick pre-checks
    if user_reply:
         user_reply = user_reply.strip()
         if len(user_reply) < 3 or user_reply.lower() in {'no', 'nope', 'nah', 'idk', "don't know", 'nothing', 'none', 'haha', 'lol', 'ok', 'yes', 'yeah'}:
             return {
                 'needs_clarification': True,
                 'clarification_prompt': 'Could you please elaborate on that?',
                 'extracted_skills': [],
                 'profile_updated': False,
                 'is_complete': False
             }
             
    # Prepare skills to probe
    comparison = compare_cv_with_job(profile.skills or [], job.extracted_skills or [])
    all_excluded = set(state['covered_skills'] + state['mentioned_skills'] + comparison['exact_matches'])
    skills_to_probe = [s for s in comparison['missing'] if s not in all_excluded]
    
    max_turns = 10
    if state['turn_count'] > max_turns or not skills_to_probe:
        completion_msg = "Excellent! You have all the key skills for this role." if not skills_to_probe else "Thanks for sharing! Your profile looks good!"
        # If there's no reply (initial turn), we still need to ask the first question, handled below, 
        # but if we hit max turns, just complete.
        if state['turn_count'] > max_turns or not skills_to_probe:
             return {
                  'needs_clarification': False,
                  'extracted_skills': [],
                  'profile_updated': False,
                  'next_question': completion_msg,
                  'next_topic': 'completion',
                  'is_complete': True
             }

    try:
        from .llm_engine import get_llm_client, LLM_MODEL
        client = get_llm_client()
        if not client:
             raise ValueError("No LLM Client")

        # Context for prompt
        current_skills = profile.skills or []
        skills_in_cv = ', '.join(comparison['exact_matches'])
        skills_mentioned = ', '.join(state['mentioned_skills'])
        skills_missing = ', '.join(skills_to_probe[:5])
        
        history_text = "\\n".join([
            f"{msg['role'].capitalize()}: {msg['content']}"
            for msg in (conversation_history[-6:] if conversation_history else [])
        ])

        system_prompt = f"""You are an expert technical interviewer processing a conversation turn.
Job: {job.title} at {job.company}
Required Skills: {', '.join(job.extracted_skills or [])}

Current Profile Skills: {json.dumps(current_skills, default=str)}
Skills already covered: {skills_in_cv}, {skills_mentioned}
Skills to ask about next: {skills_missing}

Recent Conversation:
{history_text}

User just replied: "{user_reply}"

Your task is to respond with a JSON object that does TWO things:
1. Analzye the user's reply (if any). Extract skills, evaluate quality. Is it a valid answer to the previous question?
2. Generate the NEXT conversational question to ask the user, picking ONE skill from the "Skills to ask about next" list.

JSON format required:
{{
  "reply_analysis": {{
     "is_valid": true/false,
     "quality_score": 0-10, // 0 if vague/non-sense
     "clarification_prompt": "Ask for more details if is_valid is false",
     "skills_to_add": [
        {{"name": "Skill", "proficiency": "Intermediate", "years": "2"}}
     ],
     "all_technologies_mentioned": ["tech"]
  }},
  "next_question_generation": {{
     "question": "Your next conversational question here, max 30 words.",
     "topic_skill": "The skill you are asking about"
  }}
}}
"""

        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": system_prompt}],
            response_format={"type": "json_object"}
        )
        
        data = json.loads(response.choices[0].message.content)
        
        reply_analysis = data.get('reply_analysis', {})
        next_gen = data.get('next_question_generation', {})
        
        # Handle invalid reply
        if user_reply and not reply_analysis.get('is_valid', True):
             return {
                 'needs_clarification': True,
                 'clarification_prompt': reply_analysis.get('clarification_prompt', 'Could you elaborate?'),
                 'extracted_skills': [],
                 'profile_updated': False,
                 'is_complete': False
             }
             
        # Process skills
        skills_to_add = reply_analysis.get('skills_to_add', [])
        quality_score = reply_analysis.get('quality_score', 0)
        all_mentioned = reply_analysis.get('all_technologies_mentioned', [])
        
        if all_mentioned:
            for tech in all_mentioned:
                if tech not in state['mentioned_skills'] and tech not in state['covered_skills']:
                    state['mentioned_skills'].append(tech)
                    
        profile_updated = False
        if quality_score >= 5 and skills_to_add:
            current_skill_names = {s.get('name', '').lower() for s in current_skills if isinstance(s, dict)}
            for skill in skills_to_add:
                s_name = skill.get('name', '').strip()
                if s_name and s_name.lower() not in current_skill_names:
                    current_skills.append({
                        'name': s_name,
                        'proficiency': skill.get('proficiency', 'Intermediate'),
                        'years': skill.get('years', '')
                    })
                    profile_updated = True
                    
            if profile_updated:
                 profile.skills = current_skills
                 profile.save()
                 
        next_topic = next_gen.get('topic_skill', 'general')
        if next_topic and next_topic not in state['covered_skills']:
             state['covered_skills'].append(next_topic)
             
        return {
            'needs_clarification': False,
            'extracted_skills': skills_to_add,
            'profile_updated': profile_updated,
            'next_question': next_gen.get('question', "What else can you tell me about your background?"),
            'next_topic': next_topic,
            'is_complete': False
        }

    except Exception as e:
        logger.exception(f"Chat turn failed: {e}")
        return {
            'needs_clarification': False,
            'extracted_skills': [],
            'profile_updated': False,
            'next_question': "Tell me about your background.",
            'next_topic': "general",
            'is_complete': False
        }


def reset_conversation_state(user_id: int, job_id: str):
    """Clear conversation state."""
    state_key = f"{user_id}_{job_id}"
    if state_key in _conversation_state:
        del _conversation_state[state_key]
