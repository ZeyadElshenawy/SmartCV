"""N-key Groq daily-rotation + alias-binding tests for llm_engine.

Covers: the alias map (resume_gen_v2 → RESUME_GEN keys), the ordered key
list, and _ThrottledLLM.invoke's TPD-rotate / TPM-sleep / exhaustion routing.
No real Groq — a scripted fake inner raises simulated 429s.
"""
from __future__ import annotations

import os
from unittest.mock import patch

from django.test import SimpleTestCase

from profiles.services import llm_engine
from profiles.services.llm_engine import (
    AllGroqKeysExhausted,
    _ThrottledLLM,
    _resolve_key_list,
)

# Realistic Groq 429 message bodies (the discriminator is the substring).
_TPD = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "meta-llama/llama-4-scout on tokens per day (TPD): Limit 500000, Used "
    "499059, Requested 1317. Please try again in 2m1s.', 'type': 'tokens', "
    "'code': 'rate_limit_exceeded'}}"
)
_TPM = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached on tokens "
    "per minute (TPM): Limit 30000, Used 29000, Requested 2000. Please try "
    "again in 12.5s.', 'code': 'rate_limit_exceeded'}}"
)


def _err(msg):
    return Exception(msg)


class _ScriptedInner:
    """invoke() consumes the next scripted action: an Exception → raise it,
    anything else → return it."""

    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    def invoke(self, *a, **k):
        self.calls += 1
        act = self.actions.pop(0)
        if isinstance(act, Exception):
            raise act
        return act


def _clean_task_env():
    for k in list(os.environ):
        if k.startswith("GROQ_API_KEY_RESUME_GEN") or k.startswith("GROQ_API_KEY_GAP_ANALYZER"):
            os.environ.pop(k)


class KeyListResolutionTests(SimpleTestCase):
    """Alias map + ordered N-key list, with a deterministic global fallback."""

    def setUp(self):
        p = patch.object(llm_engine, "DEFAULT_GROQ_API_KEY", "GLOBAL")
        p.start()
        self.addCleanup(p.stop)

    def test_resume_gen_v2_aliases_to_resume_gen_keys(self):
        with patch.dict(os.environ, {}, clear=False):
            _clean_task_env()
            os.environ["GROQ_API_KEY_RESUME_GEN"] = "K1"
            os.environ["GROQ_API_KEY_RESUME_GEN2"] = "K2"
            os.environ["GROQ_API_KEY_RESUME_GEN3"] = "K3"
            keys = _resolve_key_list("resume_gen_v2")
        self.assertEqual(keys, ["K1", "K2", "K3", "GLOBAL"])

    def test_gap_analyzer_v2_primary_aliases_to_gap_analyzer_keys(self):
        with patch.dict(os.environ, {}, clear=False):
            _clean_task_env()
            os.environ["GROQ_API_KEY_GAP_ANALYZER"] = "G1"
            os.environ["GROQ_API_KEY_GAP_ANALYZER2"] = "G2"
            keys = _resolve_key_list("gap_analyzer_v2_retry")
        self.assertEqual(keys, ["G1", "G2", "GLOBAL"])

    def test_stops_at_first_missing_number(self):
        with patch.dict(os.environ, {}, clear=False):
            _clean_task_env()
            os.environ["GROQ_API_KEY_RESUME_GEN"] = "K1"
            os.environ["GROQ_API_KEY_RESUME_GEN3"] = "K3"  # gap at 2 → unreachable
            keys = _resolve_key_list("resume_gen_v2")
        self.assertEqual(keys, ["K1", "GLOBAL"])

    def test_no_per_task_key_uses_global_only(self):
        with patch.dict(os.environ, {}, clear=False):
            _clean_task_env()
            keys = _resolve_key_list("resume_gen_v2")
        self.assertEqual(keys, ["GLOBAL"])

    def test_dedupe_when_per_task_equals_global(self):
        with patch.dict(os.environ, {}, clear=False):
            _clean_task_env()
            os.environ["GROQ_API_KEY_RESUME_GEN"] = "GLOBAL"  # same as fallback
            keys = _resolve_key_list("resume_gen_v2")
        self.assertEqual(keys, ["GLOBAL"])  # deduped, not ["GLOBAL","GLOBAL"]


