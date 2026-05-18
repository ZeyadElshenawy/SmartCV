"""Integration-test infrastructure: fixture loader, LLM recorder/replay,
and a pipeline runner that drives the full resume-generation flow
end-to-end without requiring a Django test DB.

Recording mode: ``INTEGRATION_RECORD=1 pytest tests/integration/`` —
real Groq calls are made and responses serialised to
``tests/integration/fixtures/recordings/<test_name>.json``.

Replay mode (default): recorded responses are returned without
hitting the network. Suitable for CI.
"""
from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    import django  # noqa: F401
    from django.conf import settings as _dj_settings
    if not _dj_settings.configured:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
        django.setup()
except Exception:  # noqa: BLE001
    pass


FIXTURE_DIR = Path(__file__).parent / "fixtures"
RECORDINGS_DIR = FIXTURE_DIR / "recordings"
INTEGRATION_RECORD = os.environ.get('INTEGRATION_RECORD') == '1'

# PR 4.2 — Frozen KB snapshot lets replay tests exercise the full
# `retrieve_chunks` path (including cosine distance + facet filter +
# category diversification) without DB access. Captured once during
# record mode against the live ``KnowledgeChunk`` table; reused
# verbatim by every replay run. The leading underscore marks it as
# infrastructure (sibling to per-test recordings).
KB_SNAPSHOT_PATH = RECORDINGS_DIR / "_kb_snapshot.json"

# Seconds to sleep before each non-first LLM call during recording.
# Tuned to stay under Groq's TPM ceiling for the largest fixtures
# (Zeyad's 101 KB profile produces a ~28K-token resume-gen prompt;
# combined with the 3 prior LLM calls that's ~50K tokens per test,
# well above the free-tier 30K TPM ceiling without spacing). 15 s ×
# 3 inter-call gaps × 20 tests = ~15 minutes of pure pause time;
# combined with LLM RTT (~3-8 s each) the full record run is ~25-30
# minutes. Replay mode skips this entirely.
DEFAULT_INTER_CALL_PAUSE_SECONDS = float(
    os.environ.get('INTEGRATION_INTER_CALL_PAUSE', '15')
)


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


def load_fixture(name: str) -> dict:
    """Load profile + JD by fixture name.

    Returns ``{'profile': dict, 'jd': dict, 'jd_text': str}`` where
    ``jd`` is the full JD payload (description + extracted_skills +
    extracted_skills_tiers + domain) and ``jd_text`` is the raw
    description body for legibility.
    """
    profile_path = FIXTURE_DIR / f"{name}.json"
    jd_json_path = FIXTURE_DIR / f"{name}.jd.json"
    jd_txt_path = FIXTURE_DIR / f"{name}.jd.txt"

    if not profile_path.exists():
        raise FileNotFoundError(
            f"Missing profile fixture: {profile_path}. "
            f"Add it under tests/integration/fixtures/."
        )

    with open(profile_path, encoding='utf-8') as f:
        profile = json.load(f)
    profile.pop('_test_metadata', None)

    jd_payload: dict
    if jd_json_path.exists():
        with open(jd_json_path, encoding='utf-8') as f:
            jd_payload = json.load(f)
    else:
        # Allow a .jd.txt-only fixture; tests then need an LLM call to
        # extract skills/tiers. Most fixtures should ship with .jd.json.
        if not jd_txt_path.exists():
            raise FileNotFoundError(
                f"Missing JD fixture: neither {jd_json_path} nor "
                f"{jd_txt_path} exists."
            )
        with open(jd_txt_path, encoding='utf-8') as f:
            jd_payload = {
                'title': 'Untitled',
                'company': '',
                'description': f.read(),
                'extracted_skills': [],
                'extracted_skills_tiers': {},
                'domain': '',
            }

    jd_text = jd_payload.get('description', '') or ''
    if jd_txt_path.exists():
        with open(jd_txt_path, encoding='utf-8') as f:
            jd_text = f.read()

    return {'profile': profile, 'jd': jd_payload, 'jd_text': jd_text}


# ---------------------------------------------------------------------------
# LLM recorder
# ---------------------------------------------------------------------------


