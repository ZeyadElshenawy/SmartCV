"""
Semantic validation for chatbot answers using LLM.
Determines if user's answer makes sense for the question asked.
"""
import logging
from typing import Tuple
from .llm_engine import get_structured_llm
from .schemas import SemanticValidationResult

logger = logging.getLogger(__name__)


def validate_answer_semantically(question: str, user_answer: str, topic: str = "") -> Tuple[bool, str]:
    """
    LLM-powered semantic validation using structured output.
    """
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

Be LENIENT - most answers should pass unless clearly nonsensical."""

    try:
        structured_llm = get_structured_llm(SemanticValidationResult, temperature=0.3, max_tokens=120, task="validator")
        result = structured_llm.invoke(prompt)
        
        if not result.makes_sense and result.clarification_question:
            logger.info(f"Semantic validation failed. Asking: {result.clarification_question[:50]}...")
            return False, result.clarification_question
        
        return True, ""
            
    except Exception as e:
        logger.warning(f"LLM semantic validation error: {e}")
        return True, ""  # Accept on error - don't block user
