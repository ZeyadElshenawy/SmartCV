"""End-to-end benchmark runner — Phase E.

Runs every benchmark in ``benchmarks/`` and writes:

- ``benchmarks/results/<date>/run_all.json`` — combined metrics blob.
- ``benchmarks/results/<date>/run_all.md`` — human-readable summary
  (also re-published into ``docs/benchmarks.md`` between AUTOGEN markers).

Each phase still writes its own per-benchmark JSON next to this file (the
individual entry points are unchanged), so a partial run produces partial
artifacts.

Phases included:
    Phase B  — endpoint latency
    Phase D1 — CV parser accuracy
    Phase D2 — skill extraction precision/recall/F1
    Phase D3 — gap analyzer separation + coverage
    Phase D4 — ATS scoring determinism + separation

Phase D5 (LLM-judged resume tailoring) is run separately because of its
LLM-call cost; ``--with-tailoring`` opts into it once it's wired up.

Run:
    python -m benchmarks.run_all
    python -m benchmarks.run_all --skip latency_runner   # offline mode
    python -m benchmarks.run_all --gap-repeats 3         # heavier disclosure
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from benchmarks._io import REPO_ROOT, RESULTS_DIR, write_section


PHASES = ("ats_eval", "latency_runner", "parser_eval", "skill_extractor_eval",
          "gap_eval", "tailoring_eval")


def _import_phase(name: str):
    if name == "ats_eval":
        from benchmarks import ats_eval as m
    elif name == "latency_runner":
        from benchmarks import latency_runner as m
    elif name == "parser_eval":
        from benchmarks import parser_eval as m
    elif name == "skill_extractor_eval":
        from benchmarks import skill_extractor_eval as m
    elif name == "gap_eval":
        from benchmarks import gap_eval as m
    elif name == "tailoring_eval":
        from benchmarks import tailoring_eval as m
    else:
        raise ValueError(f"unknown phase: {name}")
    return m


def _run_phase(name: str, **kwargs) -> dict:
    """Invoke one phase and capture its payload + any uncaught exception."""
    started = time.perf_counter()
    try:
        m = _import_phase(name)
        payload = m.run(**kwargs)
        return {
            "phase": name,
            "ok": True,
            "wall_seconds": round(time.perf_counter() - started, 2),
            "payload": payload,
        }
    except Exception as exc:  # noqa: BLE001 — show to caller, never crash the run
        return {
            "phase": name,
            "ok": False,
            "wall_seconds": round(time.perf_counter() - started, 2),
            "error": f"{exc.__class__.__name__}: {exc}",
            "traceback": traceback.format_exc().splitlines()[-8:],
        }


# ─── Headline extraction ────────────────────────────────────────────────────

def _headlines(results: dict) -> dict:
    """Pull a small set of README-quality numbers out of the per-phase payloads."""
    h: dict = {}

    ats = results.get("ats_eval", {}).get("payload")
    if ats:
        sep = ats["separation"]
        h["ats"] = {
            "deterministic": ats["determinism"]["deterministic"],
            "matched_mean": sep["matched"]["mean"],
            "mismatched_mean": sep["mismatched"]["mean"],
            "cohens_d": sep["cohens_d"],
            "stuffing_fired": ats["stuffing"]["stuffing_fired"],
        }

    lat = results.get("latency_runner", {}).get("payload")
    if lat:
        warm_p95s = [r["cold_warm"]["warm_p95"] for r in lat["results"]
                     if r["cold_warm"]["warm_p95"] is not None]
        h["latency"] = {
            "n_routes": lat["n_routes"],
            "requests_per_route": lat["requests_per_route"],
            "warm_p95_max_ms": max(warm_p95s) if warm_p95s else None,
            "warm_p95_median_ms": (
                round(sorted(warm_p95s)[len(warm_p95s) // 2], 2) if warm_p95s else None
            ),
        }

    parser = results.get("parser_eval", {}).get("payload")
    if parser:
        agg = parser["aggregate"]
        h["parser"] = {
            "n_cvs": parser["n_cvs"],
            "personal_info_accuracy": agg["personal_info_accuracy"]["mean"],
            "section_presence_accuracy": agg["section_presence_accuracy"]["mean"],
            "skills_jaccard": agg["skills_jaccard"]["mean"],
            "skills_f1": agg["skills_f1"]["mean"],
        }

    sx = results.get("skill_extractor_eval", {}).get("payload")
    if sx:
        agg = sx["aggregate"]
        h["skill_extractor"] = {
            "n_jds": sx["n_jds"],
            "repeats_per_jd": sx["repeats_per_jd"],
            "f1": agg["f1"]["mean"],
            "precision": agg["precision"]["mean"],
            "recall": agg["recall"]["mean"],
            "hallucination_rate": agg["hallucination_rate"]["mean"],
        }

    tailor = results.get("tailoring_eval", {}).get("payload")
    if tailor:
        a = tailor["axes"]
        prog = tailor["programmatic"]
        h["tailoring"] = {
            "n_pairs": tailor["n_pairs"],
            "buckets": tailor["buckets_evaluated"],
            "factuality_mean": a["factuality"]["mean"],
            "relevance_mean": a["relevance"]["mean"],
            "ats_fit_mean": a["ats_fit"]["mean"],
            "human_voice_mean": a["human_voice"]["mean"],
            "entity_grounding_mean": prog["entity_grounding_ratio"]["mean"],
            "banned_voice_hits_mean": prog["banned_voice_hits_per_resume"]["mean"],
        }

    gap = results.get("gap_eval", {}).get("payload")
    if gap:
        h["gap"] = {
            "n_pairs": gap["n_pairs_evaluated"],
            "repeats_per_pair": gap["repeats_per_pair"],
            "coverage_mean": gap["coverage"]["mean"],
            "perfect_coverage_pairs": gap["coverage"]["perfect_coverage_pairs"],
            "strong_mean": gap["buckets"]["strong"]["mean"],
            "partial_mean": gap["buckets"]["partial"]["mean"],
            "weak_mean": gap["buckets"]["weak"]["mean"],
            "cohens_d_strong_vs_weak": gap["separation"]["strong_vs_weak_cohens_d"],
        }

    return h


def _format_md(combined: dict) -> str:
    """Render a markdown summary from the combined results blob."""
    h = combined["headlines"]
    lines = [
        "# SmartCV Benchmark Run",
        "",
        f"- **Run date:** {combined['run_at_utc']}",
        f"- **Wall time:** {combined['wall_seconds']}s",
        f"- **Platform:** {combined['platform']['system']} "
        f"{combined['platform']['release']} / Python {combined['platform']['python']}",
        f"- **Phases:** {', '.join(combined['phases_run'])}",
        "",
        "## Headline Metrics",
        "",
        "| Metric | Value | N | Source |",
        "| --- | --- | --- | --- |",
    ]

    if "ats" in h:
        a = h["ats"]
        lines.append(f"| ATS scoring deterministic (sigma=0) | **{a['deterministic']}** | "
                     f"10 runs x 3 fixtures | benchmarks/ats_eval.py |")
        lines.append(f"| ATS matched-vs-mismatched separation | matched **{a['matched_mean']}** "
                     f"vs mismatched **{a['mismatched_mean']}** (Cohen's d = **{a['cohens_d']}**) "
                     f"| 3 matched, 6 mismatched | benchmarks/ats_eval.py |")

    if "latency" in h:
        l = h["latency"]
        lines.append(f"| Endpoint warm p95 (max across routes) | "
                     f"**{l['warm_p95_max_ms']} ms** | "
                     f"{l['n_routes']} routes x {l['requests_per_route']} req | "
                     f"benchmarks/latency_runner.py |")

    if "parser" in h:
        p = h["parser"]
        lines.append(f"| CV parser personal-info accuracy | **{p['personal_info_accuracy']:.3f}** "
                     f"| {p['n_cvs']} CVs | benchmarks/parser_eval.py |")
        lines.append(f"| CV parser skills F1 | **{p['skills_f1']:.3f}** "
                     f"(Jaccard {p['skills_jaccard']:.3f}) "
                     f"| {p['n_cvs']} CVs | benchmarks/parser_eval.py |")

    if "skill_extractor" in h:
        s = h["skill_extractor"]
        lines.append(f"| Skill extractor F1 | **{s['f1']:.3f}** "
                     f"(P={s['precision']:.3f}, R={s['recall']:.3f}, "
                     f"halluc={s['hallucination_rate']:.3f}) "
                     f"| {s['n_jds']} JDs x {s['repeats_per_jd']} runs "
                     f"| benchmarks/skill_extractor_eval.py |")

    if "gap" in h:
        g = h["gap"]
        lines.append(f"| Gap analyzer coverage (Phase 2 reconciliation) | "
                     f"**{g['coverage_mean']:.3f}** "
                     f"({g['perfect_coverage_pairs']}/{g['n_pairs']} pairs at 100%) "
                     f"| {g['n_pairs']} (CV,JD) pairs | benchmarks/gap_eval.py |")
        lines.append(f"| Gap analyzer separation (similarity score) | "
                     f"strong **{g['strong_mean']}** / partial **{g['partial_mean']}** "
                     f"/ weak **{g['weak_mean']}** "
                     f"(Cohen's d strong-vs-weak = **{g['cohens_d_strong_vs_weak']}**) "
                     f"| {g['n_pairs']} pairs | benchmarks/gap_eval.py |")

    if "tailoring" in h:
        t = h["tailoring"]
        lines.append(f"| Tailored resume — judge axes (1-10) | "
                     f"factuality **{t['factuality_mean']}** / "
                     f"relevance **{t['relevance_mean']}** / "
                     f"ats_fit **{t['ats_fit_mean']}** / "
                     f"human_voice **{t['human_voice_mean']}** "
                     f"| {t['n_pairs']} pairs ({'+'.join(t['buckets'])}) "
                     f"| benchmarks/tailoring_eval.py |")
        lines.append(f"| Tailored resume — programmatic entity grounding | "
                     f"**{t['entity_grounding_mean']}** of generated entities "
                     f"appear verbatim in source CV "
                     f"| {t['n_pairs']} pairs | benchmarks/tailoring_eval.py |")

    lines += [
        "",
        "## Phase Wall Times",
        "",
        "| Phase | Wall (s) | OK |",
        "| --- | --- | --- |",
    ]
    for phase in combined["phases_run"]:
        info = combined["phase_info"].get(phase, {})
        ok = "yes" if info.get("ok") else "no"
        lines.append(f"| {phase} | {info.get('wall_seconds')} | {ok} |")

    lines += [
        "",
        "## Disclosure",
        "",
        "- LLM metrics (parser, skill extractor, gap analyzer) are run against "
        "Groq llama-4-scout. Stochasticity is reported per-phase as std dev across "
        "configurable repeats; the headline values above are the mean.",
        "- Latency numbers are measured in-process via Django's test Client on the "
        "developer machine — production WAN latency is not included.",
        "- ATS scoring is pure-Python and deterministic; the matched-vs-mismatched "
        "separation uses an in-process synthetic suite (3 jobs x 3 matched and "
        "6 cross-paired mismatches) so it is reproducible without external fixtures.",
        "- Parser, skill-extractor, and gap-analyzer use 10 hand-curated CVs and "
        "5 hand-written JDs (`benchmarks/fixtures/`). The CV PDFs themselves are "
        "git-ignored to avoid republishing personal data.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python -m benchmarks.run_all",
        "```",
        "",
        "Per-phase entry points are listed in `benchmarks/__init__.py`.",
    ]
    return "\n".join(lines)


# ─── docs/benchmarks.md sync ────────────────────────────────────────────────

DOCS_PATH = REPO_ROOT / "docs" / "benchmarks.md"
AUTOGEN_OPEN = "<!-- benchmarks:autogen:start -->"
AUTOGEN_CLOSE = "<!-- benchmarks:autogen:end -->"


def _sync_docs(md: str) -> Path | None:
    """Replace the autogen block in docs/benchmarks.md with ``md``. No-op if file missing."""
    if not DOCS_PATH.exists():
        return None
    text = DOCS_PATH.read_text(encoding="utf-8")
    if AUTOGEN_OPEN not in text or AUTOGEN_CLOSE not in text:
        return None
    block = f"{AUTOGEN_OPEN}\n\n{md}\n\n{AUTOGEN_CLOSE}"
    new_text = re.sub(
        re.escape(AUTOGEN_OPEN) + r".*?" + re.escape(AUTOGEN_CLOSE),
        block,
        text,
        count=1,
        flags=re.DOTALL,
    )
    DOCS_PATH.write_text(new_text, encoding="utf-8")
    return DOCS_PATH


# ─── Entrypoint ─────────────────────────────────────────────────────────────

def run(skip: Iterable[str] = (), *, gap_repeats: int = 1, sx_repeats: int = 1,
        parser_repeats: int = 1, latency_requests: int = 100,
        with_tailoring: bool = False,
        tailoring_buckets: tuple[str, ...] = ("strong",)) -> dict:
    skip_set = set(skip)
    phases_run: list[str] = []
    phase_info: dict[str, dict] = {}
    payloads: dict[str, dict] = {}

    started = time.perf_counter()

    plan = [
        ("ats_eval", {}),
        ("latency_runner", {"requests_per_route": latency_requests}),
        ("parser_eval", {"repeats": parser_repeats}),
        ("skill_extractor_eval", {"repeats": sx_repeats}),
        ("gap_eval", {"repeats": gap_repeats}),
    ]
    if with_tailoring:
        plan.append(("tailoring_eval", {"buckets": tailoring_buckets}))

    for phase, kwargs in plan:
        if phase in skip_set:
            phase_info[phase] = {"ok": None, "skipped": True, "wall_seconds": 0}
            continue
        info = _run_phase(phase, **kwargs)
        phases_run.append(phase)
        phase_info[phase] = {k: v for k, v in info.items() if k != "payload"}
        payloads[phase] = info

    headlines = _headlines(payloads)
    combined = {
        "benchmark": "run_all",
        "version": 1,
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "wall_seconds": round(time.perf_counter() - started, 2),
        "platform": {
            "python": sys.version.split()[0],
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "phases_run": phases_run,
        "phase_info": phase_info,
        "headlines": headlines,
        "phase_payloads": {
            # Embed each phase's payload (already saved as standalone JSON too).
            name: payloads[name].get("payload") if payloads[name].get("ok") else None
            for name in phases_run
        },
    }

    md = _format_md(combined)
    json_path = write_section("run_all", combined)
    md_path = json_path.with_suffix(".md")
    md_path.write_text(md, encoding="utf-8")
    docs_path = _sync_docs(md)

    combined["written_to"] = {
        "json": str(json_path),
        "markdown": str(md_path),
        "docs": str(docs_path) if docs_path else None,
    }
    return combined


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SmartCV end-to-end benchmark runner")
    p.add_argument("--skip", nargs="*", default=[], choices=PHASES,
                   help="phases to skip")
    p.add_argument("--gap-repeats", type=int, default=1)
    p.add_argument("--sx-repeats", type=int, default=1, dest="sx_repeats",
                   help="repeats per JD for skill_extractor_eval")
    p.add_argument("--parser-repeats", type=int, default=1)
    p.add_argument("--latency-requests", type=int, default=100,
                   help="requests per route for latency_runner")
    p.add_argument("--with-tailoring", action="store_true",
                   help="include the LLM-judged tailoring eval (Phase D5; ~3 min, more LLM calls)")
    p.add_argument("--tailoring-buckets", nargs="+", default=["strong"],
                   choices=["strong", "partial", "weak"],
                   help="manifest buckets to evaluate for tailoring (default: strong only)")
    return p.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    args = _parse_args()
    out = run(
        skip=args.skip,
        gap_repeats=args.gap_repeats,
        sx_repeats=args.sx_repeats,
        parser_repeats=args.parser_repeats,
        latency_requests=args.latency_requests,
        with_tailoring=args.with_tailoring,
        tailoring_buckets=tuple(args.tailoring_buckets),
    )
    print(f"-- run_all complete in {out['wall_seconds']}s --")
    print(f"  JSON     : {out['written_to']['json']}")
    print(f"  Markdown : {out['written_to']['markdown']}")
    if out['written_to']['docs']:
        print(f"  Docs     : {out['written_to']['docs']}")
    else:
        print("  Docs     : (docs/benchmarks.md not found or missing autogen markers)")
