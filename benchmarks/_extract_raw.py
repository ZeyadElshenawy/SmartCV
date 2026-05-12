"""One-shot: extract raw_text from every CV in test cvs/ to JSON for label authoring."""
from __future__ import annotations
import os, sys, json, django
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "smartcv.settings")
django.setup()

from profiles.services.cv_parser import parse_cv  # noqa: E402

CV_DIR = ROOT / "test cvs"
OUT = ROOT / "benchmarks" / "_raw_texts.json"

results = {}
for fp in sorted(CV_DIR.iterdir()):
    if fp.suffix.lower() not in (".pdf", ".docx", ".doc"):
        continue
    try:
        parsed = parse_cv(str(fp))
        results[fp.name] = {
            "ok": True,
            "size_bytes": fp.stat().st_size,
            "raw_text": parsed.get("raw_text", ""),
            "parser_full_name": parsed.get("full_name"),
            "parser_email": parsed.get("email"),
            "parser_phone": parsed.get("phone"),
            "parser_location": parsed.get("location"),
        }
        print(f"OK {fp.name}: {len(results[fp.name]['raw_text'])} chars")
    except Exception as e:
        results[fp.name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(f"ERR {fp.name}: {e}")

OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nWrote {OUT} ({OUT.stat().st_size:,} bytes)")
