"""Gap-analysis evaluation (Phase D3).

Runs ``analysis.services.gap_analyzer.compute_gap_analysis`` against every
(CV, JD) pair in the fixture suite and validates two things:

1. **Coverage** — every JD skill should land in matched / missing / partial
   (this is the Phase 2 reconciliation invariant the analyzer claims).
2. **Separation** — pairs hand-labeled ``strong`` in the manifest should
   produce higher similarity scores than pairs labeled ``weak``. Reports
   per-bucket mean ± std and Cohen's d (strong vs weak).

Per-skill F1 against gold-labeled categorizations is intentionally NOT
attempted: hand-labeling 50 (CV, JD) pair categorizations would be huge
manual work and the bulk separation signal is what actually matters for
the README's "is this a useful gap analyzer?" claim.

Each (CV, JD) pair is run ``--repeats`` times (default 1) to expose LLM
stochasticity. Costs: 10 CVs * 5 JDs * 1 repeat = 50 LLM calls (~2s each
plus 10 parse_cv calls).

Run:
    python -m benchmarks.gap_eval                  # 1 repeat per pair
    python -m benchmarks.gap_eval --repeats 3      # full stochasticity disclosure
    python -m benchmarks.gap_eval --max-pairs 10   # quick smoke
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import types
from typing import Iterable

from benchmarks._io import FIXTURES_DIR, REPO_ROOT, cohens_d, summary, write_section
from analysis.services.gap_analyzer import compute_gap_analysis
from profiles.services.cv_parser import parse_cv


def _profile_from_parsed(parsed: dict) -> types.SimpleNamespace:
    """Build a duck-typed profile that the gap analyzer can read like a UserProfile."""
    return types.SimpleNamespace(
        skills=parsed.get("skills") or [],
        experiences=parsed.get("experiences") or [],
        projects=parsed.get("projects") or [],
        certifications=parsed.get("certifications") or [],
        education=parsed.get("education") or [],
        data_content={
            "github_signals": {},
            "scholar_signals": {},
            "kaggle_signals": {},
        },
    )


def _job_stub(jd: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        title=jd["title"],
        company=jd["company"],
        extracted_skills=jd["expected_skills"],
    )


def _load_manifest() -> dict:
    return json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))


def _load_jds(manifest: dict) -> dict[str, dict]:
    out = {}
    for ref in manifest["jobs"]:
        jd = json.loads((FIXTURES_DIR / ref["file"]).read_text(encoding="utf-8"))
        out[jd["id"]] = jd
    return out


def _coverage(result: dict, jd_skills: list[str]) -> dict:
    """How many of the JD's skills landed in any of the three buckets?"""
    matched = {s.get("name") if isinstance(s, dict) else str(s)
               for s in (result.get("matched_skills") or [])}
    missing = {s.get("name") if isinstance(s, dict) else str(s)
               for s in (result.get("missing_skills") or [])}
    partial = {s.get("name") if isinstance(s, dict) else str(s)
               for s in (result.get("partial_skills") or [])}
    accounted = matched | missing | partial

    job_set = {s.lower().strip() for s in jd_skills}
    accounted_lc = {s.lower().strip() for s in accounted if s}
    covered = job_set & accounted_lc
    return {
        "n_job_skills": len(job_set),
        "n_accounted": len(accounted_lc),
        "n_covered_of_job": len(covered),
        "coverage_ratio": round(len(covered) / len(job_set), 4) if job_set else 0.0,
    }


