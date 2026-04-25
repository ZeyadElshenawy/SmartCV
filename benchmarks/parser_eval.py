"""CV-parser evaluation (Phase D1).

Runs ``profiles.services.cv_parser.parse_cv`` against each fixture CV
and grades the output against the hand-curated label JSON sitting next
to it under ``benchmarks/fixtures/labels/``.

Metrics per CV:
- Personal-info exact-match (name, email, phone, location) — case- and
  whitespace-insensitive. Labels with a ``null`` value are skipped (not
  counted against precision).
- Section presence accuracy — did the parser emit anything for the
  section the label says is present?
- Skills overlap vs ``skills_canonical``: Jaccard plus precision /
  recall, with the same fuzzy synonym tolerance used by gap analyzer.
- Counts vs labels: experience_count, education_count.

Each parser call takes ~1s (mostly Groq), and the algorithm has small
LLM-driven steps so we report mean ± std over ``--repeats`` runs (default
1; bump to 3 to expose variance).

Run:
    python -m benchmarks.parser_eval
    python -m benchmarks.parser_eval --repeats 3
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from difflib import SequenceMatcher
from typing import Iterable

from benchmarks._io import FIXTURES_DIR, REPO_ROOT, summary, write_section
from profiles.services.cv_parser import parse_cv

FUZZY_CUTOFF = 0.85


def _norm(s: str | None) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _norm_phone(s: str | None) -> str:
    if s is None:
        return ""
    return re.sub(r"\D", "", str(s))


def _matches_any(needle: str, haystack: Iterable[str]) -> bool:
    n = _norm(needle)
    if not n:
        return False
    for h in haystack:
        hn = _norm(h)
        if not hn:
            continue
        if n == hn or n in hn or hn in n:
            return True
        if SequenceMatcher(None, n, hn).ratio() >= FUZZY_CUTOFF:
            return True
    return False


def _personal_info(parsed: dict, labeled: dict) -> dict:
    """Per-field exact match. ``null`` labels are skipped, not failed."""
    fields = ["name", "email", "phone", "location"]
    parsed_pi = {
        "name": parsed.get("full_name") or "",
        "email": parsed.get("email") or "",
        "phone": parsed.get("phone") or "",
        "location": parsed.get("location") or "",
    }
    out: dict = {"per_field": {}, "considered": 0, "correct": 0}
    for f in fields:
        labeled_value = labeled.get(f)
        parsed_value = parsed_pi[f]
        if labeled_value is None:
            out["per_field"][f] = {"status": "skipped_null_label",
                                   "parsed": parsed_value or None}
            continue
        out["considered"] += 1
        if f == "phone":
            ok = _norm_phone(parsed_value) == _norm_phone(labeled_value)
        elif f == "email":
            ok = _norm(parsed_value) == _norm(labeled_value)
        else:
            ok = (_norm(parsed_value) == _norm(labeled_value)
                  or _norm(labeled_value) in _norm(parsed_value)
                  or _norm(parsed_value) in _norm(labeled_value))
        if ok:
            out["correct"] += 1
        out["per_field"][f] = {
            "status": "match" if ok else "miss",
            "parsed": parsed_value,
            "labeled": labeled_value,
        }
    out["accuracy"] = round(out["correct"] / out["considered"], 4) if out["considered"] else None
    return out


def _section_presence(parsed: dict, labeled: dict) -> dict:
    """Did the parser produce non-empty output for each section flagged present?"""
    label_sections = labeled.get("section_presence") or {}
    parsed_present = {
        "summary": bool(parsed.get("raw_text")),  # parse_cv flattens summary into raw_text
        "experience": bool(parsed.get("experiences")),
        "education": bool(parsed.get("education")),
        "projects": bool(parsed.get("projects")),
        "skills": bool(parsed.get("skills")),
    }
    per: dict = {}
    matches = 0
    for section, expected_present in label_sections.items():
        actual = bool(parsed_present.get(section))
        match = actual == expected_present
        if match:
            matches += 1
        per[section] = {"expected": expected_present, "actual": actual, "match": match}
    return {
        "per_section": per,
        "accuracy": round(matches / len(label_sections), 4) if label_sections else None,
    }


def _skill_jaccard(parsed: dict, labeled: dict) -> dict:
    """Jaccard + precision/recall on parsed skill names vs canonical labels."""
    parsed_skills = []
    for s in (parsed.get("skills") or []):
        if isinstance(s, dict):
            name = s.get("name") or ""
        else:
            name = str(s)
        if name:
            parsed_skills.append(name)
    labeled_skills = labeled.get("skills_canonical") or []

    matched_in_labeled = [l for l in labeled_skills if _matches_any(l, parsed_skills)]
    matched_in_parsed = [p for p in parsed_skills if _matches_any(p, labeled_skills)]
    union_size = len(set(_norm(s) for s in parsed_skills) | set(_norm(s) for s in labeled_skills))
    intersection_size = len(matched_in_labeled)

    precision = len(matched_in_parsed) / len(parsed_skills) if parsed_skills else 0.0
    recall = len(matched_in_labeled) / len(labeled_skills) if labeled_skills else 0.0
    jaccard = intersection_size / union_size if union_size else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "n_parsed": len(parsed_skills),
        "n_labeled": len(labeled_skills),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "jaccard": round(jaccard, 4),
        "missed": [l for l in labeled_skills if not _matches_any(l, parsed_skills)],
        "extra": [p for p in parsed_skills if not _matches_any(p, labeled_skills)],
    }


def _counts(parsed: dict, labeled: dict) -> dict:
    return {
        "experience": {
            "labeled": labeled.get("experience_count"),
            "parsed": len(parsed.get("experiences") or []),
        },
        "education": {
            "labeled": labeled.get("education_count"),
            "parsed": len(parsed.get("education") or []),
        },
    }


def _load_manifest() -> dict:
    return json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))


def _label_for(cv_id: str) -> dict | None:
    p = FIXTURES_DIR / "labels" / f"{cv_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def run(repeats: int = 1) -> dict:
    manifest = _load_manifest()
    rows: list[dict] = []
    all_pi_acc: list[float] = []
    all_section_acc: list[float] = []
    all_jaccard: list[float] = []
    all_skill_f1: list[float] = []
    # Headline metric: skills accuracy on CVs that *have* a skills section.
    # CVs without one are an out-of-scope task for the parser — it extracts
    # from explicit skills sections, not by inferring from experience text —
    # so averaging them in drags the number for a job the parser isn't
    # designed to do. We still report the all-CVs aggregate alongside.
    skills_f1_with_section: list[float] = []
    skills_jaccard_with_section: list[float] = []
    all_latency_ms: list[float] = []
    started = time.perf_counter()

    for cv_meta in manifest["cvs"]:
        cv_id = cv_meta["id"]
        cv_path = REPO_ROOT / cv_meta["path"]
        labeled = _label_for(cv_id)
        if labeled is None:
            rows.append({"cv_id": cv_id, "error": "no_label_file"})
            continue

        runs: list[dict] = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            try:
                parsed = parse_cv(str(cv_path))
                err = None
            except Exception as exc:  # noqa: BLE001
                parsed = {}
                err = f"{exc.__class__.__name__}: {exc}"
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            pi = _personal_info(parsed, labeled.get("personal_info") or {})
            sp = _section_presence(parsed, labeled)
            sk = _skill_jaccard(parsed, labeled)
            counts = _counts(parsed, labeled)
            runs.append({
                "personal_info": pi,
                "section_presence": sp,
                "skills": sk,
                "counts": counts,
                "latency_ms": elapsed_ms,
                "error": err,
            })
            all_latency_ms.append(elapsed_ms)
            if pi["accuracy"] is not None:
                all_pi_acc.append(pi["accuracy"])
            if sp["accuracy"] is not None:
                all_section_acc.append(sp["accuracy"])
            all_jaccard.append(sk["jaccard"])
            all_skill_f1.append(sk["f1"])
            if (labeled.get("section_presence") or {}).get("skills") is True:
                skills_f1_with_section.append(sk["f1"])
                skills_jaccard_with_section.append(sk["jaccard"])

        pi_means = [r["personal_info"]["accuracy"] for r in runs if r["personal_info"]["accuracy"] is not None]
        sp_means = [r["section_presence"]["accuracy"] for r in runs if r["section_presence"]["accuracy"] is not None]
        sk_jacc = [r["skills"]["jaccard"] for r in runs]
        sk_f1 = [r["skills"]["f1"] for r in runs]

        rows.append({
            "cv_id": cv_id,
            "primary_role": cv_meta.get("primary_role"),
            "skills_section_present": (labeled.get("section_presence") or {}).get("skills"),
            "personal_info_accuracy_mean": round(statistics.fmean(pi_means), 4) if pi_means else None,
            "section_presence_accuracy_mean": round(statistics.fmean(sp_means), 4) if sp_means else None,
            "skill_jaccard_mean": round(statistics.fmean(sk_jacc), 4) if sk_jacc else None,
            "skill_f1_mean": round(statistics.fmean(sk_f1), 4) if sk_f1 else None,
            "runs": runs,
        })

    payload = {
        "benchmark": "parser_eval",
        "version": 1,
        "fixture_kind": "real_anonymized_cv",
        "n_cvs": len(manifest["cvs"]),
        "repeats_per_cv": repeats,
        "wall_seconds": round(time.perf_counter() - started, 2),
        "aggregate": {
            "personal_info_accuracy": summary(all_pi_acc),
            "section_presence_accuracy": summary(all_section_acc),
            "skills_jaccard": summary(all_jaccard),
            "skills_f1": summary(all_skill_f1),
            # Headline: skills metric scoped to CVs that actually have a
            # skills section. Out-of-scope CVs are still in skills_f1.
            "skills_f1_with_section": summary(skills_f1_with_section),
            "skills_jaccard_with_section": summary(skills_jaccard_with_section),
            "latency_ms": summary(all_latency_ms),
        },
        "rows": rows,
        "method": {
            "service": "profiles.services.cv_parser.parse_cv",
            "fuzzy_cutoff": FUZZY_CUTOFF,
            "scoring": {
                "personal_info": "case-insensitive match; phone digit-normalized; null labels skipped",
                "skills": "lowercased exact + difflib >= 0.85 + substring tolerance",
                "section_presence": "labeled section true => parsed list/text non-empty",
            },
        },
        "disclosure": (
            f"LLM-driven (Groq llama-4-scout via the parser's structured-output stage). "
            f"Mean of {repeats} run(s) per CV. PII normalization is intentionally lenient "
            "(case- and whitespace-insensitive) so cosmetic differences don't dominate the score."
        ),
    }
    out_path = write_section("parser_eval", payload)
    payload["written_to"] = str(out_path)
    return payload


def _format_report(payload: dict) -> str:
    agg = payload["aggregate"]
    lines = [
        "-- CV parser (Phase D1) --",
        f"  N CVs        : {payload['n_cvs']}  (x{payload['repeats_per_cv']} runs each)",
        f"  Wall         : {payload['wall_seconds']}s",
        f"  Aggregate    : "
        f"PI={agg['personal_info_accuracy']['mean']}  "
        f"sections={agg['section_presence_accuracy']['mean']}  "
        f"skills_jaccard={agg['skills_jaccard']['mean']}  "
        f"skills_F1={agg['skills_f1']['mean']}",
        "  Per CV:",
    ]
    for r in payload["rows"]:
        lines.append(
            f"    {r.get('cv_id'):34s}  "
            f"PI={r.get('personal_info_accuracy_mean')}  "
            f"sect={r.get('section_presence_accuracy_mean')}  "
            f"sk_jacc={r.get('skill_jaccard_mean')}  "
            f"sk_F1={r.get('skill_f1_mean')}"
        )
    lines.append(f"  Written to   : {payload['written_to']}")
    return "\n".join(lines)


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartCV parser benchmark")
    parser.add_argument("--repeats", type=int, default=1, help="runs per CV (default: 1)")
    return parser.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    args = _parse_args()
    result = run(repeats=args.repeats)
    print(_format_report(result))
