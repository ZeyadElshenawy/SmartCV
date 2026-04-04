import logging
from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import LearningPathResult

logger = logging.getLogger(__name__)

def generate_learning_path(skills_list):
    """
    Generate a learning path for a list of missing skills using structured output.
    Returns list of learning path items.
    """
    if not skills_list:
        return []

    prompt = f"""You are an expert technical career coach. I have a user who is repeatedly missing the following skills in the jobs they are applying for:
    
{', '.join(skills_list)}

Please generate a concrete, actionable learning path to help them acquire these skills. 

For each skill, provide:
1. A brief explanation of why it is important in the current market.
2. 1-2 Recommended free/low-cost resources (e.g., specific YouTube channels, Coursera, official documentation).
3. A practical project idea they can build to put it on their resume.

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Only generate learning paths for the explicitly provided skills list.
- Do not invent fake courses or fake YouTube channels. Recommend real, verifiable resources."""

    try:
        structured_llm = get_structured_llm(LearningPathResult, temperature=0.3, max_tokens=2048)
        result = structured_llm.invoke(prompt)
        
        learning_path = [item.model_dump() for item in result.items]
        logger.info(f"Generated learning path for {len(skills_list)} skills")
        return learning_path
        
    except Exception as e:
        logger.exception(f"Learning path generation failed: {e}")
        return []
