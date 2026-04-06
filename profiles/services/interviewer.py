import logging
import json
import re
import os
import difflib
from typing import Dict, Any, Tuple, Optional, List
from django.conf import settings
from profiles.models import UserProfile
from jobs.models import Job

logger = logging.getLogger(__name__)

# Conversation state tracking
_conversation_state = {}

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


def process_chat_turn(user_id: int, job_id: str, user_reply: str, conversation_history: List[Dict]) -> Dict[str, Any]:
    """
    Process an entire chat turn in a single LLM call using LangChain structured output.
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
             
    # Prepare skills to probe — CASE-INSENSITIVE filtering
    comparison = compare_cv_with_job(profile.skills or [], job.extracted_skills or [])
    covered_lower = {s.lower().strip() for s in state['covered_skills']}
    mentioned_lower = {s.lower().strip() for s in state['mentioned_skills']}
    matched_lower = {s.lower().strip() for s in comparison['exact_matches']}
    all_excluded_lower = covered_lower | mentioned_lower | matched_lower
    
    skills_to_probe = [s for s in comparison['missing'] if s.lower().strip() not in all_excluded_lower]
    
    max_turns = 10
    if state['turn_count'] > max_turns or not skills_to_probe:
        completion_msg = "Excellent! You have all the key skills for this role." if not skills_to_probe else "Thanks for sharing! Your profile looks good!"
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
        skills_in_cv = ', '.join(comparison['exact_matches'])
        skills_covered = ', '.join(state['covered_skills'])
        skills_missing = ', '.join(skills_to_probe[:5])
        
        history_text = "\n".join([
            f"{msg['role'].capitalize()}: {msg['content']}"
            for msg in (conversation_history[-6:] if conversation_history else [])
        ])

        system_prompt = f"""You are an expert technical interviewer processing a conversation turn.
Job: {job.title} at {job.company}
Required Skills: {', '.join(job.extracted_skills or [])}

Current Profile Skills: {json.dumps(current_skills, default=str)}
Skills already in CV: {skills_in_cv}
Skills already discussed (DO NOT ask about these again): {skills_covered}
Skills to ask about next (pick ONE from this list): {skills_missing}

Recent Conversation:
{history_text}

User just replied: "{user_reply}"

=== RULES ===
1. ANTI-HALLUCINATION: Never invent skills the user didn't mention.
2. ACCEPT ALL VALID ANSWERS: If the user describes ANY level of experience with a skill (even "I took a course" or "I'm a beginner"), that is VALID. Set is_valid=true and quality_score >= 3.
3. EXTRACT ACCURATELY: If the user mentions a skill with a proficiency level (beginner, intermediate, etc.), extract it with that level.
4. MOVE ON: Always pick a DIFFERENT skill from "Skills to ask about next" for the next question. NEVER re-ask about a skill that was already discussed.
5. NORMALIZE: Use proper casing for skill names (e.g., "PySpark" not "pyspark")."""

        structured_llm = get_structured_llm(ChatTurnResult, temperature=0.3, max_tokens=600)
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
              
        return {
            'needs_clarification': False,
            'extracted_skills': skills_to_add,
            'profile_updated': profile_updated,
            'next_question': next_gen.question or "What else can you tell me about your background?",
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