class _LLMRecorder:
    """Records LLM responses on first run, replays on subsequent runs.

    Recordings are keyed by ``<ClassName>.<test_method>`` (the class
    prefix prevents collisions between Zeyad and DevOps tests that
    share a method name like ``test_no_soft_skill_leak_in_skills``).

    Recording file format (v2):

        {
          "_recording_version": 2,
          "_recording_complete": true,
          "_call_count": 4,
          "_test_name": "TestZeyadAIDeveloper.test_role_classification",
          "_recorded_at": "2026-05-18T...Z",
          "calls": [{"call_index": 0, "result": {...}}, ...]
        }

    Failure modes:
      - Replay with no recording → loud RuntimeError prompting the user
        to run with ``INTEGRATION_RECORD=1``.
      - Replay with incomplete recording (``_recording_complete=false``)
        → RuntimeError at construction time, before any test code runs.
        Triggered by record-mode crashes (Groq rate-limit, network).
      - Replay overruns recording → loud RuntimeError noting the pipeline
        changed shape since recording; re-record.
      - Schema deserialisation failure → falls through to the raw dict.

    Record mode adds inter-call pacing (``DEFAULT_INTER_CALL_PAUSE_SECONDS``)
    before each non-first call to stay under Groq's TPM ceiling.
    """

    def __init__(self, test_name: str, inter_call_pause: float | None = None):
        self.test_name = test_name
        self.recording_path = RECORDINGS_DIR / f"{test_name}.json"
        self.call_index = 0
        self.captures: list[dict] = []
        self.inter_call_pause = (
            inter_call_pause if inter_call_pause is not None
            else DEFAULT_INTER_CALL_PAUSE_SECONDS
        )
        self.recording = self._load_recordings()

        # In replay mode, refuse to use an incomplete recording — that
        # state means a prior record run crashed mid-capture (typically
        # a Groq rate-limit). Continuing would let the pipeline fall
        # back to the offline renderer and produce confusing downstream
        # test failures. Force re-record instead.
        if self.recording is not None and not INTEGRATION_RECORD:
            if not self.recording.get("_recording_complete", True):
                err = self.recording.get("_error", "unknown")
                raise RuntimeError(
                    f"Recording for test '{test_name}' is marked INCOMPLETE "
                    f"(captured {self.recording.get('_call_count', 0)} of N "
                    f"calls; last error: {err}). The recorder now paces "
                    f"calls to avoid this; delete the recording and "
                    f"re-record with INTEGRATION_RECORD=1. "
                    f"Path: {self.recording_path}"
                )

    @property
    def recordings(self) -> list[dict]:
        """Backward-compat accessor — fixture skip-check uses
        ``not recorder.recordings`` to detect missing recordings."""
        if self.recording is None:
            return []
        return self.recording.get("calls", []) or []

    def _load_recordings(self) -> dict | None:
        """Load existing recording. Returns dict in v2 shape, or None
        when no file exists. v1 bare-list recordings are wrapped in a
        v2 envelope with ``_recording_complete=True`` assumed (any v1
        recording that wasn't complete would already have failed replay
        before this PR landed)."""
        if not self.recording_path.exists():
            return None
        with open(self.recording_path, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {
                "_recording_version": 1,
                "_recording_complete": True,
                "_call_count": len(data),
                "_test_name": self.test_name,
                "calls": data,
            }
        return data

    def _save_recordings(self, complete: bool, error: str | None = None):
        """Persist captures with completeness metadata.

        Called after every captured call (each call updates the on-disk
        file so a mid-test crash still leaves an inspectable partial
        recording). On exception, called with ``complete=False`` + the
        error string, then re-raised so the test fails loudly.
        """
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "_recording_version": 2,
            "_recording_complete": complete,
            "_call_count": len(self.captures),
            "_test_name": self.test_name,
            "_recorded_at": datetime.now(timezone.utc).isoformat(),
            "calls": self.captures,
        }
        if error:
            payload["_error"] = error
        with open(self.recording_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, default=str)

    def get_or_record(self, schema, real_invoke):
        """Either replay the next recorded response or invoke the real
        LLM and capture. ``real_invoke`` is a 0-arg callable that runs
        the actual LLM call when recording."""
        idx = self.call_index
        self.call_index += 1

        if INTEGRATION_RECORD:
            try:
                # Pace before each non-first call to stay under Groq's
                # per-minute token ceiling. The 3 prior calls in a test
                # (gap analysis + 2 classifiers) plus the resume-gen
                # call together exceed 30K tokens for large fixtures
                # without spacing; with a 15s pause the per-minute
                # window resets between calls.
                if idx > 0 and self.inter_call_pause > 0:
                    time.sleep(self.inter_call_pause)

                result = real_invoke()
                captured = self._serialize(schema, result)
                self.captures.append({'call_index': idx, 'result': captured})
                # Mark complete after each successful append. If this
                # is the test's last call, the file is correctly marked.
                # If another call follows, the next iteration re-writes
                # the same payload with one more entry; benign.
                self._save_recordings(complete=True)
                return result
            except Exception as exc:
                # Persist partial state so replay refuses to use it.
                self._save_recordings(
                    complete=False,
                    error=f"{type(exc).__name__}: {str(exc)[:300]}",
                )
                raise

        # Replay mode.
        calls = self.recordings
        if not calls:
            raise RuntimeError(
                f"No recording file for test '{self.test_name}' at "
                f"{self.recording_path}. Run with INTEGRATION_RECORD=1 "
                f"to capture recordings, then commit them."
            )
        if idx >= len(calls):
            raise RuntimeError(
                f"Test '{self.test_name}' made more LLM calls than "
                f"recorded ({idx + 1} > {len(calls)}). The pipeline "
                f"changed shape since recording. Re-record with "
                f"INTEGRATION_RECORD=1."
            )
        captured = calls[idx]
        return self._deserialize(schema, captured['result'])

    @staticmethod
    def _serialize(schema, result) -> dict:
        type_name = getattr(schema, '__name__', None) or type(result).__name__
        if hasattr(result, 'model_dump'):
            return {'_type': type_name, '_data': result.model_dump()}
        if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
            return {'_type': 'plain', '_data': result}
        return {'_type': 'unknown', '_data': str(result)}

    @staticmethod
    def _deserialize(schema, captured: dict):
        data = captured.get('_data')
        type_name = captured.get('_type')
        # Prefer the call-site schema (it's the canonical receiver type)
        # over the recorded type_name, since the recorded name may have
        # changed via schema rename.
        if schema is not None and hasattr(schema, 'model_validate'):
            try:
                return schema.model_validate(data)
            except Exception:  # noqa: BLE001
                pass
        if type_name == 'plain':
            return data
        return data


