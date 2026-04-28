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
1. `importance`: a brief explanation (1-2 sentences) of why it matters in the current market.
2. `resources`: 2-3 real, verifiable resources. EACH resource is an object with these keys:
   - `name`: the course / book / tutorial title (e.g., "MIT 6.006 Intro to Algorithms")
   - `url`: the FULL URL to the resource (e.g., "https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-spring-2020/").
     Provide ONLY URLs you are CERTAIN exist. If you can't recall the exact URL, use the provider's
     base URL (e.g., "https://www.coursera.org/" for a Coursera course) — the user can search from there.
   - `provider`: short label, one of: "Coursera", "Udemy", "edX", "YouTube", "MDN", "Official docs",
     "Book", "freeCodeCamp", "Frontend Masters", "Pluralsight", "Roadmap.sh", "Khan Academy", or
     "Other" if nothing fits.
3. `project_idea`: a practical project they can build (1-2 sentences) to put on their resume.
4. `time_estimate`: rough commitment to learn this — phrasing like "10-15 hours over 2 weeks" or
   "30+ hours, 1 month". Realistic, not aspirational.

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Only generate learning paths for the explicitly provided skills list.
- Resource URLs MUST be plausibly real. If unsure, fall back to the provider's base URL — never
  invent a course slug. A bad URL erodes user trust faster than a missing one.
- Do not invent fake courses, fake YouTube channels, or fake author names."""

    try:
        structured_llm = get_structured_llm(LearningPathResult, temperature=0.3, max_tokens=2048, task="learning_path")
        result = structured_llm.invoke(prompt)
        
        learning_path = [item.model_dump() for item in result.items]
        logger.info(f"Generated learning path for {len(skills_list)} skills")
        return learning_path
        
    except Exception as e:
        logger.exception(f"Learning path generation failed: {e}")
        return []
