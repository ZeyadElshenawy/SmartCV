"""Sentence-transformer embeddings for the RAG knowledge base.

Revived for the `feat/rag-knowledge-base` work — `all-MiniLM-L6-v2` produces
384-dim vectors, matching the dormant `VectorField(dimensions=384)` fields on
UserProfile/Job and the new `KnowledgeChunk.embedding` field.

Sync API. The model is loaded lazily on first call (~3s warm-up, cached in
process memory thereafter). Both the indexer (batch) and retriever (single
query) hit the same singleton.

`get_embedding()` and `generate_vector_input()` are kept as legacy stubs so
nothing in the gap-analysis path breaks.
"""
import logging

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_model = None


def _get_model():
    """Lazily load and cache the sentence-transformer model in-process."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s (first call; ~3s)", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    """Embed a single string into a 384-dim vector (L2-normalized).

    Used by `knowledge_retriever.retrieve_chunks` at query time.
    """
    model = _get_model()
    vec = model.encode(text or "", normalize_embeddings=True)
    return vec.tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings. Used by the indexer to embed all KB files at once."""
    if not texts:
        return []
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vecs]


# ---------------------------------------------------------------------------
# Legacy stubs — kept so existing callers in the gap-analysis path continue
# to import without error. These do NOT use the embedding model; profile-level
# similarity is handled by the LLM in gap_analyzer.py.
# ---------------------------------------------------------------------------

def get_embedding(text):
    """Stub — always returns None. Profile embeddings are not in use."""
    return None


def generate_vector_input(profile_data: dict) -> str:
    """Build a plain-text summary of the profile for LLM context.

    No longer used for vector embeddings, but kept for any code that needs a
    textual representation of the user's profile.
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
