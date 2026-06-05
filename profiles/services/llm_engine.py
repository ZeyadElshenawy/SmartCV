import os
import logging
import re
import time
from langchain_groq import ChatGroq

from profiles.services.tpm_throttle import reserve_for_invoke

logger = logging.getLogger(__name__)


class AllGroqKeysExhausted(RuntimeError):
    """Raised when EVERY configured Groq key for a task has hit its daily
    token cap (TPD). Loud-by-design: callers (especially benchmarks) must NOT
    silently degrade to offline/regex output — a daily-cap exhaustion is a
    hard stop, not a fallback, or the numbers get corrupted."""

    def __init__(self, task=None, keys_tried: int = 0):
        self.task = task
        self.keys_tried = keys_tried
        super().__init__(
            f"All {keys_tried} Groq key(s) for task={task!r} hit their daily "
            f"token cap (TPD). No key left to rotate to."
        )


# Per-call TPM-leak retry budget. TPMThrottle pre-empts per-minute 429s, so a
# TPM 429 reaching us is a rare estimate miss — sleep + retry the SAME key a
# few times rather than rotate (rotating would burn a key's daily budget on a
# ~60s wait). Capped so a persistent TPM 429 can't loop forever.
_TPM_RETRY_MAX = 3
_TPM_DEFAULT_SLEEP = 5.0
_TPM_MAX_SLEEP = 60.0


def _exc_message_blob(exc) -> str:
    """Flatten an exception + its cause/context chain into one lowercased
    string, so we can scan for Groq's rate-limit message text no matter how
    LangChain wrapped the underlying groq.RateLimitError."""
    parts, seen, e = [], 0, exc
    while e is not None and seen < 6:
        parts.append(str(e))
        e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
        seen += 1
    return " ".join(parts).lower()


def _is_429(exc, blob: str) -> bool:
    code = (getattr(exc, "status_code", None)
            or getattr(getattr(exc, "response", None), "status_code", None))
    return code == 429 or "429" in blob or "rate limit" in blob or "rate_limit" in blob


def _classify_rate_limit(exc):
    """Return 'tpd' (daily cap → rotate key), 'tpm' (per-minute → sleep+retry
    same key), or None (not a rate limit we handle → re-raise).

    Both daily and per-minute are HTTP 429; the discriminator is Groq's
    message text ('tokens per day (TPD)' vs 'tokens per minute (TPM)'). An
    ambiguous 429 (neither phrase) is treated as TPM — sleep+retry never
    wastes a key's daily budget, and the retry cap prevents an infinite loop."""
    blob = _exc_message_blob(exc)
    if not _is_429(exc, blob):
        return None
    if "tokens per day" in blob or "(tpd)" in blob or "per day" in blob:
        return "tpd"
    if "tokens per minute" in blob or "(tpm)" in blob or "per minute" in blob:
        return "tpm"
    return "tpm"  # ambiguous 429 → safe default (sleep, never burn a key)


def _retry_after_seconds(exc, blob: str) -> float:
    """Best-effort wait for a TPM 429: Retry-After header, else 'try again in
    Xs' from the message, else a default. Clamped to a sane ceiling."""
    hdrs = getattr(getattr(exc, "response", None), "headers", None)
    if hdrs:
        try:
            ra = hdrs.get("retry-after") or hdrs.get("Retry-After")
            if ra is not None:
                return max(0.0, min(_TPM_MAX_SLEEP, float(ra)))
        except (TypeError, ValueError):
            pass
    m = re.search(r"try again in ([\d.]+)\s*s", blob)
    if m:
        try:
            return max(0.0, min(_TPM_MAX_SLEEP, float(m.group(1))))
        except ValueError:
            pass
    return _TPM_DEFAULT_SLEEP