@pytest.fixture
def llm_recorder(request) -> _LLMRecorder:
    """Per-test recorder. Recording key is ``<ClassName>.<method>`` —
    the class prefix prevents method-name collisions across test
    classes (e.g., both Zeyad and DevOps suites have a
    ``test_no_soft_skill_leak_in_skills`` method).

    Skips the test (rather than raising) when neither a recording
    exists nor INTEGRATION_RECORD=1 is set — this lets CI run the
    integration suite without failing every test on a fresh checkout
    that hasn't recorded yet.
    """
    method_name = request.node.name.split('[', 1)[0]
    class_name = request.cls.__name__ if request.cls else 'NoClass'
    test_name = f"{class_name}.{method_name}"
    recorder = _LLMRecorder(test_name=test_name)
    if not INTEGRATION_RECORD and not recorder.recordings:
        pytest.skip(
            f"No recording at {recorder.recording_path}. "
            f"Run with INTEGRATION_RECORD=1 to capture, then commit."
        )
    return recorder


# ---------------------------------------------------------------------------
# LLM patching — replace ``get_structured_llm`` with a recording-aware stub.
# ---------------------------------------------------------------------------


class _RecordedStructuredLLM:
    """Stand-in for a ``get_structured_llm(schema)`` result. Exposes the
    minimum ``.invoke(prompt)`` surface the pipeline uses.

    Holds an *unpatched* reference to the real factory so record mode
    can call it without recursing back through the monkey-patched
    version. The recursion would otherwise be:
    invoke → get_or_record → _do_real → get_structured_llm → _structured_factory →
    _RecordedStructuredLLM → invoke → …
    """

    def __init__(self, schema, recorder: _LLMRecorder, kwargs: dict, real_factory):
        self.schema = schema
        self.recorder = recorder
        self.kwargs = kwargs
        self._real_factory = real_factory

    def invoke(self, prompt):
        def _do_real():
            real_llm = self._real_factory(self.schema, **self.kwargs)
            return real_llm.invoke(prompt)
        return self.recorder.get_or_record(self.schema, _do_real)


