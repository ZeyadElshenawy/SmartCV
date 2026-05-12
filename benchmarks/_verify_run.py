"""Post-run verification: dump headlines from the most recent run_all.json."""
import json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"

dirs = sorted(d for d in RESULTS.iterdir() if d.is_dir())
if not dirs:
    print("No results directories found")
    raise SystemExit(1)

latest = dirs[-1]
print(f"Latest results dir: {latest.name}")
print(f"Files:")
for f in sorted(latest.iterdir()):
    print(f"  {f.name:35s}  {f.stat().st_size:>10,} bytes")

run_all_path = latest / "run_all.json"
if not run_all_path.exists():
    print(f"\nNO run_all.json — partial run only")
    raise SystemExit(0)

run_all = json.loads(run_all_path.read_text(encoding="utf-8"))

print(f"\n=== run_all.json summary ===")
print(f"  run_at_utc:    {run_all.get('run_at_utc')}")
print(f"  wall_seconds:  {run_all.get('wall_seconds')}")
print(f"  phases_run:    {run_all.get('phases_run')}")
print(f"  phase_info:")
for phase, info in (run_all.get("phase_info") or {}).items():
    ok = info.get("ok", "?")
    sec = info.get("wall_seconds", "?")
    err = info.get("error", "")
    print(f"    {phase:25s}  ok={ok}  {sec}s  {err}")

print(f"\n=== Headlines ===")
for k, v in (run_all.get("headlines") or {}).items():
    print(f"  {k}:")
    if isinstance(v, dict):
        for kk, vv in v.items():
            print(f"    {kk}: {vv}")
    else:
        print(f"    {v}")