class RotationInvokeTests(SimpleTestCase):
    """_ThrottledLLM.invoke: rotate on TPD, sleep+retry-same-key on TPM,
    raise on exhaustion, re-raise non-rate-limit errors."""

    def setUp(self):
        # No real throttle sleeping in unit tests.
        r = patch.object(llm_engine, "reserve_for_invoke", lambda *a, **k: 0.0)
        r.start()
        self.addCleanup(r.stop)

    def _wrap(self, fakes, keys):
        return _ThrottledLLM(
            fakes[keys[0]], max_output_tokens=100, keys=keys,
            rebuild=lambda k: fakes[k], task="resume_gen_v2",
        )

    def test_tpd_rotates_to_next_key(self):
        keys = ["K1", "K2"]
        fakes = {"K1": _ScriptedInner([_err(_TPD)]), "K2": _ScriptedInner(["OK"])}
        w = self._wrap(fakes, keys)
        self.assertEqual(w.invoke("x"), "OK")
        self.assertEqual(w._key_idx, 1)
        self.assertEqual(fakes["K1"].calls, 1)
        self.assertEqual(fakes["K2"].calls, 1)

    def test_tpm_sleeps_and_retries_same_key_no_rotation(self):
        keys = ["K1", "K2"]
        fakes = {
            "K1": _ScriptedInner([_err(_TPM), "OK"]),  # TPM then success, SAME key
            "K2": _ScriptedInner(["WRONG"]),
        }
        w = self._wrap(fakes, keys)
        with patch.object(llm_engine.time, "sleep") as msleep:
            self.assertEqual(w.invoke("x"), "OK")
            self.assertTrue(msleep.called)
        self.assertEqual(w._key_idx, 0)        # did NOT rotate
        self.assertEqual(fakes["K1"].calls, 2)  # retried same key
        self.assertEqual(fakes["K2"].calls, 0)  # never touched the next key

    def test_all_keys_tpd_raises_exhausted_no_silent_degrade(self):
        keys = ["K1", "K2"]
        fakes = {"K1": _ScriptedInner([_err(_TPD)]), "K2": _ScriptedInner([_err(_TPD)])}
        w = self._wrap(fakes, keys)
        with self.assertRaises(AllGroqKeysExhausted) as cm:
            w.invoke("x")
        self.assertEqual(cm.exception.keys_tried, 2)
        self.assertEqual(cm.exception.task, "resume_gen_v2")

    def test_non_rate_limit_error_reraises_without_rotation(self):
        keys = ["K1", "K2"]
        fakes = {
            "K1": _ScriptedInner([_err("ValueError: schema mismatch")]),
            "K2": _ScriptedInner(["OK"]),
        }
        w = self._wrap(fakes, keys)
        with self.assertRaises(Exception) as cm:
            w.invoke("x")
        self.assertNotIsInstance(cm.exception, AllGroqKeysExhausted)
        self.assertEqual(w._key_idx, 0)
        self.assertEqual(fakes["K2"].calls, 0)

    def test_tpm_persisting_raises_after_cap_not_exhausted(self):
        keys = ["K1"]
        fakes = {"K1": _ScriptedInner([_err(_TPM)] * 10)}
        w = self._wrap(fakes, keys)
        with patch.object(llm_engine.time, "sleep"):
            with self.assertRaises(Exception) as cm:
                w.invoke("x")
        self.assertNotIsInstance(cm.exception, AllGroqKeysExhausted)
        self.assertEqual(fakes["K1"].calls, llm_engine._TPM_RETRY_MAX + 1)