class _RecordedPlainLLM:
    """Stand-in for ``get_llm()``. Same unpatched-reference trick as
    ``_RecordedStructuredLLM`` to avoid recursion."""

    def __init__(self, recorder: _LLMRecorder, kwargs: dict, real_factory):
        self.recorder = recorder
        self.kwargs = kwargs
        self._real_factory = real_factory

    def invoke(self, prompt):
        def _do_real():
            real_llm = self._real_factory(**self.kwargs)
            return real_llm.invoke(prompt)
        return self.recorder.get_or_record(None, _do_real)


# ---------------------------------------------------------------------------
# KB snapshot — frozen ``KnowledgeChunk`` table for DB-less replay.
# ---------------------------------------------------------------------------


class _SnapshotChunk:
    """Duck-typed stand-in for ``KnowledgeChunk`` in replay mode.

    Exposes the attributes the production retriever reads: ``kb_id``,
    ``type``, ``title``, ``concrete_rule``, ``body``, ``roles``,
    ``seniority``, ``industries``, ``region``, ``weight``,
    ``embedding``. Anything else falls through to whatever the
    snapshot JSON carries (so a future field addition doesn't break
    existing recordings)."""

    def __init__(self, data: dict):
        for key, value in data.items():
            setattr(self, key, value)

    def __repr__(self):
        return f"<_SnapshotChunk kb_id={getattr(self, 'kb_id', '?')!r}>"


# Module-level cache so the snapshot is read from disk once per session.
_KB_SNAPSHOT_CACHE: list[_SnapshotChunk] | None = None


def _load_kb_snapshot() -> list[_SnapshotChunk]:
    """Load the frozen KB snapshot. Cached after first read.

    In record mode, returns ``[]`` when the snapshot doesn't exist yet
    (capture happens at session start; until then production retrieval
    runs against the live DB). In replay mode, raises if the snapshot
    is missing — the suite can't function without it.
    """
    global _KB_SNAPSHOT_CACHE
    if _KB_SNAPSHOT_CACHE is not None:
        return _KB_SNAPSHOT_CACHE

    if not KB_SNAPSHOT_PATH.exists():
        if INTEGRATION_RECORD:
            return []
        raise RuntimeError(
            f"KB snapshot not found at {KB_SNAPSHOT_PATH}. "
            f"Run with INTEGRATION_RECORD=1 (and a reachable dev DB) to "
            f"capture it. The snapshot is reused across all integration "
            f"tests in replay mode."
        )

    with open(KB_SNAPSHOT_PATH, encoding='utf-8') as f:
        data = json.load(f)
    _KB_SNAPSHOT_CACHE = [_SnapshotChunk(c) for c in (data.get("chunks", []) or [])]
    return _KB_SNAPSHOT_CACHE


def _capture_kb_snapshot() -> int:
    """Dump the live ``KnowledgeChunk`` table to ``_kb_snapshot.json``.

    Only callable in record mode (requires Django + dev DB up).
    Returns the number of chunks captured. Overwrites any existing
    snapshot to keep it fresh.
    """
    from profiles.models import KnowledgeChunk

    chunks_payload: list[dict] = []
    for row in KnowledgeChunk.objects.all():
        chunks_payload.append({
            "kb_id": row.kb_id,
            "type": row.type,
            "title": row.title or "",
            "concrete_rule": row.concrete_rule or "",
            "body": row.body or "",
            "roles": list(row.roles or []),
            "seniority": list(row.seniority or []),
            "industries": list(row.industries or []),
            "region": row.region or "global",
            "weight": row.weight or "medium",
            "embedding": [float(x) for x in row.embedding] if row.embedding is not None else None,
        })

    payload = {
        "_snapshot_version": 1,
        "_snapshot_created": datetime.now(timezone.utc).isoformat(),
        "_chunk_count": len(chunks_payload),
        "chunks": chunks_payload,
    }

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(KB_SNAPSHOT_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, default=str)
    return len(chunks_payload)


