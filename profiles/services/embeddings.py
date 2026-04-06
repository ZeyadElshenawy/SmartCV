import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embeddings have been fully removed.
# All similarity scoring is now handled by the LLM in gap_analyzer.py.
# This module is kept as a thin stub so existing imports don't break.
# ---------------------------------------------------------------------------

def get_embedding(text):
    """Stub — always returns None. Embeddings are no longer used."""
    return None


def generate_vector_input(profile_data: dict) -> str:
    """
    Build a plain-text summary of the profile for LLM context.
    No longer used for vector embeddings, but kept for any code that
    needs a textual representation of the user's profile.
    """
    if not profile_data:
        return ""

    parts = []

    summary = profile_data.get('normalized_summary') or profile_data.get('summary')
    if summary:
        parts.append(f"Summary: {summary}")

    experiences = profile_data.get('experiences') or []
    if experiences:
        latest = experiences[0]
        if latest:
            parts.append(f"Current Role: {latest.get('title', 'Unknown')} at {latest.get('company', '')}")

    skills = profile_data.get('skills') or []
    if skills:
        skill_names = [s.get('name') if isinstance(s, dict) else str(s) for s in skills if s]
        parts.append(f"Top Skills: {', '.join(skill_names[:15])}")

    education = profile_data.get('education') or []
    if education:
        edu_strs = [f"{e.get('degree')} in {e.get('field')}" for e in education if e]
        parts.append(f"Education: {', '.join(edu_strs)}")

    return "\n".join(parts)
