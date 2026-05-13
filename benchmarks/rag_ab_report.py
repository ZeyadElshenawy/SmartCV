"""RAG A/B paired-delta report for the §5 RAG evaluation.

Takes two snapshots written by `benchmarks.tailoring_eval_ab` (baseline T0
and a treatment T1/T2/T3), pairs rows by (cv_id, jd_id), and computes:

  Per axis (factuality / relevance / ats_fit / human_voice):
    - mean delta (paired, treatment − baseline)
    - median delta
    - Cohen's d effect size (paired)
    - Wilcoxon signed-rank p-value (one-sided when direction is fixed)
    - 95% bootstrap CI on the mean delta

  Programmatic:
    - voice_hit_count: mean delta, percent reduction
    - entity grounding ratio: mean delta (regression check)

Pass/fail table against §5.3 thresholds:
  human_voice Δ ≥ +1.5, voice_hits Δ ≥ −80% relative, factuality Δ ≥ −0.5,
  grounded_ratio Δ ≥ −0.05.

Outputs a markdown summary block ready to paste into benchmarks/CHANGELOG.md.

Usage:
    python -m benchmarks.rag_ab_report \\
        --baseline benchmarks/results/2026-05-13/tailoring_eval__T0.json \\
        --treatment benchmarks/results/2026-05-13/tailoring_eval__T2.json \\
        --out      benchmarks/results/2026-05-13/rag_ab_report__T0_vs_T2.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy import stats


AXES = ("factuality", "relevance", "ats_fit", "human_voice")

# §5.3 primary merge gates (T2 vs T0).
THRESHOLDS = {
    "human_voice_delta_min": 1.5,
    "voice_hits_pct_reduction_min": 0.80,
    "factuality_delta_min": -0.5,
    "grounded_ratio_delta_min": -0.05,
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pair_rows(baseline: dict, treatment: dict) -> list[dict]:
    """Pair rows on (cv_id, jd_id). Skips pairs missing on either side or
    that recorded an error in either run."""
    def key(r): return (r.get("cv_id"), r.get("jd_id"))
    b_idx = {key(r): r for r in baseline.get("rows", []) if "judge" in r}
    paired = []
    for r in treatment.get("rows", []):
        if "judge" not in r:
            continue
        b = b_idx.get(key(r))
        if not b:
            continue
        paired.append({
            "cv_id": r["cv_id"],
            "jd_id": r["jd_id"],
            "bucket": r.get("bucket"),
            "baseline": b,
            "treatment": r,
        })
    return paired


def _axis_pairs(paired: list[dict], axis: str) -> tuple[list[int], list[int]]:
    """Return (baseline_scores, treatment_scores) for the given judge axis."""
    base, treat = [], []
    for p in paired:
        bs = p["baseline"]["judge"].get(axis, {}).get("score")
        ts = p["treatment"]["judge"].get(axis, {}).get("score")
        if bs is None or ts is None:
            continue
        base.append(int(bs))
        treat.append(int(ts))
    return base, treat


def _voice_hits(paired: list[dict]) -> tuple[list[int], list[int]]:
    return (
        [int(p["baseline"].get("voice_hit_count") or 0) for p in paired],
        [int(p["treatment"].get("voice_hit_count") or 0) for p in paired],
    )


def _grounded(paired: list[dict]) -> tuple[list[float], list[float]]:
    """Filter to pairs where both runs have a grounded-ratio number."""
    base, treat = [], []
    for p in paired:
        b = (p["baseline"].get("programmatic_factuality") or {}).get("ratio")
        t = (p["treatment"].get("programmatic_factuality") or {}).get("ratio")
        if b is None or t is None:
            continue
        base.append(float(b))
        treat.append(float(t))
    return base, treat


# ---------- statistical helpers ----------

def _cohens_d_paired(base: list[float], treat: list[float]) -> float | None:
    """Paired Cohen's d = mean(diff) / std(diff)."""
    if len(base) < 2:
        return None
    diffs = np.asarray(treat, dtype=float) - np.asarray(base, dtype=float)
    sd = float(np.std(diffs, ddof=1))
    if sd == 0:
        return float("inf") if np.mean(diffs) != 0 else 0.0
    return round(float(np.mean(diffs) / sd), 4)


