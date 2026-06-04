import json
import logging
from urllib.parse import quote_plus

from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import LearningPathResult

logger = logging.getLogger(__name__)


# Provider -> search-URL template. The LLM has no web access and cannot
# reliably emit course-specific URLs, so the prompt previously instructed
# it to fall back to the provider's bare homepage when uncertain -- and,
# being uncertain about almost every URL, it consistently emitted
# homepages. We now post-process the LLM's output: for any provider in
# this map with a non-None template, the URL is replaced with a
# deterministic search link built from the resource name. Search pages
# always land somewhere useful (results pre-populated by the title),
# never on a 404 or a bare homepage.
#
# Two cases where the LLM's URL is preserved:
#   * "Official docs" (template is None) -- the LLM typically knows the
#     exact stable doc page (docs.python.org/3/library/..., MDN, React
#     docs, etc.), which is higher-signal than a search page.
#   * Provider not in the map -- no template to apply; keep the LLM's
#     hint as the best available, no crash on unknown labels.
#
# General by design: data-driven, no hardcoded courses, no per-resource
# logic. The keys mirror the provider enum the prompt instructs the LLM
# to use; adding a new provider is one line.
_PROVIDER_SEARCH_TEMPLATES: dict[str, str | None] = {
    "Coursera": "https://www.coursera.org/search?query={q}",
    "Udemy": "https://www.udemy.com/courses/search/?q={q}",
    "edX": "https://www.edx.org/search?q={q}",
    "YouTube": "https://www.youtube.com/results?search_query={q}",
    "MDN": "https://developer.mozilla.org/en-US/search?q={q}",
    "freeCodeCamp": "https://www.freecodecamp.org/news/search/?query={q}",
    "Frontend Masters": "https://frontendmasters.com/search/?q={q}",
    "Pluralsight": "https://www.pluralsight.com/search?q={q}",
    "Roadmap.sh": "https://roadmap.sh/?q={q}",
    "Khan Academy": "https://www.khanacademy.org/search?page_search_query={q}",
    "Book": "https://www.google.com/search?q={q}+book",
    "Other": "https://www.google.com/search?q={q}",
    "Official docs": None,
}


def _apply_search_links(items: list[dict]) -> list[dict]:
    """Replace LLM-emitted URLs with deterministic platform-search links.

    Mutates each resource's ``url`` in place and returns ``items`` for
    convenience. Behaviour per resource:

      * provider in the map with a non-None template -> ``url`` becomes
        the search URL built from the URL-encoded resource name.
      * provider is "Official docs" (template None) -> LLM's ``url`` is
        kept unchanged.
      * provider not in the map -> LLM's ``url`` is kept unchanged.
      * empty / whitespace-only ``name`` -> LLM's ``url`` is kept
        unchanged (a search for the empty string would land on the bare
        search page -- no better than the homepage we are replacing).
    """
    for item in items:
        if not isinstance(item, dict):
            continue
        resources = item.get("resources")
        if not isinstance(resources, list):
            continue
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            name_raw = resource.get("name") or ""
            name = name_raw.strip() if isinstance(name_raw, str) else ""
            if not name:
                continue
            provider = resource.get("provider")
            if provider not in _PROVIDER_SEARCH_TEMPLATES:
                continue
            template = _PROVIDER_SEARCH_TEMPLATES[provider]
            if template is None:
                continue
            resource["url"] = template.format(q=quote_plus(name))
    return items


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
   - `provider`: short label, one of: "Coursera", "Udemy", "edX", "YouTube", "MDN", "Official docs",
     "Book", "freeCodeCamp", "Frontend Masters", "Pluralsight", "Roadmap.sh", "Khan Academy", or
     "Other" if nothing fits.
   - `url`: for "Official docs" (e.g., Python docs, MDN, the React docs), provide the exact stable
     URL — the system preserves it. For ALL OTHER providers, the system constructs a search link
     from `name` + `provider` automatically; any URL you put here is REPLACED, so a placeholder like
     the provider's base URL is fine. Provide the resource NAME accurately — that is what gets
     searched.
3. `project_idea`: a practical project they can build (1-2 sentences) to put on their resume.
4. `time_estimate`: rough commitment to learn this — phrasing like "10-15 hours over 2 weeks" or
   "30+ hours, 1 month". Realistic, not aspirational.

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Only generate learning paths for the explicitly provided skills list.
- Provide the resource NAME and PROVIDER accurately — the system uses both to construct the link.
  A search built from a fabricated course name finds nothing useful.
- Do not invent fake courses, fake YouTube channels, or fake author names."""

    structured_llm = get_structured_llm(
        LearningPathResult, temperature=0.3, max_tokens=2048, task="learning_path"
    )
    try:
        result = structured_llm.invoke(prompt)
        learning_path = [item.model_dump() for item in result.items]
        _apply_search_links(learning_path)
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
                _apply_search_links(items)
                logger.info(
                    "Learning path recovered from failed_generation (%d items)",
                    len(items),
                )
                return items
            except Exception:
                logger.exception("Recovered failed_generation didn't validate")
        logger.exception(f"Learning path generation failed: {e}")
        return []