def _cosine_distance(a: list, b: list) -> float:
    """Cosine distance ``1 - (a·b) / (|a|·|b|)`` over plain Python lists.

    Tiny epsilon avoids division-by-zero on degenerate vectors;
    production uses pgvector's CosineDistance with the same formula.
    """
    if not a or not b or len(a) != len(b):
        return 1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = (na ** 0.5) * (nb ** 0.5)
    if denom < 1e-12:
        return 1.0
    return 1.0 - (dot / denom)


def _make_patched_query_universal(snapshot: list[_SnapshotChunk]):
    """Return a replacement for ``_query_universal`` that operates on
    the snapshot. Mirrors production: filter by ``UNIVERSAL_CATEGORIES``,
    drop chunks without embeddings, sort by cosine distance to JD."""
    def _patched(jd_embedding, top_n):
        from profiles.services.knowledge_retriever import UNIVERSAL_CATEGORIES
        cands = [
            c for c in snapshot
            if getattr(c, 'type', '') in UNIVERSAL_CATEGORIES
            and getattr(c, 'embedding', None)
        ]
        cands.sort(key=lambda c: _cosine_distance(jd_embedding, c.embedding))
        return cands[:top_n]
    return _patched


def _make_patched_query_role_specific_diversified(snapshot: list[_SnapshotChunk]):
    """Return a replacement for ``_query_role_specific_diversified`` that
    operates on the snapshot. Mirrors production's per-category fetch +
    facet filter + diversification handoff.

    Imports ``_facet_matches`` and ``_diversify_per_category`` from
    production — both are pure (no DB) and shared so the test path
    can't drift from production behaviour."""
    def _patched(
        jd_embedding,
        role_tag,
        seniority,
        region,
        top_n,
        per_category_over_fetch=3,
    ):
        from profiles.services.knowledge_retriever import (
            ROLE_SPECIFIC_CATEGORIES,
            _facet_matches,
            _diversify_per_category,
        )
        per_category: dict[str, list] = {}
        per_fetch = per_category_over_fetch * max(top_n, 1)
        for category in ROLE_SPECIFIC_CATEGORIES:
            cands = [
                c for c in snapshot
                if getattr(c, 'type', '') == category
                and getattr(c, 'embedding', None)
            ]
            cands.sort(key=lambda c: _cosine_distance(jd_embedding, c.embedding))
            cands = cands[:per_fetch]
            kept: list = []
            for c in cands:
                if not _facet_matches(getattr(c, 'roles', []) or [], role_tag):
                    continue
                if not _facet_matches(getattr(c, 'seniority', []) or [], seniority):
                    continue
                if getattr(c, 'region', 'global') not in ("global", region):
                    continue
                kept.append(c)
            if kept:
                per_category[category] = kept
        return _diversify_per_category(per_category, top_n)
    return _patched


@pytest.fixture(scope='session', autouse=True)
def kb_snapshot_ready(django_db_blocker):
    """Session-scoped: in record mode, captures the KB snapshot once
    against the live DB (run before any LLM calls so the file exists
    even if the suite is interrupted mid-run). In replay mode, no-op
    — the snapshot is loaded lazily by the per-test patcher.

    pytest-django blocks DB access by default for performance. The
    ``django_db_blocker.unblock()`` context manager temporarily lifts
    the block for the duration of the snapshot dump."""
    if INTEGRATION_RECORD:
        try:
            with django_db_blocker.unblock():
                count = _capture_kb_snapshot()
            print(f"\nKB snapshot captured: {count} chunks at {KB_SNAPSHOT_PATH}")
        except Exception as exc:  # noqa: BLE001
            print(
                f"\nKB snapshot capture failed ({exc}). Continuing — "
                f"production retrieval may degrade during this record run."
            )
    yield


