"""Resume-tailoring quality evaluation (Phase D5).

Pipeline per (CV, JD) pair:
    1. Parse CV via ``profiles.services.cv_parser.parse_cv``.
    2. Run gap analysis via ``analysis.services.gap_analyzer.compute_gap_analysis``.
    3. Generate a tailored resume via ``resumes.services.resume_generator.generate_resume_content``.
    4. Score the result with the 4-axis LLM judge in ``benchmarks/llm_judge.py``
       (factuality / relevance / ats_fit / human_voice, 1-10).
    5. Programmatic factuality pre-check: every company / school in the
       generated resume must appear verbatim in the source CV text.
    6. Programmatic voice check: count banned-phrase hits.

Default scope: the pairs labeled ``strong`` in
``benchmarks/fixtures/manifest.json`` (typically 8-12). That keeps the
LLM-call budget bounded (~3 calls per pair: gap, generate, judge).

Run:
    python -m benchmarks.tailoring_eval
    python -m benchmarks.tailoring_eval --max-pairs 3   # quick smoke
    python -m benchmarks.tailoring_eval --buckets strong partial   # widen scope
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import types
from typing import Iterable

from benchmarks._io import FIXTURES_DIR, REPO_ROOT, summary, write_section
from benchmarks.llm_judge import banned_phrase_hits, factuality_check, judge
from analysis.services.gap_analyzer import compute_gap_analysis
from profiles.services.cv_parser import parse_cv
from resumes.services.resume_generator import generate_resume_content


def _profile_from_parsed(parsed: dict) -> types.SimpleNamespace:
    """Duck-typed UserProfile-like for the gap analyzer + generator."""
    # Build a data_content blob that the generator pulls full CV from.
    data_content = {
        "skills": parsed.get("skills") or [],
        "experiences": parsed.get("experiences") or [],
        "education": parsed.get("education") or [],
        "projects": parsed.get("projects") or [],
        "certifications": parsed.get("certifications") or [],
        "github_signals": {},
        "scholar_signals": {},
        "kaggle_signals": {},
    }
    return types.SimpleNamespace(
        skills=parsed.get("skills") or [],
        experiences=parsed.get("experiences") or [],
        projects=parsed.get("projects") or [],
        certifications=parsed.get("certifications") or [],
        education=parsed.get("education") or [],
        data_content=data_content,
        raw_text=parsed.get("raw_text") or "",
    )


def _job_stub(jd: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        title=jd["title"],
        company=jd["company"],
        description=jd["description"],
        extracted_skills=jd["expected_skills"],
    )


def _gap_stub(gap_result: dict) -> types.SimpleNamespace:
    """The generator reads gap_analysis.matched_skills as an attribute."""
    raw = gap_result.get("matched_skills") or []
    names = [s.get("name") if isinstance(s, dict) else str(s) for s in raw]
    return types.SimpleNamespace(matched_skills=[n for n in names if n])


def _select_pairs(manifest: dict, jds: dict[str, dict], buckets: tuple[str, ...]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    bucket_set = set(buckets)
    for cv_meta in manifest["cvs"]:
        cv_id = cv_meta["id"]
        for jd_id in jds:
            label = manifest["expected_match_strength"].get(cv_id, {}).get(jd_id, "weak")
            if label in bucket_set:
                out.append((cv_id, jd_id, label))
    return out


def _load() -> tuple[dict, dict[str, dict]]:
    manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))
    jds: dict[str, dict] = {}
    for ref in manifest["jobs"]:
        jd = json.loads((FIXTURES_DIR / ref["file"]).read_text(encoding="utf-8"))
        jds[jd["id"]] = jd
    return manifest, jds


def run(buckets: tuple[str, ...] = ("strong",), max_pairs: int | None = None) -> dict:
    manifest, jds = _load()
    pairs = _select_pairs(manifest, jds, buckets)
    if max_pairs is not None:
        pairs = pairs[:max_pairs]

    parsed_cache: dict[str, dict] = {}
    rows: list[dict] = []
    fact_scores: list[int] = []
    rel_scores: list[int] = []
    ats_scores: list[int] = []
    voice_scores: list[int] = []
    grounded_ratios: list[float] = []
    voice_hit_counts: list[int] = []
    started = time.perf_counter()
    parse_started = started

    # Parse CVs lazily (only the ones referenced in selected pairs).
    referenced_cv_ids = {cv_id for cv_id, _, _ in pairs}
    for cv_meta in manifest["cvs"]:
        if cv_meta["id"] not in referenced_cv_ids:
            continue
        try:
            parsed_cache[cv_meta["id"]] = parse_cv(str(REPO_ROOT / cv_meta["path"]))
        except Exception as exc:  # noqa: BLE001
            parsed_cache[cv_meta["id"]] = {"_error": f"{exc.__class__.__name__}: {exc}"}
    parse_seconds = round(time.perf_counter() - parse_started, 2)

    eval_started = time.perf_counter()
    for cv_id, jd_id, bucket in pairs:
        parsed = parsed_cache.get(cv_id) or {}
        if parsed.get("_error"):
            rows.append({"cv_id": cv_id, "jd_id": jd_id, "bucket": bucket,
                         "error": parsed["_error"]})
            continue
        profile = _profile_from_parsed(parsed)
        job = _job_stub(jds[jd_id])

        try:
            gap_result = compute_gap_analysis(profile, job)
        except Exception as exc:  # noqa: BLE001
            rows.append({"cv_id": cv_id, "jd_id": jd_id, "bucket": bucket,
                         "stage": "gap_analysis", "error": f"{exc.__class__.__name__}: {exc}"})
            continue

        try:
            generated = generate_resume_content(profile, job, _gap_stub(gap_result))
        except Exception as exc:  # noqa: BLE001
            rows.append({"cv_id": cv_id, "jd_id": jd_id, "bucket": bucket,
                         "stage": "generate", "error": f"{exc.__class__.__name__}: {exc}"})
            continue

        # Programmatic checks. `confirmed_projects` lets enriched projects
        # ground via their source_url / source_id even when they're not in
        # the parsed CV text — same path real users hit after Phase 2's
        # project-review confirm step.
        confirmed_projects = (profile.data_content or {}).get("projects") or []
        prog_fact = factuality_check(generated, profile.raw_text, confirmed_projects=confirmed_projects)
        voice_hits = banned_phrase_hits(generated)

        # LLM judge
        try:
            verdict = judge(
                source_cv={k: v for k, v in parsed.items() if k != "raw_text"},
                job_title=job.title,
                job_company=job.company,
                job_skills=job.extracted_skills,
                job_description=job.description,
                generated_resume=generated,
            )
            verdict_dict = verdict.model_dump()
        except Exception as exc:  # noqa: BLE001
            rows.append({"cv_id": cv_id, "jd_id": jd_id, "bucket": bucket,
                         "stage": "judge", "error": f"{exc.__class__.__name__}: {exc}",
                         "programmatic_factuality": prog_fact,
                         "voice_hits": voice_hits})
            continue

        rows.append({
            "cv_id": cv_id,
            "jd_id": jd_id,
            "bucket": bucket,
            "judge": verdict_dict,
            "programmatic_factuality": prog_fact,
            "voice_hits": voice_hits,
            "voice_hit_count": len(voice_hits),
        })
        fact_scores.append(verdict_dict["factuality"]["score"])
        rel_scores.append(verdict_dict["relevance"]["score"])
        ats_scores.append(verdict_dict["ats_fit"]["score"])
        voice_scores.append(verdict_dict["human_voice"]["score"])
        if prog_fact.get("ratio") is not None:
            grounded_ratios.append(prog_fact["ratio"])
        voice_hit_counts.append(len(voice_hits))

    payload = {
        "benchmark": "tailoring_eval",
        "version": 1,
        "fixture_kind": "real_anonymized_cv_x_jd",
        "buckets_evaluated": list(buckets),
        "n_pairs": len(rows),
        "parse_wall_seconds": parse_seconds,
        "eval_wall_seconds": round(time.perf_counter() - eval_started, 2),
        "wall_seconds": round(time.perf_counter() - started, 2),
        "axes": {
            "factuality": summary(fact_scores),
            "relevance": summary(rel_scores),
            "ats_fit": summary(ats_scores),
            "human_voice": summary(voice_scores),
        },
        "programmatic": {
            "entity_grounding_ratio": summary(grounded_ratios),
            "banned_voice_hits_per_resume": summary(voice_hit_counts),
        },
        "rows": rows,
        "method": {
            "generator": "resumes.services.resume_generator.generate_resume_content",
            "judge_module": "benchmarks.llm_judge",
            "judge_axes": ["factuality", "relevance", "ats_fit", "human_voice"],
            "judge_scale": "1-10 per axis",
        },
        "disclosure": (
            "LLM-judged metric — both the resume generator and the judge are "
            "Groq llama-4-scout. Judge temperature=0.0 so its scores are "
            "near-deterministic; the generator runs at the production temp "
            "(0.7 default) so per-pair scores will vary across runs. "
            "Single judge model: not human-validated; treat absolute numbers "
            "as a smoke test, relative trends across pairs as more reliable. "
            "Programmatic entity-grounding and voice-hit checks are independent "
            "of the LLM judge and provide a non-LLM cross-check."
        ),
    }
    out_path = write_section("tailoring_eval", payload)
    payload["written_to"] = str(out_path)
    return payload


def _format_report(payload: dict) -> str:
    a = payload["axes"]
    p = payload["programmatic"]
    lines = [
        "-- Resume tailoring (Phase D5) --",
        f"  Pairs        : {payload['n_pairs']}  buckets={payload['buckets_evaluated']}",
        f"  Wall         : parse={payload['parse_wall_seconds']}s  "
        f"eval={payload['eval_wall_seconds']}s",
        "  Judge axes (1-10):",
        f"    factuality   mean={a['factuality']['mean']}  std={a['factuality']['std']}  n={a['factuality']['n']}",
        f"    relevance    mean={a['relevance']['mean']}  std={a['relevance']['std']}",
        f"    ats_fit      mean={a['ats_fit']['mean']}  std={a['ats_fit']['std']}",
        f"    human_voice  mean={a['human_voice']['mean']}  std={a['human_voice']['std']}",
        "  Programmatic checks:",
        f"    entity grounding mean={p['entity_grounding_ratio']['mean']} "
        f"(min={p['entity_grounding_ratio']['min']})",
        f"    banned-voice hits per resume mean={p['banned_voice_hits_per_resume']['mean']} "
        f"(max={p['banned_voice_hits_per_resume']['max']})",
        f"  Written to   : {payload['written_to']}",
    ]
    return "\n".join(lines)


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SmartCV resume tailoring benchmark")
    p.add_argument("--buckets", nargs="+", default=["strong"],
                   choices=["strong", "partial", "weak"],
                   help="which manifest buckets to evaluate (default: strong only)")
    p.add_argument("--max-pairs", type=int, default=None)
    return p.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    args = _parse_args()
    out = run(buckets=tuple(args.buckets), max_pairs=args.max_pairs)
    print(_format_report(out))
