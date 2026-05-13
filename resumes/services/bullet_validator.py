"""Deterministic post-generation bullet validator (§4 of the RAG plan).

Pure-Python rules engine that scores a generated resume against the same
contract the prompt nudges the LLM toward. No LLM calls in this path.

Three tiers of checks:
  - Tier A (per bullet):   A1 banned phrase · A2 action-verb start ·
                           A3 "Responsible for" opener · A4 inside-out summary ·
                           A5 length · A6 em-dash · A7 demonstrating closer
  - Tier B (per role):     B1 quantification coverage · B2 verb diversity ·
                           B3 structure variation
  - Tier C (resume-level): C1 length per seniority · C2 buzzword saturation

Modes:
  - "report_only" (default) — return findings, don't mutate
  - "safe_autofix" — apply deterministic rewrites (banned-phrase substitution
    via BANNED_PHRASES, em-dash → comma). Anything else stays flagged.

Caller signature:
    report = validate_resume(generated_resume_dict, seniority="mid")
    resume, report = validate_resume(d, seniority="mid", mode="safe_autofix")
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, Field

from profiles.services.prompt_guards import BANNED_JARGON_PHRASES, BANNED_PHRASES
from profiles.services.kb_loader import get_action_verbs


# ---------------------------------------------------------------------------
# Result schemas
# ---------------------------------------------------------------------------

Severity = Literal["error", "warn"]


class BulletFinding(BaseModel):
    rule_id: str
    severity: Severity
    location: str
    bullet_text: str = Field(..., max_length=400)
    issue: str
    suggested_fix: str | None = None


class ValidationReport(BaseModel):
    passed: bool
    findings: list[BulletFinding] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rule constants
# ---------------------------------------------------------------------------

# A3: bullet must not start with these "duty" openers.
_DUTY_OPENERS = (
    "responsible for",
    "tasked with",
    "in charge of",
    "duties included",
    "duties involved",
    "duties:",
    "worked on",
    "helped with",
    "helped to",
    "assisted with",
    "assisted in",
    "participated in",
)

# A4: inside-out summary opener templates (substring match, lowercase).
_INSIDE_OUT_OPENERS = (
    "with over",       # "With over N years of experience..."
    "with more than",
    "as a ",           # "As a senior <role>, I am passionate about..."
    "driven by ",
    "i bring ",
    "i am passionate",
    "passionate about",
    "results-driven professional",
    "highly motivated",
    "i excel at",
)

# A6: em-dash. U+2014 only — en-dash and hyphen are fine.
_EM_DASH = "—"

# A7: "<action>, demonstrating <skill>" closer.
_DEMONSTRATING_CLOSER = re.compile(r",\s+demonstrating\s+\w", re.IGNORECASE)

# B1: any of these regexes counts as a quantification.
_QUANT_PATTERNS = [
    re.compile(r"\b\d+(?:\.\d+)?%"),                                              # 50%, 12.5%
    re.compile(r"\$\d+(?:,\d{3})*(?:\.\d+)?\s*[KMB]?\b"),                          # $1.2M, $500K
    re.compile(r"\b\d+(?:,\d{3})+\b"),                                            # 12,000
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|min|hr|hours?|days?|weeks?|months?|years?)\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+\d.+?\s+to\s+\d", re.IGNORECASE),                       # from 40 min to 6 min
    re.compile(r"\b\d+(?:\.\d+)?x\b", re.IGNORECASE),                             # 3x, 2.5x
    re.compile(r"\b\d{2,}\b"),                                                    # standalone ≥10
]

# A5: bullet length bounds (chars). Sweet spot per ats_rules/011_resume_length.
_LEN_MIN = 50
_LEN_MAX = 250

# C1: resume length per seniority (total bullet count).
_SENIORITY_RANGES: dict[str, tuple[int, int]] = {
    "intern": (5, 15),
    "junior": (8, 20),
    "mid": (12, 30),
    "senior": (15, 35),
    "lead": (15, 35),
}


# ---------------------------------------------------------------------------
# Bullet iteration helpers
# ---------------------------------------------------------------------------

def _iter_role_bullets(resume: dict) -> list[tuple[str, str, int, list[str]]]:
    """Yield (section, role_label, role_index, bullets_list) for every role
    in experience/projects. Bullets come from `description` (List[str] after
    schema normalization) or `highlights`.

    Returned `bullets_list` is the SAME list reference inside the resume dict
    — autofix can mutate it in place.
    """
    out: list[tuple[str, str, int, list[str]]] = []
    for kind in ("experience", "projects"):
        for i, role in enumerate(resume.get(kind) or []):
            if not isinstance(role, dict):
                continue
            label = role.get("title") or role.get("name") or f"{kind}[{i}]"
            desc = role.get("description")
            if isinstance(desc, list):
                out.append((kind, str(label), i, desc))
            highlights = role.get("highlights")
            if isinstance(highlights, list) and highlights:
                out.append((kind, f"{label}.highlights", i, highlights))
    return out


def _all_bullets(resume: dict) -> list[tuple[str, str]]:
    """Flat list of (location_path, bullet_text) for every bullet."""
    out: list[tuple[str, str]] = []
    for kind, _label, idx, bullets in _iter_role_bullets(resume):
        for b_idx, bullet in enumerate(bullets):
            if isinstance(bullet, str) and bullet.strip():
                if "highlights" in _label or False:
                    loc = f"{kind}[{idx}].highlights[{b_idx}]"
                else:
                    loc = f"{kind}[{idx}].description[{b_idx}]"
                out.append((loc, bullet))
    return out


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------

def _check_banned_phrase(bullet: str, location: str, strict: bool) -> list[BulletFinding]:
    """A1: literal banned phrases from BANNED_PHRASES (+ jargon when strict)."""
    findings: list[BulletFinding] = []
    text = bullet.lower()
    seen: set[str] = set()
    for phrase, replacement in BANNED_PHRASES.items():
        if phrase in text and phrase not in seen:
            seen.add(phrase)
            fix: str | None = None
            if replacement is not None:
                fix = _substitute(bullet, phrase, replacement)
            findings.append(BulletFinding(
                rule_id="A1_banned_phrase",
                severity="error",
                location=location,
                bullet_text=bullet[:400],
                issue=f"contains banned token '{phrase}'" + (
                    f" → suggest '{replacement}'" if replacement else " (delete-only)"
                ),
                suggested_fix=fix,
            ))
    if strict:
        for phrase in BANNED_JARGON_PHRASES:
            if phrase in text and phrase not in seen:
                seen.add(phrase)
                findings.append(BulletFinding(
                    rule_id="A1_banned_jargon",
                    severity="error",
                    location=location,
                    bullet_text=bullet[:400],
                    issue=f"contains corporate jargon '{phrase}' (strict mode)",
                ))
    return findings


def _substitute(bullet: str, phrase: str, replacement: str) -> str:
    """Case-aware single-occurrence substitution. 'Leveraged' → 'Used', not 'used'."""
    pattern = re.compile(re.escape(phrase), re.IGNORECASE)

    def _repl(m: re.Match) -> str:
        match = m.group(0)
        if match.isupper():
            return replacement.upper()
        if match[0].isupper():
            return replacement.capitalize()
        return replacement
    return pattern.sub(_repl, bullet, count=1)


def _check_action_verb_start(bullet: str, location: str, verbs: frozenset[str]) -> list[BulletFinding]:
    """A2: bullet should start with an action verb, a system/tool name, or a
    scale/quantification — per HUMAN_VOICE_RULE rule 4. WARN only (rule 4
    explicitly wants 1-in-3 bullets NOT to start with a verb).
    """
    first = bullet.strip().split(None, 1)
    if not first:
        return []
    head = first[0].strip(".,;:")
    head_lc = head.lower()

    # action verb match
    if head_lc in verbs:
        return []
    # bullet starts with a digit (likely a metric / scale) — fine.
    if head[:1].isdigit():
        return []
    # token contains a digit anywhere (technical jargon: p95, h264, v2.0,
    # k8s, AWS-S3 etc.). Almost always a deliberate concrete reference.
    if any(c.isdigit() for c in head):
        return []
    # bullet starts with a system/tool name pattern (Capitalized + Capitalized
    # like "React +" or "Across 12 ..." or "Storybook"). Light heuristic:
    # any token of length ≥3 with an uppercase first letter passes.
    if head[:1].isupper() and len(head) >= 3:
        return []

    return [BulletFinding(
        rule_id="A2_action_verb_start",
        severity="warn",
        location=location,
        bullet_text=bullet[:400],
        issue=f"first token '{head}' is not a recognized action verb, system name, or metric",
    )]


def _check_duty_opener(bullet: str, location: str) -> list[BulletFinding]:
    """A3: bullet must not start with 'Responsible for' / 'Tasked with' / etc."""
    text = bullet.strip().lower()
    for opener in _DUTY_OPENERS:
        if text.startswith(opener):
            return [BulletFinding(
                rule_id="A3_duty_opener",
                severity="error",
                location=location,
                bullet_text=bullet[:400],
                issue=f"opens with duty phrase '{opener}'; rewrite as accomplishment",
            )]
    return []


def _check_length(bullet: str, location: str) -> list[BulletFinding]:
    """A5: length 50–250 chars."""
    n = len(bullet.strip())
    if n == 0:
        return []
    if n < _LEN_MIN:
        return [BulletFinding(
            rule_id="A5_length_short",
            severity="warn",
            location=location,
            bullet_text=bullet[:400],
            issue=f"bullet is {n} chars; minimum is {_LEN_MIN}",
        )]
    if n > _LEN_MAX:
        return [BulletFinding(
            rule_id="A5_length_long",
            severity="warn",
            location=location,
            bullet_text=bullet[:400],
            issue=f"bullet is {n} chars; cap is {_LEN_MAX}",
        )]
    return []


def _check_em_dash(bullet: str, location: str) -> list[BulletFinding]:
    """A6: HUMAN_VOICE_RULE rule 7 — em-dashes are an AI tell. Auto-fixable."""
    if _EM_DASH not in bullet:
        return []
    return [BulletFinding(
        rule_id="A6_em_dash",
        severity="warn",
        location=location,
        bullet_text=bullet[:400],
        issue="contains em-dash; replace with comma",
        suggested_fix=bullet.replace(_EM_DASH, ", "),
    )]


def _check_demonstrating_closer(bullet: str, location: str) -> list[BulletFinding]:
    """A7: ',demonstrating <skill>' closer is the canonical AI tell."""
    if not _DEMONSTRATING_CLOSER.search(bullet):
        return []
    return [BulletFinding(
        rule_id="A7_demonstrating_closer",
        severity="error",
        location=location,
        bullet_text=bullet[:400],
        issue="ends with ', demonstrating <skill>' AI-tell closer — name the concrete result instead",
    )]


def _check_inside_out(summary_text: str, location: str) -> list[BulletFinding]:
    """A4: summary field only — banned inside-out opener templates."""
    if not summary_text:
        return []
    text = summary_text.strip().lower()
    for opener in _INSIDE_OUT_OPENERS:
        if text.startswith(opener) or text.find(opener) < 30:
            return [BulletFinding(
                rule_id="A4_inside_out_summary",
                severity="error",
                location=location,
                bullet_text=summary_text[:400],
                issue=f"summary uses inside-out opener pattern matching '{opener}'",
            )]
    return []


# ---------- Tier B (role-level) ----------

def _has_quantification(text: str) -> bool:
    return any(p.search(text) for p in _QUANT_PATTERNS)


def _check_role_quantification(bullets: list[str], location: str) -> list[BulletFinding]:
    """B1: every role with bullets needs at least one quantified bullet.
    Promoted to ERROR if role has ≥3 bullets and zero are quantified.
    """
    bullets = [b for b in bullets if isinstance(b, str) and b.strip()]
    if not bullets:
        return []
    if any(_has_quantification(b) for b in bullets):
        return []
    severity: Severity = "error" if len(bullets) >= 3 else "warn"
    return [BulletFinding(
        rule_id="B1_quantification",
        severity=severity,
        location=location,
        bullet_text=bullets[0][:400],
        issue=f"role has {len(bullets)} bullets but none contain a number/metric/timeframe",
    )]


def _first_token_lc(text: str) -> str:
    parts = text.strip().split(None, 1)
    return parts[0].strip(".,;:").lower() if parts else ""


def _check_verb_diversity(bullets: list[str], location: str) -> list[BulletFinding]:
    """B2: no two consecutive bullets start with the same first token."""
    out: list[BulletFinding] = []
    prev = ""
    for i, b in enumerate(bullets):
        if not isinstance(b, str):
            continue
        head = _first_token_lc(b)
        if head and head == prev:
            out.append(BulletFinding(
                rule_id="B2_verb_diversity",
                severity="warn",
                location=f"{location}[{i}]",
                bullet_text=b[:400],
                issue=f"starts with the same word as the previous bullet ('{head}')",
            ))
        prev = head
    return out


def _check_structure_variation(bullets: list[str], location: str, verbs: frozenset[str]) -> list[BulletFinding]:
    """B3: of any 3 consecutive bullets, ≥1 must NOT start with a verb."""
    out: list[BulletFinding] = []
    if len(bullets) < 3:
        return out
    for i in range(len(bullets) - 2):
        window = bullets[i:i + 3]
        verb_starts = sum(1 for b in window if _first_token_lc(b) in verbs)
        if verb_starts == 3:
            out.append(BulletFinding(
                rule_id="B3_structure_variation",
                severity="warn",
                location=f"{location}[{i}..{i+2}]",
                bullet_text=window[0][:400],
                issue="3 consecutive bullets all start with a verb — vary opener (lead with system, scale, or outcome)",
            ))
    return out


# ---------- Tier C (resume-level) ----------

def _check_resume_length(total_bullets: int, seniority: str) -> list[BulletFinding]:
    """C1: bullet count within seniority band."""
    lo, hi = _SENIORITY_RANGES.get(seniority.lower(), (10, 30))
    if lo <= total_bullets <= hi:
        return []
    return [BulletFinding(
        rule_id="C1_resume_length",
        severity="warn",
        location="resume",
        bullet_text="",
        issue=f"{total_bullets} bullets is outside the {seniority} band ({lo}–{hi})",
    )]


def _check_buzzword_saturation(total_bullets: int, banned_hits_bullets: int) -> list[BulletFinding]:
    """C2: if >10% of bullets contain any banned phrase, flag whole resume."""
    if total_bullets == 0:
        return []
    pct = banned_hits_bullets / total_bullets
    if pct <= 0.10:
        return []
    return [BulletFinding(
        rule_id="C2_buzzword_saturation",
        severity="error",
        location="resume",
        bullet_text="",
        issue=f"{banned_hits_bullets}/{total_bullets} bullets ({pct:.0%}) contain banned phrases; "
              f"resume may need regeneration",
    )]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_resume(
    generated_resume: dict,
    seniority: str = "mid",
    mode: Literal["report_only", "safe_autofix"] = "report_only",
    strict: bool = False,
):
    """Run the rule set.

    Returns:
      - in `report_only` mode: `ValidationReport`
      - in `safe_autofix` mode: `(mutated_resume_dict, ValidationReport)`
    """
    verbs = get_action_verbs()
    if mode == "safe_autofix":
        resume = deepcopy(generated_resume)
    else:
        resume = generated_resume

    findings: list[BulletFinding] = []
    banned_bullet_count = 0

    # --- Per-bullet (Tier A) ---
    for location, bullet in _all_bullets(resume):
        bullet_findings = []
        bullet_findings.extend(_check_banned_phrase(bullet, location, strict))
        bullet_findings.extend(_check_action_verb_start(bullet, location, verbs))
        bullet_findings.extend(_check_duty_opener(bullet, location))
        bullet_findings.extend(_check_length(bullet, location))
        bullet_findings.extend(_check_em_dash(bullet, location))
        bullet_findings.extend(_check_demonstrating_closer(bullet, location))

        # Tally for C2.
        if any(f.rule_id.startswith("A1_") for f in bullet_findings):
            banned_bullet_count += 1

        findings.extend(bullet_findings)

    # --- Summary (A4) ---
    summary_text = resume.get("professional_summary") or resume.get("summary") or ""
    if isinstance(summary_text, str):
        findings.extend(_check_inside_out(summary_text, "professional_summary"))

    # --- Per-role (Tier B) ---
    for kind, label, idx, bullets in _iter_role_bullets(resume):
        loc = f"{kind}[{idx}].description"
        findings.extend(_check_role_quantification(bullets, loc))
        findings.extend(_check_verb_diversity(bullets, loc))
        findings.extend(_check_structure_variation(bullets, loc, verbs))

    # --- Resume-level (Tier C) ---
    all_bullets_flat = [b for _loc, b in _all_bullets(resume)]
    total_bullets = len(all_bullets_flat)
    findings.extend(_check_resume_length(total_bullets, seniority))
    findings.extend(_check_buzzword_saturation(total_bullets, banned_bullet_count))

    # --- Auto-fix pass (safe_autofix only) ---
    if mode == "safe_autofix":
        _apply_safe_autofix(resume, findings)

    stats = {
        "total_bullets": total_bullets,
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warns": sum(1 for f in findings if f.severity == "warn"),
        "banned_phrase_pct": round(banned_bullet_count / total_bullets, 4) if total_bullets else 0.0,
        "quant_coverage_pct": _quant_coverage(resume),
        "seniority": seniority,
        "mode": mode,
        "strict": strict,
    }
    report = ValidationReport(
        passed=stats["errors"] == 0,
        findings=findings,
        stats=stats,
    )

    return (resume, report) if mode == "safe_autofix" else report


def _quant_coverage(resume: dict) -> float:
    """Fraction of bullets containing at least one quantification regex hit."""
    total = 0
    hits = 0
    for _loc, b in _all_bullets(resume):
        total += 1
        if _has_quantification(b):
            hits += 1
    return round(hits / total, 4) if total else 0.0


def _apply_safe_autofix(resume: dict, findings: list[BulletFinding]) -> None:
    """Chain auto-fixable findings per bullet. Each fix is recomputed against
    the current (possibly already-fixed) text so multiple findings on one
    bullet compose rather than overwrite each other.

    Only A1 (banned phrase with defined replacement) and A6 (em-dash) chain
    fixes — anything else stays flagged but unchanged.

    Mutates `resume` (which was deepcopied by the caller in safe_autofix mode).
    """
    # Group findings by location so we can re-derive each fix against the
    # latest text. Preserve insertion order so findings apply in the order
    # they were generated (em-dash first if it came first, etc.).
    by_location: dict[str, list[BulletFinding]] = {}
    for f in findings:
        if f.rule_id not in ("A1_banned_phrase", "A6_em_dash"):
            continue
        by_location.setdefault(f.location, []).append(f)

    for loc, finds in by_location.items():
        m = re.match(r"^(experience|projects)\[(\d+)\]\.(description|highlights)\[(\d+)\]$", loc)
        if not m:
            continue
        section, role_idx, field, b_idx = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        try:
            text = resume[section][role_idx][field][b_idx]
        except (KeyError, IndexError, TypeError):
            continue
        for f in finds:
            if f.rule_id == "A6_em_dash":
                text = text.replace(_EM_DASH, ", ")
            elif f.rule_id == "A1_banned_phrase":
                # Re-derive replacement against the current text. The phrase
                # is in f.issue between single-quotes; parse it back out.
                m2 = re.search(r"'([^']+)'", f.issue)
                if not m2:
                    continue
                phrase = m2.group(1)
                replacement = BANNED_PHRASES.get(phrase)
                if replacement is None:
                    continue  # delete-only words stay flagged, not auto-removed
                text = _substitute(text, phrase, replacement)
        resume[section][role_idx][field][b_idx] = text
