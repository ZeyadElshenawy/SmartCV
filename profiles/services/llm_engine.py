import os
import logging
from langchain_groq import ChatGroq

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Central model configuration
# Groq LPU — fastest inference available (~2s responses)
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
LLM_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")


def get_llm(temperature: float = 0.3, max_tokens: int = 4096) -> ChatGroq:
    """
    Returns a raw ChatGroq instance for plain-text generation
    (cover letters, salary scripts, etc.).
    """
    return ChatGroq(
        model=LLM_MODEL,
        api_key=GROQ_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=1,
        timeout=20,
    )


def get_structured_llm(pydantic_schema, temperature: float = 0.1, max_tokens: int = 8000):
    """
    Returns a ChatGroq instance bound to a Pydantic schema via
    `with_structured_output()`.  The output is guaranteed to be a
    valid instance of *pydantic_schema* — no manual JSON parsing needed.
    """
    llm = ChatGroq(
        model=LLM_MODEL,
        api_key=GROQ_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=1,
        timeout=20,
    )
    return llm.with_structured_output(pydantic_schema)


# ---------------------------------------------------------------------------
# Backward-compat shim so files that haven't been migrated yet still work.
# Returns an object that mimics the old client.chat.completions.create() API.
# ---------------------------------------------------------------------------
class _LegacyMessage:
    def __init__(self, content): self.content = content

class _LegacyChoice:
    def __init__(self, content): self.message = _LegacyMessage(content)

class _LegacyResponse:
    def __init__(self, content): self.choices = [_LegacyChoice(content)]

class _LegacyChatCompletions:
    def create(self, model, messages, **kwargs):
        kwargs.pop("disable_reasoning", None)
        kwargs.pop("timeout", None)
        kwargs.pop("response_format", None)
        temp = kwargs.pop("temperature", 0.3)
        max_tok = kwargs.pop("max_tokens", 4096)
        llm = get_llm(temperature=temp, max_tokens=max_tok)
        from langchain_core.messages import HumanMessage, SystemMessage
        lc_messages = []
        for m in messages:
            if m["role"] == "system":
                lc_messages.append(SystemMessage(content=m["content"]))
            else:
                lc_messages.append(HumanMessage(content=m["content"]))
        result = llm.invoke(lc_messages)
        return _LegacyResponse(result.content)

class _LegacyChat:
    def __init__(self): self.completions = _LegacyChatCompletions()

class _LegacyClient:
    def __init__(self): self.chat = _LegacyChat()

def get_llm_client():
    """Backward-compatible shim. New code should use get_llm() or get_structured_llm()."""
    return _LegacyClient()
