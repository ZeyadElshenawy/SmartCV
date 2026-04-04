import os
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_model = None
_model_initialized = False

def get_sentence_transformer():
    global _model, _model_initialized
    if not _model_initialized:
        _model_initialized = True
        try:
            from sentence_transformers import SentenceTransformer
            _model_name = os.getenv("HF_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
            _model = SentenceTransformer(_model_name)
            logger.info(f"Loaded local embedding model: {_model_name}")
        except ImportError:
            _model = None
            logger.warning("sentence_transformers not installed. Embeddings will be disabled.")
        except Exception as e:
            _model = None
            logger.error(f"Error loading embedding model: {e}")
    return _model

def get_embedding(text: str) -> Optional[List[float]]:
    """
    Generate a text embedding via the local SentenceTransformer model.
    This runs entirely on your local machine (using GPU if available), 
    completely avoiding the slow Hugging Face remote API.
    """
    if not text or not text.strip():
        return None

    model = get_sentence_transformer()
    if model is None:
        logger.warning("sentence_transformers not loaded — embeddings disabled.")
        return None

    try:
        # Encode returns a numpy array, we convert to list of floats
        embedding = model.encode(text[:2000], normalize_embeddings=True)
        return [float(v) for v in embedding]

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
