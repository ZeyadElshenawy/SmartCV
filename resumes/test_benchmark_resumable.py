"""Crash-safe / resumable benchmark harness tests.

Covers the shared _io checkpoint helpers and the gap_eval loop wiring (the
pair-eval template; skill/parser/tailoring use the identical pattern). The
per-item Groq call is mocked — NO live Groq.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from benchmarks import _io
from benchmarks import gap_eval
from profiles.services.llm_engine import AllGroqKeysExhausted


class IoCheckpointTests(SimpleTestCase):
    """The load-bearing mechanism: append/read, success-only completed set,
    success-preferred assembly, torn-line tolerance."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(_io, "RESULTS_DIR", Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def _key(self, r):
        return r.get("k")

    def test_append_writes_lines_to_disk(self):
        _io.append_partial("t", {"k": "a", "v": 1})
        _io.append_partial("t", {"k": "b", "v": 2})
        on_disk = _io.partial_path("t").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(on_disk), 2)
        self.assertEqual([r["k"] for r in _io.read_partial("t")], ["a", "b"])

    def test_completed_keys_excludes_errored_rows(self):
        _io.append_partial("t", {"k": "a"})                              # success
        _io.append_partial("t", {"k": "b", "error": "x"})               # top-level error
        _io.append_partial("t", {"k": "c", "runs": [{"error": "y"}]})   # sub-run error
        _io.append_partial("t", {"k": "d", "stage": "judge", "error": "z"})  # stage error
        self.assertEqual(_io.completed_keys("t", self._key), {"a"})

    def test_assemble_rows_success_preferred(self):
        _io.append_partial("t", {"k": "a", "error": "boom"})  # errored first
        _io.append_partial("t", {"k": "a", "v": 42})          # retried success
        rows = _io.assemble_rows("t", self._key)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["v"], 42)
        self.assertFalse(_io.row_has_error(rows[0]))

    def test_torn_last_line_tolerated(self):
        _io.partial_path("t").write_text('{"k": "a"}\n{"k": "b", "v":', encoding="utf-8")
        self.assertEqual([r["k"] for r in _io.read_partial("t")], ["a"])


# --- gap_eval integration fixtures (3 pairs: cvA x {jd1,jd2,jd3}) -------------
def _manifest():
    return {"cvs": [{"id": "cvA", "path": "x"}],
            "expected_match_strength": {"cvA": {"jd1": "strong", "jd2": "strong", "jd3": "strong"}}}


def _jds(manifest=None):
    return {jid: {"id": jid, "title": jid, "expected_skills": []} for jid in ("jd1", "jd2", "jd3")}


_GOOD = {"similarity_score": 0.8, "matched_skills": [], "missing_skills": [],
         "partial_skills": [], "analysis_method": "llm_v2"}


def _success_row(jid, score=0.7):
    return {"cv_id": "cvA", "jd_id": jid, "expected": "strong",
            "runs": [{"similarity_score": score, "coverage": {"coverage_ratio": 1.0},
                      "latency_ms": 1.0, "error": None}]}


class GapEvalResumableTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (
            patch.object(_io, "RESULTS_DIR", Path(tmp.name)),
            patch("benchmarks.gap_eval._load_manifest", _manifest),
            patch("benchmarks.gap_eval._load_jds", _jds),
            patch("benchmarks.gap_eval.parse_cv", lambda path: {"_ok": True}),
            patch("benchmarks.gap_eval._profile_from_parsed", lambda parsed: SimpleNamespace()),
            patch("benchmarks.gap_eval._job_stub", lambda jd: SimpleNamespace()),
            patch("benchmarks.gap_eval._coverage", lambda result, exp: {"coverage_ratio": 1.0}),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_incremental_write_then_final_assembly(self):
        # 3 items land on disk BEFORE the final summary write; summary built from
        # the partial; partial cleared after a successful final write.
        captured = {}
        real_write = _io.write_section

        def spy(name, payload):
            captured["lines_before_final"] = len(
                _io.partial_path(name).read_text(encoding="utf-8").splitlines())
            return real_write(name, payload)

        with patch("benchmarks.gap_eval.compute_gap_analysis", return_value=_GOOD), \
             patch("benchmarks.gap_eval.write_section", side_effect=spy):
            payload = gap_eval.run()

        self.assertEqual(captured["lines_before_final"], 3)
        self.assertEqual(payload["n_pairs_evaluated"], 3)
        self.assertFalse(_io.partial_path("gap_eval").exists())  # cleared after final

    def test_resume_skips_completed(self):
        _io.append_partial("gap_eval", _success_row("jd1"))
        _io.append_partial("gap_eval", _success_row("jd2"))
        seen = []
        with patch("benchmarks.gap_eval.compute_gap_analysis",
                   side_effect=lambda p, j: (seen.append(1), _GOOD)[1]):
            payload = gap_eval.run()
        self.assertEqual(len(seen), 1)                # only jd3 processed
        self.assertEqual(payload["n_pairs_evaluated"], 3)

    def test_retry_errored_item(self):  # LOAD-BEARING
        _io.append_partial("gap_eval", _success_row("jd1"))
        _io.append_partial("gap_eval", {  # jd2 ERRORED
            "cv_id": "cvA", "jd_id": "jd2", "expected": "strong",
            "runs": [{"similarity_score": None, "coverage": {}, "latency_ms": 1.0,
                      "error": "RuntimeError: boom"}]})
        seen = []
        with patch("benchmarks.gap_eval.compute_gap_analysis",
                   side_effect=lambda p, j: (seen.append(1), _GOOD)[1]):
            payload = gap_eval.run()
        # jd2 (errored) is RETRIED + jd3 (never done) → 2 calls; jd1 skipped
        self.assertEqual(len(seen), 2)
        by_jd = {r["jd_id"]: r for r in payload["rows"]}
        self.assertFalse(_io.row_has_error(by_jd["jd2"]))   # now successful

    def test_clean_exhaustion_stop_no_fake_rows(self):
        calls = {"n": 0}

        def se(p, j):
            calls["n"] += 1
            if calls["n"] == 3:
                raise AllGroqKeysExhausted(task="gap_analyzer_v2_primary", keys_tried=4)
            return _GOOD

        with patch("benchmarks.gap_eval.compute_gap_analysis", side_effect=se):
            payload = gap_eval.run()
        self.assertEqual(payload["status"], "partial_exhausted")
        self.assertEqual(payload["completed"], 2)
        rows = _io.read_partial("gap_eval")
        self.assertEqual(len(rows), 2)                       # only completed
        self.assertTrue(all(not _io.row_has_error(r) for r in rows))  # no fake error rows
        self.assertTrue(_io.partial_path("gap_eval").exists())        # kept for resume

    def test_crash_before_final_write_then_resume(self):
        calls = {"n": 0}

        def se(p, j):
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt()   # process-kill simulation (BaseException)
            return _GOOD

        with patch("benchmarks.gap_eval.compute_gap_analysis", side_effect=se):
            with self.assertRaises(KeyboardInterrupt):
                gap_eval.run()
        # The 2 completed items are on disk (incremental), no final summary yet.
        self.assertEqual(len(_io.read_partial("gap_eval")), 2)
        self.assertTrue(_io.partial_path("gap_eval").exists())

        seen = []
        with patch("benchmarks.gap_eval.compute_gap_analysis",
                   side_effect=lambda p, j: (seen.append(1), _GOOD)[1]):
            payload = gap_eval.run()
        self.assertEqual(len(seen), 1)                # resumed from item 3 only
        self.assertEqual(payload["n_pairs_evaluated"], 3)


# --- run_all orchestration: partial-phase handling + phase-level resume ------
def _ok(name):
    return {"phase": name, "ok": True, "wall_seconds": 0, "payload": {"benchmark": name}}


def _partial(name, completed=3, total=25):
    return {"phase": name, "ok": True, "wall_seconds": 0,
            "payload": {"benchmark": name, "status": "partial_exhausted",
                        "completed": completed, "total": total,
                        "partial_path": f"/x/{name}.partial.jsonl", "resumable": True}}


class RunAllResumableTests(SimpleTestCase):
    def setUp(self):
        from benchmarks import run_all
        self.run_all = run_all
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (
            patch.object(_io, "RESULTS_DIR", Path(tmp.name)),
            patch.object(run_all, "RESULTS_DIR", Path(tmp.name)),
            patch.object(run_all, "_headlines", lambda payloads: {}),
            patch.object(run_all, "_format_md", lambda combined: "md"),
            patch.object(run_all, "_sync_docs", lambda md: None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _run(self, phase_results, **kwargs):
        calls = []

        def fake_phase(name, **kw):
            calls.append(name)
            return phase_results[name]

        with patch.object(self.run_all, "_run_phase", side_effect=fake_phase):
            out = self.run_all.run(**kwargs)
        return out, calls

    def test_partial_exhausted_stops_cleanly(self):
        results = {
            "ats_eval": _ok("ats_eval"),
            "latency_runner": _ok("latency_runner"),
            "parser_eval": _ok("parser_eval"),
            "skill_extractor_eval": _partial("skill_extractor_eval", 3, 25),
            # gap_eval should NOT be reached
        }
        out, calls = self._run(results)
        # Did not crash on the missing aggregate; recorded as resumable-incomplete.
        self.assertEqual(out["status"], "partial_exhausted")
        self.assertTrue(out["resumable"])
        self.assertEqual(out["stopped_phase"]["phase"], "skill_extractor_eval")
        self.assertEqual(out["stopped_phase"]["completed"], 3)
        self.assertEqual(out["stopped_phase"]["total"], 25)
        # Stopped before the later (shared-account) phases.
        self.assertNotIn("gap_eval", calls)
        # Completed phases banked in progress; the exhausted one is NOT.
        prog = {r["phase"] for r in _io.read_partial("run_all_progress")}
        self.assertEqual(prog, {"ats_eval", "latency_runner", "parser_eval"})

    def test_full_success_clears_progress(self):
        results = {p: _ok(p) for p in
                   ("ats_eval", "latency_runner", "parser_eval",
                    "skill_extractor_eval", "gap_eval")}
        out, calls = self._run(results)
        self.assertEqual(out["status"], "complete")
        self.assertFalse(out["resumable"])
        self.assertEqual(set(calls), set(results))         # all ran
        self.assertFalse(_io.partial_path("run_all_progress").exists())  # cleared

    def test_resume_skips_completed_phases(self):
        # Prior run finished ats/latency/parser; re-run resumes the rest.
        for p in ("ats_eval", "latency_runner", "parser_eval"):
            _io.append_partial("run_all_progress", {"phase": p, "payload": {"benchmark": p}})
        results = {
            "skill_extractor_eval": _ok("skill_extractor_eval"),
            "gap_eval": _ok("gap_eval"),
        }
        out, calls = self._run(results)
        # Completed phases NOT re-invoked (no token re-burn)…
        self.assertNotIn("ats_eval", calls)
        self.assertNotIn("parser_eval", calls)
        # …the remaining phases run.
        self.assertIn("skill_extractor_eval", calls)
        self.assertIn("gap_eval", calls)
        self.assertEqual(out["status"], "complete")
        # Final report still lists the prior-completed phases.
        self.assertIn("parser_eval", out["phases_run"])
        self.assertIn("gap_eval", out["phases_run"])
        # Clean completion clears progress for a future fresh run.
        self.assertFalse(_io.partial_path("run_all_progress").exists())