@pytest.fixture
def patched_llm_calls(llm_recorder, monkeypatch, django_db_blocker):
    """Patch ``get_structured_llm`` + ``get_llm`` at the engine module
    AND at each downstream import site. ``from X import f`` binds the
    function into the importing module's namespace, so patching only
    the source isn't enough — we have to walk the call sites that did
    ``from profiles.services.llm_engine import ...``.

    Captures references to the unpatched factories BEFORE installing
    the patch so record mode can call them without recursing through
    the patched version.

    Also unblocks pytest-django's DB-access guard for the test body.
    In record mode this lets production retrieval hit the live KB;
    in replay mode the unblock is harmless (retrieve_chunks is
    patched to read from the snapshot instead).
    """
    from profiles.services import llm_engine as _engine
    _real_structured = _engine.get_structured_llm
    _real_plain = _engine.get_llm

    def _structured_factory(schema, **kwargs):
        return _RecordedStructuredLLM(schema, llm_recorder, kwargs, _real_structured)

    def _plain_factory(**kwargs):
        return _RecordedPlainLLM(llm_recorder, kwargs, _real_plain)

    monkeypatch.setattr(
        'profiles.services.llm_engine.get_structured_llm',
        _structured_factory,
    )
    monkeypatch.setattr(
        'profiles.services.llm_engine.get_llm',
        _plain_factory,
    )

    importers = [
        'analysis.services.gap_analyzer',
        'profiles.services.role_classifier',
        'resumes.services.resume_generator',
        'jobs.services.skill_extractor',
    ]
    for mod_name in importers:
        try:
            mod = __import__(mod_name, fromlist=['*'])
        except Exception:  # noqa: BLE001
            continue
        if hasattr(mod, 'get_structured_llm'):
            monkeypatch.setattr(f'{mod_name}.get_structured_llm', _structured_factory)
        if hasattr(mod, 'get_llm'):
            monkeypatch.setattr(f'{mod_name}.get_llm', _plain_factory)

    # PR 4.2 — KB-query patches: replay mode reads chunks from the
    # frozen snapshot instead of hitting Django ORM. Record mode skips
    # this (production retrieve_chunks runs against the live DB so the
    # snapshot capture in ``kb_snapshot_ready`` reflects current
    # production state).
    if not INTEGRATION_RECORD:
        snapshot = _load_kb_snapshot()
        if snapshot:
            patched_universal = _make_patched_query_universal(snapshot)
            patched_role_diversified = _make_patched_query_role_specific_diversified(snapshot)
            monkeypatch.setattr(
                'profiles.services.knowledge_retriever._query_universal',
                patched_universal,
            )
            monkeypatch.setattr(
                'profiles.services.knowledge_retriever._query_role_specific_diversified',
                patched_role_diversified,
            )

    # Unblock pytest-django's DB guard for the test body.
    # - Record mode: production retrieval needs real DB access.
    # - Replay mode: retrieve_chunks is patched to read the snapshot;
    #   unblocking is harmless. v2 grounding (which also touches DB
    #   via retrieve_for_skills) will still fall back to v1 because
    #   our duck-typed _FakeUser.id isn't a real UUID — that's a
    #   separate concern.
    with django_db_blocker.unblock():
        yield llm_recorder


# ---------------------------------------------------------------------------
# Pipeline runner — drives the full flow without Django ORM dependency.
# ---------------------------------------------------------------------------


class _FakeUser:
    """Duck-typed User. v2 grounding's ``retrieve_for_skills`` reads
    ``profile.user`` (for evidence retrieval). Provide a minimal stand-in."""

    def __init__(self):
        self.id = 'integration-test-user'
        self.email = 'integration-test@example.com'


class _FakeProfile:
    """Duck-typed UserProfile. The pipeline reads ``data_content`` plus
    backward-compat accessors (``.skills`` etc.); a few code paths also
    touch ``.user_id`` / ``.user``. We populate just what's read."""

    def __init__(self, data_content: dict):
        self.data_content = copy.deepcopy(data_content or {})
        self.user_id = 'integration-test-user'
        self.id = 'integration-test-profile'
        self.user = _FakeUser()

    @property
    def skills(self):
        return list(self.data_content.get('skills', []) or [])

    @property
    def experiences(self):
        return list(self.data_content.get('experiences', []) or [])

    @property
    def education(self):
        return list(self.data_content.get('education', []) or [])

    @property
    def projects(self):
        return list(self.data_content.get('projects', []) or [])

    @property
    def certifications(self):
        return list(self.data_content.get('certifications', []) or [])


