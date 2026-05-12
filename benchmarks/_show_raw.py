"""Print raw text for the 7 weak CVs to verify what skills they actually mention."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
raw = json.loads((ROOT / "benchmarks/_raw_texts.json").read_text(encoding="utf-8"))

# CV ID → filename mapping for the 7 weak CVs
weak_cvs = {
    "cv_frontend_mid_react": "cv_3.pdf",
    "cv_frontend_senior_react_vue_v2": "cv_7.pdf",
    "cv_frontend_jquery_legacy": "cv_11.pdf",
    "cv_bahgat_ai_research_senior": "Mohamed Bahget CV .pdf",
    "cv_abbas_backend_devops_senior_student": "Ahmed-Mahmoud-Abbas-MasterCV.pdf",
    "cv_frontend_senior_react_vue": "cv_0.pdf",
    "cv_taher_mobile_jr": "Mohamed Taher Amin -  CV.pdf",
}

for cv_id, fn in weak_cvs.items():
    info = raw.get(fn, {})
    text = info.get("raw_text", "")
    print(f"\n{'='*80}\n{cv_id} ({fn})\n{'='*80}")
    print(text)