def run(repeats: int = 1, max_pairs: int | None = None) -> dict:
    manifest = _load_manifest()
    jds = _load_jds(manifest)

    # Parse all CVs once (each is a Groq + heuristic call) — share across JDs.
    parsed_cache: dict[str, dict] = {}
    parse_started = time.perf_counter()
    parse_errors: list[dict] = []
    for cv_meta in manifest["cvs"]:
        cv_id = cv_meta["id"]
        cv_path = REPO_ROOT / cv_meta["path"]
        try:
            parsed_cache[cv_id] = parse_cv(str(cv_path))
        except Exception as exc:  # noqa: BLE001
            parse_errors.append({"cv_id": cv_id, "error": f"{exc.__class__.__name__}: {exc}"})
            parsed_cache[cv_id] = None
    parse_seconds = round(time.perf_counter() - parse_started, 2)

    pairs: list[tuple[str, str, str]] = []
    for cv_meta in manifest["cvs"]:
        cv_id = cv_meta["id"]
        for jd_id, _ in jds.items():
            label = manifest["expected_match_strength"].get(cv_id, {}).get(jd_id, "weak")
            pairs.append((cv_id, jd_id, label))
    if max_pairs is not None:
        pairs = pairs[:max_pairs]

    rows: list[dict] = []
    bucket_scores: dict[str, list[float]] = {"strong": [], "partial": [], "weak": []}
    coverage_ratios: list[float] = []
    latencies_ms: list[float] = []
    started = time.perf_counter()

    for cv_id, jd_id, expected in pairs:
        parsed = parsed_cache.get(cv_id)
        if parsed is None:
            rows.append({"cv_id": cv_id, "jd_id": jd_id, "expected": expected,
                         "error": "parse_failed", "scores": []})
            continue
        profile = _profile_from_parsed(parsed)
        job = _job_stub(jds[jd_id])

        runs: list[dict] = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            try:
                result = compute_gap_analysis(profile, job)
                err = None
            except Exception as exc:  # noqa: BLE001
                result = {}
                err = f"{exc.__class__.__name__}: {exc}"
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            cov = _coverage(result, jds[jd_id]["expected_skills"])
            runs.append({
                "similarity_score": result.get("similarity_score"),
                "n_matched": len(result.get("matched_skills") or []),
                "n_missing": len(result.get("missing_skills") or []),
                "n_partial": len(result.get("partial_skills") or []),
                "analysis_method": result.get("analysis_method"),
                "coverage": cov,
                "latency_ms": elapsed_ms,
                "error": err,
            })
            latencies_ms.append(elapsed_ms)
            if cov.get("coverage_ratio") is not None:
                coverage_ratios.append(cov["coverage_ratio"])
            ss = result.get("similarity_score")
            if ss is not None and expected in bucket_scores:
                bucket_scores[expected].append(float(ss))

        scores = [r["similarity_score"] for r in runs if r["similarity_score"] is not None]
        rows.append({
            "cv_id": cv_id,
            "jd_id": jd_id,
            "expected": expected,
            "similarity_mean": round(statistics.fmean(scores), 4) if scores else None,
            "similarity_std": round(statistics.pstdev(scores), 4) if len(scores) > 1 else 0.0,
            "runs": runs,
        })

    payload = {
        "benchmark": "gap_eval",
        "version": 1,
        "fixture_kind": "real_anonymized_cv_x_jd",
        "n_cvs": len(manifest["cvs"]),
        "n_jds": len(jds),
        "n_pairs_evaluated": len(rows),
        "repeats_per_pair": repeats,
        "parse_wall_seconds": parse_seconds,
        "wall_seconds": round(time.perf_counter() - started, 2),
        "parse_errors": parse_errors,
        "buckets": {
            label: {
                **summary(scores),
                "scores": scores,
            }
            for label, scores in bucket_scores.items()
        },
        "separation": {
            "strong_vs_weak_cohens_d": cohens_d(bucket_scores["strong"], bucket_scores["weak"]),
            "strong_mean_gt_weak_mean": (
                (statistics.fmean(bucket_scores["strong"]) if bucket_scores["strong"] else 0)
                > (statistics.fmean(bucket_scores["weak"]) if bucket_scores["weak"] else 0)
            ),
        },
        "coverage": {
            **summary(coverage_ratios),
            "perfect_coverage_pairs": sum(1 for c in coverage_ratios if c >= 0.999),
        },
        "latency_ms": summary(latencies_ms),
        "rows": rows,
        "method": {
            "service": "analysis.services.gap_analyzer.compute_gap_analysis",
            "buckets_source": "fixtures/manifest.json -> expected_match_strength",
        },
        "disclosure": (
            f"LLM-driven (Groq llama-4-scout). Mean of {repeats} run(s) per pair. "
            "Per-skill ground-truth labels not provided — separation is measured "
            "by similarity_score across hand-graded strong/partial/weak buckets, "
            "and coverage validates the analyzer's '100% reconciliation' claim."
        ),
    }
    out_path = write_section("gap_eval", payload)
    payload["written_to"] = str(out_path)
    return payload


def _format_report(payload: dict) -> str:
    b = payload["buckets"]
    sep = payload["separation"]
    cov = payload["coverage"]
    lines = [
        "-- Gap analysis (Phase D3) --",
        f"  Pairs        : {payload['n_pairs_evaluated']}  "
        f"({payload['n_cvs']} CVs x {payload['n_jds']} JDs, x{payload['repeats_per_pair']} runs)",
        f"  Parse wall   : {payload['parse_wall_seconds']}s   "
        f"Gap wall   : {payload['wall_seconds']}s",
        "  Similarity by expected bucket:",
        f"    strong   n={b['strong']['n']:>3}  mean={b['strong']['mean']}  std={b['strong']['std']}",
        f"    partial  n={b['partial']['n']:>3}  mean={b['partial']['mean']}  std={b['partial']['std']}",
        f"    weak     n={b['weak']['n']:>3}  mean={b['weak']['mean']}  std={b['weak']['std']}",
        f"  Separation   : strong > weak? {sep['strong_mean_gt_weak_mean']}  "
        f"d(strong,weak)={sep['strong_vs_weak_cohens_d']}",
        f"  Coverage     : mean={cov['mean']}  perfect={cov['perfect_coverage_pairs']}/{cov['n']}",
        f"  Latency      : median={payload['latency_ms']['median']}ms  "
        f"p95={payload['latency_ms']['p95']}ms",
        f"  Written to   : {payload['written_to']}",
    ]
    return "\n".join(lines)


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartCV gap-analysis benchmark")
    parser.add_argument("--repeats", type=int, default=1, help="runs per (CV,JD) pair (default: 1)")
    parser.add_argument("--max-pairs", type=int, default=None,
                        help="cap on (CV,JD) pairs evaluated (default: all)")
    return parser.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    args = _parse_args()
    result = run(repeats=args.repeats, max_pairs=args.max_pairs)
    print(_format_report(result))
