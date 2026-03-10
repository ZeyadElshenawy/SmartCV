import os
import logging
import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Central model configuration — change this one line to swap models globally
# Uses the local Ollama instance (OpenAI-compatible endpoint)
# llama3.1:8b running on a local RTX 4070 is extremely fast and free
# ---------------------------------------------------------------------------
LLM_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")


def get_llm_client() -> OpenAI | None:
    """
    Returns an OpenAI-compatible client pointed at the local Ollama API.
    A 45-second timeout is set to prevent requests hanging indefinitely.
    """
    try:
        # Ollama doesn't require an API key, but the OpenAI client needs a dummy string
        client = OpenAI(
            api_key="ollama",
            base_url=OLLAMA_BASE_URL,
            http_client=httpx.Client(timeout=45.0),
        )
        return client
    except Exception as e:
        logger.error("Failed to initialize Ollama LLM client: %s", e)
        return None