class _ThrottledLLM:
    """Wraps a LangChain Runnable so `.invoke()` consults the TPM throttle
    first (per-minute pacing) AND rotates Groq keys on a daily-cap (TPD)
    error. Attribute access other than `invoke` passes through to the inner
    object, preserving `.with_structured_output()` chains.

    Two distinct limits, two distinct responses:
      * TPMThrottle (process-wide rolling 60s window) handles tokens-PER-MINUTE
        by sleeping BEFORE the call — unchanged. NOTE: that one window now
        counts usage across rotated keys too, so after a rotation it slightly
        over-throttles. That's safe — it errs toward sleeping, never toward
        exceeding a per-key TPM.
      * This wrapper handles tokens-PER-DAY: on a TPD 429 it rebuilds the inner
        ChatGroq with the next configured key and retries; when all keys are
        exhausted it raises AllGroqKeysExhausted (never a silent degrade)."""

    __slots__ = ('_inner', '_max_output_tokens', '_keys', '_key_idx', '_rebuild', '_task')

    def __init__(self, inner, max_output_tokens, keys, rebuild, task=None):
        self._inner = inner
        self._max_output_tokens = int(max_output_tokens or 0)
        self._keys = list(keys or [])
        self._key_idx = 0
        self._rebuild = rebuild   # callable(api_key) -> inner runnable
        self._task = task

    def __getattr__(self, name):
        # Only reached when `name` isn't a slot; safe because _inner is always
        # assigned in __init__ before any external attribute access.
        return getattr(self._inner, name)

    def invoke(self, input_, *args, **kwargs):
        reserve_for_invoke(input_, self._max_output_tokens)
        tpm_retries = 0
        while True:
            try:
                return self._inner.invoke(input_, *args, **kwargs)
            except AllGroqKeysExhausted:
                raise
            except Exception as exc:  # noqa: BLE001 — classify & route
                kind = _classify_rate_limit(exc)
                if kind == "tpd":
                    if self._key_idx + 1 < len(self._keys):
                        self._key_idx += 1
                        logger.warning(
                            "Groq TPD daily cap (task=%s); rotating to key #%d/%d.",
                            self._task, self._key_idx + 1, len(self._keys),
                        )
                        self._inner = self._rebuild(self._keys[self._key_idx])
                        reserve_for_invoke(input_, self._max_output_tokens)
                        continue
                    raise AllGroqKeysExhausted(
                        task=self._task, keys_tried=len(self._keys),
                    ) from exc
                if kind == "tpm":
                    if tpm_retries >= _TPM_RETRY_MAX:
                        logger.error(
                            "Groq TPM 429 persisted after %d retries (task=%s); giving up.",
                            tpm_retries, self._task,
                        )
                        raise
                    tpm_retries += 1
                    delay = _retry_after_seconds(exc, _exc_message_blob(exc))
                    logger.warning(
                        "Groq TPM 429 leak-through (task=%s); sleeping %.1fs, "
                        "retry %d/%d on SAME key.",
                        self._task, delay, tpm_retries, _TPM_RETRY_MAX,
                    )
                    time.sleep(delay)
                    reserve_for_invoke(input_, self._max_output_tokens)
                    continue
                raise  # not a rate limit we handle

# ---------------------------------------------------------------------------
# Per-task credential resolution + N-key daily (TPD) rotation.
# Each call site passes a `task` name. We resolve an ORDERED key list:
#   GROQ_API_KEY_<BASE>, GROQ_API_KEY_<BASE>2, ...3, ...4  (stop at the first
#   missing in that sequence), then the global GROQ_API_KEY as final fallback.
# On a daily-cap (TPD) error, _ThrottledLLM.invoke rotates to the next key.
#
# <BASE> is the task suffix, EXCEPT the v2 task names below, which alias onto
# the user's existing key bases so their keys bind without an env rename
# (Option A): resume_gen_v2 -> RESUME_GEN, gap_analyzer_v2_* -> GAP_ANALYZER.
# Model lookup still uses GROQ_MODEL_<SUFFIX> (un-aliased) → global.
# ---------------------------------------------------------------------------
DEFAULT_GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEFAULT_GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# Backward-compat alias (some legacy paths still import LLM_MODEL directly)
LLM_MODEL = DEFAULT_GROQ_MODEL

