"""Replay a prior skill_extractor_eval run through the new canonicalizer
to estimate the F1 lift WITHOUT making any LLM calls. Loads the
2026-05-06 results before the C2 change (cached in run_all.json's phase
payload, since the latest skill_extractor_eval.json is now empty due to
rate limiting)."""
import json, sys, os, django
from pathlib import Path
from difflib import SequenceMatcher

sys.path.insert(0, ".")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "smartcv.settings")
django.setup()
sys.stdout.reconfigure(encoding="utf-8")

from jobs.services.skill_extractor import _canonicalize_skill  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The pre-C2 results are embedded in run_all.json's phase_payloads.
run_all = json.loads((ROOT / "benchmarks/results/2026-05-06/run_all.json").read_text(encoding="utf-8"))
prior = run_all["phase_payloads"]["skill_extractor_eval"]

FUZZY = 0.85

def norm(s):
    return (s or "").lower().strip()

def matches_any(needle, haystack):
    n = norm(needle)
    if not n:
        return False
    for h in haystack:
        hn = norm(h)
        if not hn:
            continue
        if n == hn:
            return True
        if SequenceMatcher(None, n, hn).ratio() >= FUZZY:
            return True
    return False

def score(extracted, labeled):
    tp_e = sum(1 for e in extracted if matches_any(e, labeled))
    tp_l = sum(1 for l in labeled if matches_any(l, extracted))
    p = tp_e / len(extracted) if extracted else 0.0
    r = tp_l / len(labeled) if labeled else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1

# Find each JD fixture and its expected_skills label.
fixtures_dir = ROOT / "benchmarks/fixtures/jobs"
jd_labels = {}
for fp in sorted(fixtures_dir.glob("*.json")):
    j = json.loads(fp.read_text(encoding="utf-8"))
    jd_labels[j["id"]] = j.get("expected_skills") or []

orig_f1, canon_f1 = [], []
canon_label_f1 = []
print(f"\nReplay: prior extracted_raw -> canonicalized -> compare to labels\n")
print(f"{'jd_id':50s} {'orig F1':>8} {'canon ext only':>15} {'canon both':>12}")
for r in prior["per_job"]:
    jd_id = r["jd_id"]
    raw = (r["runs"][0] or {}).get("extracted_raw") or []
    labels = jd_labels.get(jd_id) or []
    if not raw:
        continue

    # Original (no canon)
    _, _, f1_orig = score(raw, labels)
    # Canonicalize extractor side only
    canon_ext = list(dict.fromkeys(_canonicalize_skill(s) for s in raw))
    _, _, f1_canon = score(canon_ext, labels)
    # Canonicalize both sides
    canon_lab = list(dict.fromkeys(_canonicalize_skill(s) for s in labels))
    _, _, f1_both = score(canon_ext, canon_lab)

    orig_f1.append(f1_orig)
    canon_f1.append(f1_canon)
    canon_label_f1.append(f1_both)
    print(f"{jd_id:50s} {f1_orig:>8.3f} {f1_canon:>15.3f} {f1_both:>12.3f}")

def m(xs):
    return sum(xs) / len(xs) if xs else 0.0

print(f"\n{'MEAN':50s} {m(orig_f1):>8.3f} {m(canon_f1):>15.3f} {m(canon_label_f1):>12.3f}")
print(f"\nLabels-also-canonicalized (which is what would happen in production once "
      f"both sides flow through extract_skills/parser canon paths) shows the realistic upper-bound.")
