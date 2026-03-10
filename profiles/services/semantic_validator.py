"""
Semantic validation for chatbot answers using LLM.
Determines if user's answer makes sense for the question asked.
"""
import logging
import json
from typing import Tuple
from .llm_engine import get_llm_client

logger = logging.getLogger(__name__)


def validate_answer_semantically(question: str, user_answer: str, topic: str = "") -> Tuple[bool, str]:
    """
    LLM-powered semantic validation: Does the answer make sense for the question?
   
    Args:
        question: The question asked
        user_answer: User's response
        topic: Optional context about what was being discussed
       
    Returns:
        (makes_sense: bool, clarification_message: str)
        - If makes_sense=True, clarification_message is empty
        - If makes_sense=False, clarification_message contains natural follow-up question
    """
    client = get_llm_client()
    if not client:
        return True, ""  # Accept if LLM unavailable
    
    prompt = f"""You are a professional interviewer evaluating a candidate's response.

Question Asked: "{question}"
Topic/Context: {topic if topic else "general professional experience"}
Candidate's Answer: "{user_answer}"

Task: Determine if the answer makes sense and is relevant to the question.

VALID answers (return makes_sense=true):
- Directly addresses the question
- Somewhat vague but on-topic  
- "I don't have experience" - VALID (honestly answers)
- Mentions relevant technologies/experience
- Even brief if it's relevant

INVALID answers (return makes_sense=false, need clarification):
- Completely off-topic (asked Python, talks about pizza)
- Random/nonsensical text
- Just greetings or jokes, no substance
- Gibberish or unrelated response

Return strict JSON:
{{
  "makes_sense": true or false,
  "clarification_question": "friendly, natural follow-up question if answer doesn't make sense"
}}

Be LENIENT - most answers should pass unless clearly nonsensical."""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=120,
            timeout=15,
        )
        
        content = response.choices[0].message.content
        
        if not content:
             return True, ""

        # Extract JSON
        start = content.find('{')
        end = content.rfind('}')
        
        if start != -1 and end != -1:
            try:
                result = json.loads(content[start:end+1])
                makes_sense = result.get('makes_sense', True)
                clarification = result.get('clarification_question', '')
                
                if not makes_sense and clarification:
                    logger.info(f"Semantic validation failed. Asking: {clarification[:50]}...")
                    return False, clarification
                
                return True, ""
                
            except json.JSONDecodeError:
                logger.warning("Failed to parse validation JSON")
                return True, ""
        
        return True, ""
            
    except Exception as e:
        logger.warning(f"LLM semantic validation error: {e}")
        return True, ""  # Accept on error - don't block user
