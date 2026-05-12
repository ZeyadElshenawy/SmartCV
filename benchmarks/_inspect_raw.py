"""Print first 1000 chars + last 800 chars of each extracted CV for label authoring."""
import json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

raw = json.loads(Path("benchmarks/_raw_texts.json").read_text(encoding="utf-8"))
for fn in sorted(raw):
    info = raw[fn]
    if not info.get("ok"):
        print(f"\n{'='*80}\n{fn}: ERROR {info['error']}\n{'='*80}")
        continue
    text = info["raw_text"]
    print(f"\n{'='*80}")
    print(f"{fn}  ({len(text)} chars, parser_full_name={info['parser_full_name']!r})")
    print(f"  parser_email={info['parser_email']!r} phone={info['parser_phone']!r} loc={info['parser_location']!r}")
    print('='*80)
    print("HEAD:")
    print(text[:1000])
    print("\n...TAIL:")
    print(text[-800:])
