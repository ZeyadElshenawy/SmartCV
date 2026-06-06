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

from benchmarks._io import (
    FIXTURES_DIR, REPO_ROOT, cohens_d, summary, write_section,
    append_partial, completed_keys, assemble_rows, clear_partial, partial_path,
)
from profiles.services.llm_engine import AllGroqKeysExhausted
from analysis.services.gap_analyzer import compute_gap_analysis
from profiles.services.cv_parser import parse_cv


def _profile_from_parsed(parsed: dict) -> types.SimpleNamespace:
    """Build a duck-typed profile that the gap analyzer reads like a real
    UserProfile.

    CRITICAL: the gap analyzer's grounding validator reads
    ``profile.data_content`` (analysis/services/gap_analyzer.py), exactly as a
    production UserProfile stores the whole parsed CV in its data_content JSONB
    (profiles/models.py: ``profile.skills`` == ``data_content['skills']``, etc.).
    The earlier stub left data_content WITHOUT skills/experiences/projects, so
    grounding saw an empty profile and demoted every matched skill — collapsing
    benchmark separation while production (full data_content) was fine. We now
    mirror production: data_content carries the full parsed profile, with
    experience/project bullets folded into ``description`` (the field
    ``_grounding_prose_corpus`` reads — parse_cv emits them as
    ``responsibilities``), plus the github/scholar/kaggle signal stubs the
    analyzer's signal readers expect.
    """
    def _norm_section(items):
        out = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            d = dict(it)
            if not d.get("description"):
                d["description"] = it.get("responsibilities") or it.get("bullets") or []
            out.append(d)
        return out

    skills = parsed.get("skills") or []
    experiences = _norm_section(parsed.get("experiences"))
    projects = _norm_section(parsed.get("projects"))
    certifications = parsed.get("certifications") or []
    education = parsed.get("education") or []
    summary = parsed.get("professional_summary") or parsed.get("summary") or ""
    data_content = {
        "skills": skills,
        "experiences": experiences,
        "projects": projects,
        "certifications": certifications,
        "education": education,
        "professional_summary": summary,
        "github_signals": {},
        "scholar_signals": {},
        "kaggle_signals": {},
    }
    return types.SimpleNamespace(
        skills=skills,
        experiences=experiences,
        projects=projects,
        certifications=certifications,
        education=education,
        data_content=data_content,
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


def run(repeats: int = 1, max_pairs: int | None = None, sleep: float = 0.0) -> dict:
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

    # Pair-iteration policy:
    # - JDs with `paired_cv_id` (auto-generated by jd_generator) only run on their
    #   diagonal pair — prevents 25x25 Cartesian explosion when the JD set grows.
    # - JDs without `paired_cv_id` (hand-curated JDs) run Cartesian over all CVs.
    cv_ids_in_manifest = {c["id"] for c in manifest["cvs"]}
    pairs: list[tuple[str, str, str]] = []
    for jd_id, jd in jds.items():
        paired_cv = jd.get("paired_cv_id")
        if paired_cv:
            if paired_cv not in cv_ids_in_manifest:
                continue  # orphan JD whose paired CV was removed
            label = manifest["expected_match_strength"].get(paired_cv, {}).get(jd_id, "strong")
            pairs.append((paired_cv, jd_id, label))
        else:
            for cv_meta in manifest["cvs"]:
                cv_id = cv_meta["id"]
                label = manifest["expected_match_strength"].get(cv_id, {}).get(jd_id, "weak")
                pairs.append((cv_id, jd_id, label))
    if max_pairs is not None:
        pairs = pairs[:max_pairs]

    name = "gap_eval"
    def _key(r):  # item key = (cv_id, jd_id)
        return (r.get("cv_id"), r.get("jd_id"))
    completed = completed_keys(name, _key)   # success-only; errored items retry
    started = time.perf_counter()

    try:
        for cv_id, jd_id, expected in pairs:
            if (cv_id, jd_id) in completed:
                continue  # already succeeded in a prior run
            parsed = parsed_cache.get(cv_id)
            if parsed is None:
                append_partial(name, {"cv_id": cv_id, "jd_id": jd_id, "expected": expected,
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
                except AllGroqKeysExhausted:
                    raise  # bubble to the clean-stop handler — NOT an error row
                except Exception as exc:  # noqa: BLE001
                    result = {}
                    err = f"{exc.__class__.__name__}: {exc}"
                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                if sleep > 0:
                    time.sleep(sleep)
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
                    # Raw tier-aware breakdown (name + proximity per skill) so a
                    # future scorer change can be re-measured OFFLINE with no
                    # Groq. Persisted on the post-grounding result (what the
                    # similarity_score was computed from).
                    "matched_must_have":    result.get("matched_must_have") or [],
                    "matched_nice_to_have": result.get("matched_nice_to_have") or [],
                    "missing_must_have":    result.get("missing_must_have") or [],
                    "missing_nice_to_have": result.get("missing_nice_to_have") or [],
                })

            scores = [r["similarity_score"] for r in runs if r["similarity_score"] is not None]
            append_partial(name, {
                "cv_id": cv_id,
                "jd_id": jd_id,
                "expected": expected,
                "similarity_mean": round(statistics.fmean(scores), 4) if scores else None,
                "similarity_std": round(statistics.pstdev(scores), 4) if len(scores) > 1 else 0.0,
                "runs": runs,
            })
    except AllGroqKeysExhausted as exc:
        done = len(completed_keys(name, _key))
        print(f"[gap_eval] STOPPED at {done}/{len(pairs)} — all Groq keys hit the daily "
              f"cap (task={getattr(exc, 'task', '?')}). Re-run the SAME command after the "
              f"cap resets to resume; completed items are saved and will be skipped.")
        return {"benchmark": name, "status": "partial_exhausted", "completed": done,
                "total": len(pairs), "partial_path": str(partial_path(name)), "resumable": True}

    # ---- All pairs done → rebuild the summary from the partial JSONL ----
    rows = assemble_rows(name, _key)
    bucket_scores: dict[str, list[float]] = {"strong": [], "partial": [], "weak": []}
    coverage_ratios: list[float] = []
    latencies_ms: list[float] = []
    for row in rows:
        exp = row.get("expected")
        for run in (row.get("runs") or []):
            lm = run.get("latency_ms")
            if lm is not None:
                latencies_ms.append(lm)
            cr = (run.get("coverage") or {}).get("coverage_ratio")
            if cr is not None:
                coverage_ratios.append(cr)
            ss = run.get("similarity_score")
            if ss is not None and exp in bucket_scores:
                bucket_scores[exp].append(float(ss))

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
    clear_partial(name)   # only AFTER the final write succeeds → fresh next run
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
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep after each compute_gap_analysis call to stay "
             "under Groq's 30k TPM cap. Default: 0.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    args = _parse_args()
    result = run(repeats=args.repeats, max_pairs=args.max_pairs, sleep=args.sleep)
    print(_format_report(result))
