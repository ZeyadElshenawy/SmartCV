"""Diagnose why parser skills F1 is low — show per-CV FP/FN."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "benchmarks/results/2026-05-06/parser_eval.json").read_text(encoding="utf-8"))

rows = data.get("rows") or []
print(f"=== Parser skills diagnostic — {len(rows)} CVs ===\n")

# Build flat per-CV view from rows[].runs[0].skills
def view(row):
    run0 = (row.get("runs") or [{}])[0]
    return {
        "cv_id": row.get("cv_id"),
        "skills_section": row.get("skills_section_present"),
        "primary_role": row.get("primary_role"),
        "f1": row.get("skill_f1_mean", 0.0),
        "precision": (run0.get("skills") or {}).get("precision", 0.0),
        "recall": (run0.get("skills") or {}).get("recall", 0.0),
        "n_parsed": (run0.get("skills") or {}).get("n_parsed", 0),
        "n_labeled": (run0.get("skills") or {}).get("n_labeled", 0),
        "missed": (run0.get("skills") or {}).get("missed", []),
        "hallucinated": (run0.get("skills") or {}).get("extra", []),
        "matched": (run0.get("skills") or {}).get("matched", []),
    }

views = [view(r) for r in rows]
views_sorted = sorted(views, key=lambda v: v["f1"])

print(">>> 12 WORST CVs by skills F1 <<<\n")
for v in views_sorted[:12]:
    print(f"  {v['cv_id']}  ({v['primary_role']}, has_skills_section={v['skills_section']})")
    print(f"    F1={v['f1']:.3f}  P={v['precision']:.3f}  R={v['recall']:.3f}")
    print(f"    n_parsed={v['n_parsed']}  n_labeled={v['n_labeled']}")
    if v['missed']:
        print(f"    MISSED ({len(v['missed'])}): {v['missed'][:15]}")
    if v['hallucinated']:
        print(f"    HALLUC ({len(v['hallucinated'])}): {v['hallucinated'][:15]}")
    print()

from collections import Counter
all_missed = Counter()
all_halluc = Counter()
for v in views:
    for s in v["missed"]:
        all_missed[s] += 1
    for s in v["hallucinated"]:
        all_halluc[s] += 1

print(f">>> Most frequently MISSED skills (false negatives) <<<")
for sk, n in all_missed.most_common(20):
    print(f"  {n:>2}x  {sk}")

print(f"\n>>> Most frequently HALLUCINATED skills (false positives) <<<")
for sk, n in all_halluc.most_common(20):
    print(f"  {n:>2}x  {sk}")
