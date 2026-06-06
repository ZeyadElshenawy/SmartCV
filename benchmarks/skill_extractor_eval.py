"""Skill extraction evaluation (Phase D2).

Runs ``jobs.services.skill_extractor.extract_skills`` against each fixture
JD and scores the LLM's output against the hand-curated ``expected_skills``
list in the fixture's JSON.

Metrics per job:
    precision = |extracted ∩ labeled| / |extracted|
    recall    = |extracted ∩ labeled| / |labeled|
    f1        = harmonic mean
    hallucination_rate = |extracted \\ labeled| / |extracted|

Synonym normalization mirrors the gap analyzer: the canonical
``skills_match`` (alias table + trailing-noun strip + ``difflib >= 0.85`` on
the canonical forms) catches ``react.js`` ↔ ``react``. On top of that, an
eval-side generic-qualifier strip + grouping split fixes the verbose-phrasing
double-penalty (``Tailwind CSS`` ↔ ``Tailwind``, ``npm/yarn`` ↔ ``npm``)
without crediting absent skills.

The eval is run ``--repeats`` times per JD (default 3) so we can disclose
LLM stochasticity. Outputs aggregate mean + std across runs.

Run:
    python -m benchmarks.skill_extractor_eval
    python -m benchmarks.skill_extractor_eval --repeats 1   # quick smoke
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from pathlib import Path
from typing import Iterable

from benchmarks._io import (
    FIXTURES_DIR, summary, write_section,
    append_partial, completed_keys, assemble_rows, clear_partial, partial_path,
)
from profiles.services.llm_engine import AllGroqKeysExhausted
from jobs.services.skill_extractor import (
    extract_skills, skills_match, _canonicalize_skill,
)

FUZZY_CUTOFF = 0.85

# --- Scoring matcher -------------------------------------------------------
# The eval scores extractor output against hand-curated gold labels. Raw
# difflib at 0.85 double-PENALIZED verbose/grouped phrasing: the tier-split
# extractor (de59b38) emits "Tailwind CSS" / "REST API design" / "Linux
# systems" / "npm/yarn" while gold holds the canonical "Tailwind" / "REST API"
# / "Linux" / "npm". difflib("tailwind css","tailwind")=0.80 < 0.85, so the
# SAME real skill counted as BOTH a hallucination (extra) AND a miss.
#
# We now match via the PRODUCTION canonical matcher (skills_match, the same one
# fix B added), plus a scoring-side canonicalization that strips trailing
# GENERIC-QUALIFIER nouns and splits slash/"and" groupings. The qualifier set
# is curated to category words that pad a skill phrase WITHOUT creating a
# distinct skill; it deliberately EXCLUDES skill-distinguishing modifiers
# ("native", "script", "server", ...) so "React Native" != "React". This
# credits variant phrasing of a REAL match only — it never credits an absent
# skill (a genuine recall miss stays a miss) or a true hallucination.
_EVAL_QUALIFIER_NOUNS = {
    "css", "systems", "system", "design", "workflow", "workflows",
    "service", "services", "chart", "charts", "gitops", "framework",
    "frameworks", "library", "libraries", "tooling", "pipeline", "pipelines",
    "client", "clients",
}
_EVAL_SPLIT = re.compile(r"[\\/&+,]| and ")


def _normalize(s: str) -> str:
    return (s or "").lower().strip()


def _eval_canon(s: str) -> str:
    """Production canonical form, then strip trailing generic-qualifier nouns.

    "Tailwind CSS" -> "tailwind"; "REST API design" -> "rest api";
    "Linux systems" -> "linux". Skill-distinguishing modifiers are NOT in the
    set, so "React Native" stays "react native" (!= "react").
    """
    c = _canonicalize_skill(s).strip().lower()
    parts = c.split()
    while len(parts) >= 2 and parts[-1] in _EVAL_QUALIFIER_NOUNS:
        parts = parts[:-1]
    return " ".join(parts)


def _eval_atoms(s: str) -> list[str]:
    """Split a grouped phrase ("npm/yarn", "REST and GraphQL") into atoms."""
    return [p.strip() for p in _EVAL_SPLIT.split(s or "") if p.strip()]


def _skill_equiv(a: str, b: str) -> bool:
    """True iff two skill names denote the same skill, for SCORING.

    Layered, each strictly more permissive but still phantom-safe:
      1. skills_match — production canonical (alias + trailing-noun strip) +
         difflib>=0.85 on the canonical forms (catches Vue.js<->Vue etc.);
      2. equal eval-canonical forms (strips css/design/systems/... qualifiers
         that the production strip set doesn't — Tailwind CSS<->Tailwind);
      3. grouping: any atom of a ("npm" in "npm/yarn") matches any atom of b
         under (1) or (2).
    """
    if not a or not b:
        return False
    if skills_match(a, b):
        return True
    ea, eb = _eval_canon(a), _eval_canon(b)
    if ea and ea == eb:
        return True
    atoms_a, atoms_b = _eval_atoms(a), _eval_atoms(b)
    if len(atoms_a) > 1 or len(atoms_b) > 1:
        for pa in atoms_a:
            for pb in atoms_b:
                if skills_match(pa, pb):
                    return True
                cpa, cpb = _eval_canon(pa), _eval_canon(pb)
                if cpa and cpa == cpb:
                    return True
    return False


def _matches_any(needle: str, haystack: Iterable[str]) -> bool:
    """True if ``needle`` denotes the same skill as any string in ``haystack``.

    Uses the canonical/variant-aware matcher (``_skill_equiv``), NOT raw
    difflib, so verbose phrasing of a real skill is neither a miss nor a
    hallucination. Absent skills and true hallucinations still fail to match.
    """
    if not _normalize(needle):
        return False
    return any(_skill_equiv(needle, h) for h in haystack if _normalize(h))


def _score(extracted: list[str], labeled: list[str]) -> dict:
    """Per-run precision/recall/F1/hallucination, with fuzzy synonym tolerance."""
    matched_extracted: list[str] = []
    for e in extracted:
        if _matches_any(e, labeled):
            matched_extracted.append(e)
    matched_labeled: list[str] = []
    for l in labeled:
        if _matches_any(l, extracted):
            matched_labeled.append(l)

    tp_extracted = len(matched_extracted)
    tp_labeled = len(matched_labeled)
    n_extracted = len(extracted)
    n_labeled = len(labeled)

    precision = tp_extracted / n_extracted if n_extracted else 0.0
    recall = tp_labeled / n_labeled if n_labeled else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    hallucination = (n_extracted - tp_extracted) / n_extracted if n_extracted else 0.0

    missed = [l for l in labeled if not _matches_any(l, extracted)]
    extras = [e for e in extracted if not _matches_any(e, labeled)]

    return {
        "n_extracted": n_extracted,
        "n_labeled": n_labeled,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "hallucination_rate": round(hallucination, 4),
        "missed": missed,
        "extra": extras,
    }


def _load_jds() -> list[dict]:
    manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for ref in manifest["jobs"]:
        out.append(json.loads((FIXTURES_DIR / ref["file"]).read_text(encoding="utf-8")))
    return out


def run(repeats: int = 3, sleep: float = 0.0) -> dict:
    jds = _load_jds()
    name = "skill_extractor_eval"
    def _key(r):  # item key = jd_id
        return r.get("jd_id")
    completed = completed_keys(name, _key)
    started = time.perf_counter()

    try:
        for jd in jds:
            if jd["id"] in completed:
                continue
            runs = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                try:
                    extracted = extract_skills(jd["description"])
                    err = None
                except AllGroqKeysExhausted:
                    raise
                except Exception as exc:  # noqa: BLE001
                    extracted = []
                    err = f"{exc.__class__.__name__}: {exc}"
                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                if sleep > 0:
                    time.sleep(sleep)
                scored = _score(extracted, jd["expected_skills"])
                scored["extracted_raw"] = extracted
                scored["latency_ms"] = elapsed_ms
                scored["error"] = err
                runs.append(scored)

            f1s = [r["f1"] for r in runs]
            ps = [r["precision"] for r in runs]
            rs = [r["recall"] for r in runs]
            hs = [r["hallucination_rate"] for r in runs]
            append_partial(name, {
                "jd_id": jd["id"],
                "title": jd["title"],
                "expected_skills_count": len(jd["expected_skills"]),
                "repeats": repeats,
                "f1_mean": round(statistics.fmean(f1s), 4) if f1s else None,
                "f1_std": round(statistics.pstdev(f1s), 4) if len(f1s) > 1 else 0.0,
                "precision_mean": round(statistics.fmean(ps), 4) if ps else None,
                "recall_mean": round(statistics.fmean(rs), 4) if rs else None,
                "hallucination_mean": round(statistics.fmean(hs), 4) if hs else None,
                "runs": runs,
            })
    except AllGroqKeysExhausted as exc:
        done = len(completed_keys(name, _key))
        print(f"[skill_extractor_eval] STOPPED at {done}/{len(jds)} — all Groq keys hit "
              f"the daily cap (task={getattr(exc, 'task', '?')}). Re-run the SAME command "
              f"after the cap resets to resume.")
        return {"benchmark": name, "status": "partial_exhausted", "completed": done,
                "total": len(jds), "partial_path": str(partial_path(name)), "resumable": True}

    # ---- All JDs done → rebuild the summary from the partial JSONL ----
    per_job = assemble_rows(name, _key)
    all_f1: list[float] = []
    all_precision: list[float] = []
    all_recall: list[float] = []
    all_hallucination: list[float] = []
    all_latency_ms: list[float] = []
    for row in per_job:
        for run in (row.get("runs") or []):
            all_f1.append(run.get("f1"))
            all_precision.append(run.get("precision"))
            all_recall.append(run.get("recall"))
            all_hallucination.append(run.get("hallucination_rate"))
            lm = run.get("latency_ms")
            if lm is not None:
                all_latency_ms.append(lm)
    all_f1 = [v for v in all_f1 if v is not None]
    all_precision = [v for v in all_precision if v is not None]
    all_recall = [v for v in all_recall if v is not None]
    all_hallucination = [v for v in all_hallucination if v is not None]

    payload = {
        "benchmark": "skill_extractor_eval",
        "version": 1,
        "fixture_kind": "real_anonymized_jd",
        "n_jds": len(jds),
        "repeats_per_jd": repeats,
        "wall_seconds": round(time.perf_counter() - started, 2),
        "aggregate": {
            "f1": summary(all_f1),
            "precision": summary(all_precision),
            "recall": summary(all_recall),
            "hallucination_rate": summary(all_hallucination),
            "latency_ms": summary(all_latency_ms),
        },
        "per_job": per_job,
        "method": {
            "fuzzy_cutoff": FUZZY_CUTOFF,
            "service": "jobs.services.skill_extractor.extract_skills",
            "scoring": ("canonical skills_match (alias + trailing-noun strip + "
                        "difflib>=0.85 on canonical forms) + eval-side "
                        "generic-qualifier strip + slash/and grouping split"),
        },
        "disclosure": (
            f"LLM-driven metric — Groq llama-4-scout, temperature=0.0 in the "
            f"service call. Mean of {repeats} run(s) per JD. Variance shown as "
            f"std. Skills compared via the canonical skills_match (alias + "
            f"trailing-noun strip + difflib >= {FUZZY_CUTOFF} on canonical "
            f"forms; matches gap-analyzer) plus an eval-side generic-qualifier "
            f"strip + grouping split so verbose phrasing of a real skill is "
            f"not double-penalized as both a miss and a hallucination."
        ),
    }
    out_path = write_section("skill_extractor_eval", payload)
    payload["written_to"] = str(out_path)
    clear_partial(name)
    return payload


def _format_report(payload: dict) -> str:
    agg = payload["aggregate"]
    lines = [
        "-- Skill extraction (Phase D2) --",
        f"  N JDs        : {payload['n_jds']}  (x{payload['repeats_per_jd']} runs each)",
        f"  Wall         : {payload['wall_seconds']}s",
        f"  Aggregate    : F1={agg['f1']['mean']}  "
        f"P={agg['precision']['mean']}  R={agg['recall']['mean']}  "
        f"halluc={agg['hallucination_rate']['mean']}  "
        f"latency={agg['latency_ms']['median']}ms (median)",
        "  Per JD:",
    ]
    for j in payload["per_job"]:
        lines.append(
            f"    {j['jd_id']:32s}  "
            f"F1={j['f1_mean']:.3f}+/-{j['f1_std']:.3f}  "
            f"P={j['precision_mean']:.3f}  R={j['recall_mean']:.3f}  "
            f"halluc={j['hallucination_mean']:.3f}"
        )
    lines.append(f"  Written to   : {payload['written_to']}")
    return "\n".join(lines)


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartCV skill extraction benchmark")
    parser.add_argument("--repeats", type=int, default=3, help="runs per JD (default: 3)")
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep after each extract_skills call to stay under "
             "Groq's 30k TPM cap. Default: 0.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    args = _parse_args()
    result = run(repeats=args.repeats, sleep=args.sleep)
    print(_format_report(result))
