"""Side-by-side: labeled skills vs LLM-pipeline-extracted skills, for low-F1 CVs."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
data = json.loads((ROOT / "benchmarks/results/2026-05-06/parser_eval.json").read_text(encoding="utf-8"))

# Find all CVs with F1 < 0.50
weak = []
for r in data["rows"]:
    f1 = r.get("skill_f1_mean", 0.0)
    if f1 < 0.50:
        run0 = (r.get("runs") or [{}])[0]
        sk = run0.get("skills", {})
        weak.append({
            "cv_id": r["cv_id"],
            "f1": f1,
            "section_present": r.get("skills_section_present"),
            "n_parsed": sk.get("n_parsed", 0),
            "n_labeled": sk.get("n_labeled", 0),
            "missed": sk.get("missed", []),
            "extra": sk.get("extra", []),
        })

weak.sort(key=lambda x: x["f1"])
print(f"{len(weak)} CVs with F1 < 0.50:\n")

LABELS_DIR = ROOT / "benchmarks/fixtures/labels"
for w in weak:
    label_path = LABELS_DIR / f"{w['cv_id']}.json"
    label = json.loads(label_path.read_text(encoding="utf-8")) if label_path.exists() else {}
    labeled_skills = label.get("skills_canonical", [])

    print(f"=== {w['cv_id']}  (F1={w['f1']:.3f}, has_section={w['section_present']}) ===")
    print(f"  LABELED ({len(labeled_skills)}): {labeled_skills}")
    print(f"  MISSED  ({len(w['missed'])}): {w['missed']}")
    print(f"  EXTRA   ({len(w['extra'])}): {w['extra']}")
    print()
