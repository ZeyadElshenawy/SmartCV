"""Diagnose skill extractor — per-JD FP/FN."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "benchmarks/results/2026-05-06/skill_extractor_eval.json").read_text(encoding="utf-8"))

print(f"Top-level keys: {sorted(data.keys())}\n")

rows = data.get("per_job") or []
print(f"=== Skill Extractor diagnostic — {len(rows)} JDs ===\n")

def view(r):
    run0 = (r.get("runs") or [{}])[0]
    return {
        "jd_id": r.get("jd_id"),
        "title": r.get("title"),
        "f1": r.get("f1_mean", 0.0),
        "precision": r.get("precision_mean", 0.0),
        "recall": r.get("recall_mean", 0.0),
        "halluc": r.get("hallucination_mean", 0.0),
        "n_extracted": run0.get("n_extracted", 0),
        "n_labeled": run0.get("n_labeled", 0),
        "missed": run0.get("missed", []),
        "extra": run0.get("extra", []),
    }

views = [view(r) for r in rows]
views_sorted = sorted(views, key=lambda v: v["f1"])

print(">>> 12 WORST JDs by F1 <<<\n")
for v in views_sorted[:12]:
    print(f"  {v['jd_id']}  ({v['title']})")
    print(f"    F1={v['f1']:.3f}  P={v['precision']:.3f}  R={v['recall']:.3f}  halluc={v['halluc']:.3f}")
    print(f"    n_extracted={v['n_extracted']}  n_labeled={v['n_labeled']}")
    if v['missed']:
        print(f"    MISSED ({len(v['missed'])}): {v['missed'][:15]}")
    if v['extra']:
        print(f"    EXTRA  ({len(v['extra'])}): {v['extra'][:15]}")
    print()

from collections import Counter
all_missed = Counter()
all_extra = Counter()
for v in views:
    for s in v["missed"]:
        all_missed[s] += 1
    for s in v["extra"]:
        all_extra[s] += 1

print(">>> Most missed (false negatives) across 30 JDs <<<")
for s, n in all_missed.most_common(20):
    print(f"  {n:>2}x  {s}")

print("\n>>> Most extra (false positives / hallucinations) across 30 JDs <<<")
for s, n in all_extra.most_common(20):
    print(f"  {n:>2}x  {s}")

# Split: hand-curated JDs vs auto-generated
hand_views = [v for v in views if not v["jd_id"].startswith("jd_auto_")]
auto_views = [v for v in views if v["jd_id"].startswith("jd_auto_")]

def avg(vals):
    return sum(vals) / len(vals) if vals else 0

print(f"\n>>> Mean F1 by JD source <<<")
print(f"  hand-curated ({len(hand_views)} JDs): F1={avg([v['f1'] for v in hand_views]):.3f}")
print(f"  auto-generated ({len(auto_views)} JDs): F1={avg([v['f1'] for v in auto_views]):.3f}")