def _make_fake_job(jd_payload: dict) -> SimpleNamespace:
    """Duck-typed Job. Reads ``.title``, ``.company``, ``.description``,
    ``.extracted_skills``, ``.extracted_skills_tiers``, ``.domain``."""
    return SimpleNamespace(
        id='integration-test-job',
        title=jd_payload.get('title') or 'Untitled Role',
        company=jd_payload.get('company') or '',
        description=jd_payload.get('description') or '',
        extracted_skills=list(jd_payload.get('extracted_skills') or []),
        extracted_skills_tiers=dict(jd_payload.get('extracted_skills_tiers') or {}),
        domain=jd_payload.get('domain') or '',
    )


def _make_fake_gap_analysis(profile: _FakeProfile, job: SimpleNamespace):
    """Duck-typed GapAnalysis. ``compute_gap_analysis`` returns a dict
    of tiered match data; the pipeline reads ``.matched_must_have``,
    ``.missing_must_have`` etc. as attributes. We populate from a real
    ``compute_gap_analysis`` call (which is itself patched to use the
    recorder)."""
    from analysis.services.gap_analyzer import compute_gap_analysis
    result = compute_gap_analysis(profile, job)
    ga = SimpleNamespace(
        id='integration-test-ga',
        matched_skills=list(result.get('matched_skills') or []),
        missing_skills=list(result.get('missing_skills') or []),
        partial_skills=list(result.get('partial_skills') or []),
        matched_must_have=list(result.get('matched_must_have') or []),
        matched_nice_to_have=list(result.get('matched_nice_to_have') or []),
        missing_must_have=list(result.get('missing_must_have') or []),
        missing_nice_to_have=list(result.get('missing_nice_to_have') or []),
        similarity_score=result.get('similarity_score', 0.0),
        match_band=result.get('match_band', ''),
        avg_proximity=result.get('avg_proximity'),
    )
    # Wrap tier objects so ``.name`` access works — gap_analyzer returns
    # dicts in the flat fields but Pydantic models in the tier fields.
    def _wrap(items):
        out = []
        for item in items:
            if isinstance(item, dict):
                out.append(SimpleNamespace(**item))
            else:
                out.append(item)
        return out
    ga.matched_must_have = _wrap(ga.matched_must_have)
    ga.matched_nice_to_have = _wrap(ga.matched_nice_to_have)
    ga.missing_must_have = _wrap(ga.missing_must_have)
    ga.missing_nice_to_have = _wrap(ga.missing_nice_to_have)
    return ga


def run_full_pipeline(profile_data: dict, jd_payload: dict) -> dict:
    """Drive the SmartCV pipeline end-to-end with duck-typed inputs.

    Returns the generated resume content dict with three integration-only
    metadata keys attached: ``_classification``, ``_retrieval_metadata``,
    ``_plan_metadata``. The renderer ignores underscore-prefixed keys;
    tests use them to assert on classifier / planner / retriever state.

    Skips Django ORM entirely so tests don't need ``--create-db`` or
    a test database. The pipeline functions read profile/job/gap_analysis
    via duck-typed attribute access only.
    """
    profile = _FakeProfile(profile_data)
    job = _make_fake_job(jd_payload)
    gap_analysis = _make_fake_gap_analysis(profile, job)

    from resumes.services.resume_generator import generate_resume_content

    metadata: dict[str, Any] = {}
    resume_content = generate_resume_content(
        profile, job, gap_analysis, metadata=metadata,
    )
    resume_content = dict(resume_content)  # mutable copy
    resume_content.update(metadata)  # attach _classification / _retrieval_metadata / _plan_metadata
    return resume_content


# ---------------------------------------------------------------------------
# Fixture loaders exposed to tests.
# ---------------------------------------------------------------------------


@pytest.fixture
def zeyad_ai_developer() -> dict:
    return load_fixture('zeyad_ai_developer')


@pytest.fixture
def junior_devops() -> dict:
    return load_fixture('junior_devops')
