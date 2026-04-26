import os
import logging
from langchain_groq import ChatGroq

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-task credential resolution.
# Each call site can pass a `task` name so we look up GROQ_API_KEY_<TASK> /
# GROQ_MODEL_<TASK> first and fall back to the global GROQ_API_KEY /
# GROQ_MODEL when unset. Lets us spread requests across multiple Groq
# accounts so per-account rate limits don't bottleneck the whole pipeline.
# ---------------------------------------------------------------------------
DEFAULT_GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEFAULT_GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# Backward-compat alias (some legacy paths still import LLM_MODEL directly)
LLM_MODEL = DEFAULT_GROQ_MODEL


def _resolve_credentials(task):
    """Return (api_key, model) for `task`, falling back to globals."""
    if not task:
        return DEFAULT_GROQ_API_KEY, DEFAULT_GROQ_MODEL
    suffix = str(task).upper().replace("-", "_")
    api_key = os.getenv(f"GROQ_API_KEY_{suffix}", "") or DEFAULT_GROQ_API_KEY
    model = os.getenv(f"GROQ_MODEL_{suffix}", "") or DEFAULT_GROQ_MODEL
    return api_key, model


def get_llm(temperature: float = 0.3, max_tokens: int = 4096, task=None) -> ChatGroq:
    """
    Returns a raw ChatGroq instance for plain-text generation
    (cover letters, salary scripts, etc.).

    Pass `task` to use a task-specific GROQ_API_KEY_<TASK> / GROQ_MODEL_<TASK>.
    Falls back to GROQ_API_KEY / GROQ_MODEL when the task-specific vars
    aren't set.
    """
    api_key, model = _resolve_credentials(task)
    return ChatGroq(
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=1,
        timeout=20,
    )


def get_structured_llm(pydantic_schema, temperature: float = 0.1, max_tokens: int = 8000, task=None):
    """
    Returns a ChatGroq instance bound to a Pydantic schema via
    `with_structured_output()`.  The output is guaranteed to be a
    valid instance of *pydantic_schema* — no manual JSON parsing needed.

    Pass `task` to use a task-specific GROQ_API_KEY_<TASK> / GROQ_MODEL_<TASK>.
    """
    api_key, model = _resolve_credentials(task)
    llm = ChatGroq(
        model=model,
        api_key=api_key,
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
    def __init__(self, task=None):
        self._task = task

    def create(self, model, messages, **kwargs):
        kwargs.pop("disable_reasoning", None)
        kwargs.pop("timeout", None)
        kwargs.pop("response_format", None)
        temp = kwargs.pop("temperature", 0.3)
        max_tok = kwargs.pop("max_tokens", 4096)
        llm = get_llm(temperature=temp, max_tokens=max_tok, task=self._task)
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
    def __init__(self, task=None):
        self.completions = _LegacyChatCompletions(task=task)

class _LegacyClient:
    def __init__(self, task=None):
        self.chat = _LegacyChat(task=task)

def get_llm_client(task=None):
    """Backward-compatible shim. New code should use get_llm() or get_structured_llm()."""
    return _LegacyClient(task=task)