def _wilcoxon_p(base: list[float], treat: list[float], alternative: str = "two-sided") -> float | None:
    """Paired Wilcoxon signed-rank p-value. Skips when n<6 or all diffs zero
    (scipy raises on those)."""
    if len(base) < 6:
        return None
    diffs = np.asarray(treat, dtype=float) - np.asarray(base, dtype=float)
    if np.allclose(diffs, 0):
        return None
    try:
        res = stats.wilcoxon(diffs, alternative=alternative)
        return round(float(res.pvalue), 6)
    except ValueError:
        return None


def _bootstrap_ci(base: list[float], treat: list[float], n_resamples: int = 5000) -> tuple[float, float] | None:
    """95% percentile bootstrap CI on the paired mean delta."""
    if len(base) < 3:
        return None
    diffs = np.asarray(treat, dtype=float) - np.asarray(base, dtype=float)
    rng = np.random.default_rng(seed=0)  # deterministic CI across re-runs
    means = []
    n = len(diffs)
    for _ in range(n_resamples):
        sample = rng.choice(diffs, size=n, replace=True)
        means.append(sample.mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    return round(float(lo), 4), round(float(hi), 4)


def _axis_summary(base: list[int], treat: list[int]) -> dict:
    """Per-axis paired stats."""
    if not base:
        return {"n": 0}
    diffs = [t - b for b, t in zip(base, treat)]
    return {
        "n": len(base),
        "baseline_mean": round(statistics.mean(base), 4),
        "treatment_mean": round(statistics.mean(treat), 4),
        "mean_delta": round(statistics.mean(diffs), 4),
        "median_delta": round(statistics.median(diffs), 4),
        "cohens_d": _cohens_d_paired(base, treat),
        "wilcoxon_p_two_sided": _wilcoxon_p(base, treat, "two-sided"),
        "ci95": _bootstrap_ci(base, treat),
    }


def _voice_summary(base: list[int], treat: list[int]) -> dict:
    if not base:
        return {"n": 0}
    base_mean = statistics.mean(base)
    treat_mean = statistics.mean(treat)
    pct_reduction = None
    if base_mean > 0:
        pct_reduction = round(1.0 - (treat_mean / base_mean), 4)
    return {
        "n": len(base),
        "baseline_mean": round(base_mean, 4),
        "treatment_mean": round(treat_mean, 4),
        "mean_delta": round(treat_mean - base_mean, 4),
        "pct_reduction": pct_reduction,
        "wilcoxon_p_two_sided": _wilcoxon_p(base, treat, "two-sided"),
    }


def _gates(axis_stats: dict, voice_stats: dict, ground_stats: dict) -> list[dict]:
    """Evaluate §5.3 thresholds. Returns list of {name, target, actual, pass}."""
    out = []

    hv = axis_stats.get("human_voice", {}).get("mean_delta")
    out.append({
        "gate": "human_voice Δ ≥ +1.5",
        "target": THRESHOLDS["human_voice_delta_min"],
        "actual": hv,
        "pass": hv is not None and hv >= THRESHOLDS["human_voice_delta_min"],
    })

    vh = voice_stats.get("pct_reduction")
    out.append({
        "gate": "voice_hits relative reduction ≥ 80%",
        "target": THRESHOLDS["voice_hits_pct_reduction_min"],
        "actual": vh,
        "pass": vh is not None and vh >= THRESHOLDS["voice_hits_pct_reduction_min"],
    })

    fa = axis_stats.get("factuality", {}).get("mean_delta")
    out.append({
        "gate": "factuality Δ ≥ −0.5 (no regression)",
        "target": THRESHOLDS["factuality_delta_min"],
        "actual": fa,
        "pass": fa is not None and fa >= THRESHOLDS["factuality_delta_min"],
    })

    gr = ground_stats.get("mean_delta")
    out.append({
        "gate": "grounded_ratio Δ ≥ −0.05",
        "target": THRESHOLDS["grounded_ratio_delta_min"],
        "actual": gr,
        "pass": gr is not None and gr >= THRESHOLDS["grounded_ratio_delta_min"],
    })

    return out


# ---------- markdown rendering ----------

def _md(value, decimals: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if math.isinf(value):
            return "∞"
        return f"{value:.{decimals}f}"
    if isinstance(value, tuple):
        return f"[{_md(value[0], decimals)}, {_md(value[1], decimals)}]"
    return str(value)


def render_markdown(baseline: dict, treatment: dict, paired: list[dict],
                    axis_stats: dict, voice_stats: dict, ground_stats: dict,
                    gates: list[dict],
                    baseline_path: Path | None = None,
                    treatment_path: Path | None = None) -> str:
    b_label = baseline.get("treatment_label", "?")
    t_label = treatment.get("treatment_label", "?")
    b_cfg = baseline.get("treatment_config", {})
    t_cfg = treatment.get("treatment_config", {})
    b_name = baseline_path.name if baseline_path else baseline.get("written_to", "?")
    t_name = treatment_path.name if treatment_path else treatment.get("written_to", "?")

    lines = [
        f"# RAG A/B Report — {b_label} vs {t_label}",
        "",
        f"- **Baseline** ({b_label}): `{b_name}` · n_pairs={baseline.get('n_pairs')}",
        f"  - `RAG_ENABLED={b_cfg.get('RAG_ENABLED')}`, `BULLET_AUTOFIX={b_cfg.get('BULLET_AUTOFIX')}`, "
        f"`BULLET_RETRY={b_cfg.get('BULLET_RETRY')}`",
        f"- **Treatment** ({t_label}): `{t_name}` · n_pairs={treatment.get('n_pairs')}",
        f"  - `RAG_ENABLED={t_cfg.get('RAG_ENABLED')}`, `BULLET_AUTOFIX={t_cfg.get('BULLET_AUTOFIX')}`, "
        f"`BULLET_RETRY={t_cfg.get('BULLET_RETRY')}`",
        f"- **Paired pairs**: {len(paired)}",
        "",
        "## Merge gates (§5.3)",
        "",
        "| Gate | Target | Actual | Pass |",
        "|---|---|---|---|",
    ]
    for g in gates:
        lines.append(f"| {g['gate']} | {_md(g['target'])} | {_md(g['actual'])} | "
                     f"{'✅' if g['pass'] else '❌'} |")

    all_pass = all(g["pass"] for g in gates)
    lines.append("")
    lines.append(f"**Verdict:** {'✅ ALL GATES PASS — merge-ready' if all_pass else '❌ at least one gate failed — diagnose before merging'}")
    lines.append("")
    lines.append("## Judge axes (paired delta)")
    lines.append("")
    lines.append("| Axis | n | Baseline mean | Treatment mean | Δ mean | Δ median | Cohen's d | Wilcoxon p (2s) | 95% CI |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for axis in AXES:
        s = axis_stats.get(axis, {})
        lines.append(
            f"| {axis} | {s.get('n', 0)} | {_md(s.get('baseline_mean'))} | {_md(s.get('treatment_mean'))} | "
            f"{_md(s.get('mean_delta'))} | {_md(s.get('median_delta'))} | "
            f"{_md(s.get('cohens_d'))} | {_md(s.get('wilcoxon_p_two_sided'), 4)} | {_md(s.get('ci95'))} |"
        )
    lines.append("")
    lines.append("## Programmatic checks")
    lines.append("")
    lines.append("| Metric | n | Baseline mean | Treatment mean | Δ mean | Notes |")
    lines.append("|---|---|---|---|---|---|")
    lines.append(
        f"| banned_voice_hits | {voice_stats.get('n', 0)} | {_md(voice_stats.get('baseline_mean'))} | "
        f"{_md(voice_stats.get('treatment_mean'))} | {_md(voice_stats.get('mean_delta'))} | "
        f"reduction: {_md(voice_stats.get('pct_reduction'))} |"
    )
    lines.append(
        f"| grounded_ratio | {ground_stats.get('n', 0)} | {_md(ground_stats.get('baseline_mean'))} | "
        f"{_md(ground_stats.get('treatment_mean'))} | {_md(ground_stats.get('mean_delta'))} | "
        f"factuality cross-check |"
    )
    lines.append("")
    lines.append("## Per-pair deltas")
    lines.append("")
    lines.append("| cv_id | jd_id | bucket | Δ fact | Δ rel | Δ ats | Δ voice | Δ hits |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for p in paired:
        bj, tj = p["baseline"]["judge"], p["treatment"]["judge"]
        def delta(axis):
            b = bj.get(axis, {}).get("score")
            t = tj.get(axis, {}).get("score")
            if b is None or t is None:
                return None
            return int(t) - int(b)
        bh = int(p["baseline"].get("voice_hit_count") or 0)
        th = int(p["treatment"].get("voice_hit_count") or 0)
        lines.append(
            f"| `{p['cv_id']}` | `{p['jd_id']}` | {p.get('bucket')} | "
            f"{_md(delta('factuality'), 0)} | {_md(delta('relevance'), 0)} | "
            f"{_md(delta('ats_fit'), 0)} | {_md(delta('human_voice'), 0)} | "
            f"{th - bh} |"
        )
    return "\n".join(lines) + "\n"


# ---------- entry point ----------

def build_report(baseline_path: Path, treatment_path: Path) -> tuple[str, dict]:
    """Return (markdown, machine-readable dict)."""
    baseline = _load(baseline_path)
    treatment = _load(treatment_path)
    paired = _pair_rows(baseline, treatment)

    axis_stats: dict = {}
    for axis in AXES:
        b, t = _axis_pairs(paired, axis)
        axis_stats[axis] = _axis_summary(b, t)

    vb, vt = _voice_hits(paired)
    voice_stats = _voice_summary(vb, vt)

    gb, gt = _grounded(paired)
    if gb:
        diffs = [t_ - b_ for b_, t_ in zip(gb, gt)]
        ground_stats = {
            "n": len(gb),
            "baseline_mean": round(statistics.mean(gb), 4),
            "treatment_mean": round(statistics.mean(gt), 4),
            "mean_delta": round(statistics.mean(diffs), 4),
        }
    else:
        ground_stats = {"n": 0}

    gates = _gates(axis_stats, voice_stats, ground_stats)
    md = render_markdown(
        baseline, treatment, paired, axis_stats, voice_stats, ground_stats, gates,
        baseline_path=baseline_path, treatment_path=treatment_path,
    )
    payload = {
        "baseline_path": str(baseline_path),
        "treatment_path": str(treatment_path),
        "n_paired": len(paired),
        "axes": axis_stats,
        "voice_hits": voice_stats,
        "grounded_ratio": ground_stats,
        "gates": gates,
        "all_gates_pass": all(g["pass"] for g in gates),
    }
    return md, payload


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RAG A/B paired-delta report.")
    p.add_argument("--baseline", required=True, type=Path,
                   help="Path to baseline (T0) tailoring_eval JSON.")
    p.add_argument("--treatment", required=True, type=Path,
                   help="Path to treatment (T1/T2/T3) tailoring_eval JSON.")
    p.add_argument("--out", type=Path, default=None,
                   help="Markdown output path; prints to stdout when omitted.")
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    md, payload = build_report(args.baseline, args.treatment)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        # Also write the machine-readable JSON next to the markdown.
        json_path = args.out.with_suffix(".json")
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
        print(f"wrote {json_path}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
