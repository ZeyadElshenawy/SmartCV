import json
import logging

from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import LearningPathResult

logger = logging.getLogger(__name__)


def _extract_failed_generation_items(exc) -> list[dict] | None:
    """Recover learning-path items from Groq's `tool_use_failed` payload.

    The model sometimes emits a bare list of LearningPathItem objects
    instead of the `{items: [...]}` wrapper the schema expects. Groq then
    rejects the call as `tool_use_failed`, but the well-formed JSON list
    is still in `error.failed_generation`. Salvage it instead of burning
    a second call on retry.

    Returns a list of dicts when the failed_generation parses as a list of
    items (or as the wrapper object), else None.
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
    # Bare list of items (the common failure mode here):
    if isinstance(parsed, list):
        return [p for p in parsed if isinstance(p, dict)]
    # Wrapped form, in case the model got it right but tool-use serializer flaked:
    if isinstance(parsed, dict) and isinstance(parsed.get('items'), list):
        return [p for p in parsed['items'] if isinstance(p, dict)]
    return None

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

    structured_llm = get_structured_llm(
        LearningPathResult, temperature=0.3, max_tokens=2048, task="learning_path"
    )
    try:
        result = structured_llm.invoke(prompt)
        learning_path = [item.model_dump() for item in result.items]
        logger.info(f"Generated learning path for {len(skills_list)} skills")
        return learning_path
    except Exception as e:
        # Groq raises BadRequestError(tool_use_failed) when the model emits a
        # bare top-level list instead of the {items: [...]} wrapper. The
        # actual content is well-formed JSON in `error.failed_generation` —
        # salvage it rather than dropping a successful generation.
        recovered = _extract_failed_generation_items(e)
        if recovered:
            try:
                from profiles.services.schemas import LearningPathItem
                items = [LearningPathItem(**item).model_dump() for item in recovered]
                logger.info(
                    "Learning path recovered from failed_generation (%d items)",
                    len(items),
                )
                return items
            except Exception:
                logger.exception("Recovered failed_generation didn't validate")
        logger.exception(f"Learning path generation failed: {e}")
        return []