# Option A — v2 task suffix → key base. The cap-hitting v2 tasks pass
# task='resume_gen_v2' / 'gap_analyzer_v2_primary' | '_retry'; aliasing maps
# them onto the user's GROQ_API_KEY_RESUME_GEN[2-4] / GROQ_API_KEY_GAP_ANALYZER[2-4]
# keys so the existing key names bind without renaming the env vars.
_TASK_KEY_ALIASES = {
    "RESUME_GEN_V2": "RESUME_GEN",
    "GAP_ANALYZER_V2_PRIMARY": "GAP_ANALYZER",
    "GAP_ANALYZER_V2_RETRY": "GAP_ANALYZER",
}


def _task_suffix(task) -> str:
    return str(task).upper().replace("-", "_") if task else ""


def _resolve_model(task) -> str:
    if not task:
        return DEFAULT_GROQ_MODEL
    return os.getenv(f"GROQ_MODEL_{_task_suffix(task)}", "") or DEFAULT_GROQ_MODEL


def _resolve_key_list(task) -> list:
    """Ordered, deduped, non-blank Groq key list for `task` (see header)."""
    keys = []
    if task:
        base = _TASK_KEY_ALIASES.get(_task_suffix(task), _task_suffix(task))
        primary = os.getenv(f"GROQ_API_KEY_{base}", "").strip()
        if primary:
            keys.append(primary)
            i = 2
            while True:
                nxt = os.getenv(f"GROQ_API_KEY_{base}{i}", "").strip()
                if not nxt:
                    break
                keys.append(nxt)
                i += 1
    if DEFAULT_GROQ_API_KEY.strip():
        keys.append(DEFAULT_GROQ_API_KEY.strip())
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _resolve_credentials(task):
    """Back-compat (used by core.health): (primary_key, model). The primary
    is the FIRST key in the rotation list — what the first call actually uses."""
    key_list = _resolve_key_list(task)
    return (key_list[0] if key_list else ""), _resolve_model(task)


def get_llm(temperature: float = 0.3, max_tokens: int = 4096, task=None):
    """
    Returns a TPM-throttled, daily-rotating wrapper over ChatGroq for
    plain-text generation (cover letters, salary scripts, etc.).

    Pass `task` to use task-specific GROQ_API_KEY_<TASK>[2-4] / GROQ_MODEL_<TASK>,
    falling back to the global GROQ_API_KEY / GROQ_MODEL. See module header.
    """
    keys = _resolve_key_list(task)
    model = _resolve_model(task)

    def _build(api_key):
        return ChatGroq(
            model=model, api_key=api_key, temperature=temperature,
            max_tokens=max_tokens, max_retries=1, timeout=20,
        )

    primary = keys[0] if keys else DEFAULT_GROQ_API_KEY
    return _ThrottledLLM(
        _build(primary), max_output_tokens=max_tokens,
        keys=keys or [primary], rebuild=_build, task=task,
    )


def get_structured_llm(pydantic_schema, temperature: float = 0.1, max_tokens: int = 8000, task=None):
    """
    Returns a TPM-throttled, daily-rotating wrapper over a ChatGroq instance
    bound to a Pydantic schema via `with_structured_output()`. The output is
    guaranteed to be a valid instance of *pydantic_schema*.

    Pass `task` to use task-specific GROQ_API_KEY_<TASK>[2-4] / GROQ_MODEL_<TASK>.
    """
    keys = _resolve_key_list(task)
    model = _resolve_model(task)

    def _build(api_key):
        llm = ChatGroq(
            model=model, api_key=api_key, temperature=temperature,
            max_tokens=max_tokens, max_retries=1, timeout=20,
        )
        return llm.with_structured_output(pydantic_schema)

    primary = keys[0] if keys else DEFAULT_GROQ_API_KEY
    return _ThrottledLLM(
        _build(primary), max_output_tokens=max_tokens,
        keys=keys or [primary], rebuild=_build, task=task,
    )


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
