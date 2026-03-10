import os
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    from huggingface_hub import InferenceClient
except ImportError:
    InferenceClient = None

# HuggingFace Inference Client for feature-extraction (embeddings)
# This uses the huggingface_hub InferenceClient which handles the correct routing
EMBEDDING_MODEL = os.getenv("HF_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")


def get_embedding(text: str) -> Optional[List[float]]:
    """
    Generate a text embedding via the HuggingFace InferenceClient.
    Falls back to None gracefully if unavailable — the gap analyzer
    then relies on LLM skill comparison (which is the primary signal anyway).
    """
    if not text or not text.strip():
        return None

    api_key = os.getenv("HF_API_KEY")
    if not api_key:
        logger.warning("HF_API_KEY not set — embeddings disabled, using LLM-only gap analysis.")
        return None

    if InferenceClient is None:
        logger.warning("huggingface_hub not installed — embeddings disabled. Run: pip install huggingface_hub")
        return None

    try:
        client = InferenceClient(token=api_key)
        result = client.feature_extraction(text[:2000], model=EMBEDDING_MODEL)
        # result is a numpy array or list — flatten if needed
        if hasattr(result, 'tolist'):
            flat = result.tolist()
        else:
            flat = list(result)

        # Handle 2D output (token-level embeddings) by mean-pooling
        if flat and isinstance(flat[0], list):
            import numpy as np
            flat = list(np.mean(flat, axis=0))

        return [float(v) for v in flat]

    except Exception as e:
        logger.error("Failed to generate embedding: %s", e)
        return None


def generate_vector_input(profile_data: dict) -> str:
    """
    Creates a weighted embedding string for high-quality semantic matching.
    """
    parts = []

    summary = profile_data.get('normalized_summary') or profile_data.get('summary')
    if summary:
        parts.append(f"Summary: {summary}")

    experiences = profile_data.get('experiences', [])
    if experiences:
        latest = experiences[0]
        parts.append(f"Current Role: {latest.get('title', 'Unknown')} at {latest.get('company', '')}")

    skills = profile_data.get('skills', [])
    if skills:
        skill_names = [s.get('name') if isinstance(s, dict) else str(s) for s in skills]
        parts.append(f"Top Skills: {', '.join(skill_names[:15])}")

    achievements = []
    for p in profile_data.get('projects', [])[:3]:
        achievements.append(f"Project: {p.get('name')} - {p.get('description', '')[:100]}")
    for exp in experiences[:3]:
        desc = exp.get('description', '')
        if desc:
            achievements.append(f"Work: {desc[:200]}")
    if achievements:
        parts.append("Achievements: " + " | ".join(achievements))

    education = profile_data.get('education', [])
    if education:
        edu_strs = [f"{e.get('degree')} in {e.get('field')}" for e in education]
        parts.append(f"Education: {', '.join(edu_strs)}")

    return "\n".join(parts)
