"""KB integration for the v2 resume pipeline.

The audit (2026-06-01) confirmed the KB is 67 hand-curated RESUME-CRAFT
RULES — NOT facts about the candidate, NOT example resumes, NOT
prioritization-of-user-facts signal. This module wires those rules into
two stages of the v2 pipeline:

  - **Planner**: receives the SENIORITY / INDUSTRY norm chunks as
    calibration context. The ACTUAL cap-adjustment is deterministic
    (driven by ``RoleClassification.seniority``), not parsed from the
    KB prose — KB prose is logged in ``plan.notes`` for explainability.
  - **Generator**: receives BULLET-PATTERN / BANNED-PATTERN / ACTION-
    VERB chunks as PHRASING rules, injected into the per-bullet prompt
    under a hardened-boundary "WRITING RULES" section.

Pre-fetch ONCE per pipeline run; the corpus is 67 chunks total, so
retrieve-once-and-pass-down is correct.

**The integrity boundary is enforced, not just labeled.** The
generator's number-lock and grounding guards (``_ungrounded_numbers``,
``_allowed_numbers_from_facts``) are byte-for-byte unchanged: they
build the allowed-numbers pool ONLY from allocated fact values + their
claim/evidence text, NEVER from KB prose. So even if a future KB chunk
contains an example like "Reduced latency 40%", the 40 cannot enter a
bullet about THIS candidate — the substring "40" never enters the
allowed pool from a KB chunk, only from a fact. The boundary survives
even if the corpus grows examples.

When RAG is disabled / retrieval returns nothing / retrieval errors,
this module returns ``[]`` and downstream code treats it as a no-op —
mirroring v1's "first thing dropped under token pressure" behavior.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Categories that drive STRUCTURAL calibration in the planner. These
# tell us things like "intern → education-first, page-conscious caps;
# senior → wider bullet allowances".
_PLANNER_CALIBRATION_TYPES: set = {"seniority_norm", "industry_norm"}

# Categories that drive PHRASING in the generator. These bite at the
# per-bullet level — STAR/XYZ shape, banned openings, AI-tells.
_GENERATOR_PHRASING_TYPES: set = {
    "bullet_pattern", "banned_pattern", "action_verb",
}


# Deterministic per-seniority calibration. The KB prose is advisory
# (logged in plan.notes); the cap-adjustment itself is data-table
# driven so a misclassification or a phrasing change in the KB never
# silently distorts allocation. Conservative: when seniority is
# unknown/missing/empty, return ``None`` so the planner falls back
# to its own ``DEFAULT_SECTION_CAPS``.
_SENIORITY_CAP_OVERRIDES: dict[str, dict[str, int]] = {
    # Interns: tightly page-conscious — education leads, fewer
    # experience bullets, projects matter more.
    "intern": {
        "skills": 12,
        "experience": 8,
        "projects": 6,
        "certifications": 6,
    },
    # Juniors: 4-5 bullets per role across 1-2 roles fits a page.
    "junior": {
        "skills": 13,
        "experience": 10,
        "projects": 7,
        "certifications": 7,
    },
    # Mid: current defaults are tuned here — explicit to make the
    # calibration table the source of truth.
    "mid": {
        "skills": 15,
        "experience": 12,
        "projects": 8,
        "certifications": 8,
    },
    # Senior / staff: more room for experience, slightly tighter
    # projects (track record speaks louder than side work).
    "senior": {
        "skills": 18,
        "experience": 16,
        "projects": 6,
        "certifications": 8,
    },
    "staff": {
        "skills": 18,
        "experience": 18,
        "projects": 5,
        "certifications": 8,
    },
}


def prefetch_kb_for_pipeline(
    jd_text: str,
    classification=None,
    *,
    k: int = 6,
    universal_share: int = 3,
    enabled: Optional[bool] = None,
) -> list:
    """Pre-fetch KB chunks ONCE per v2 pipeline run.

    Args:
      jd_text: JD body — embedded once by the retriever.
      classification: ``RoleClassification`` from ``classify_for_jd``,
        or ``None`` (a fail-safe stub is used).
      k / universal_share: forwarded to the retriever.
      enabled: explicit on/off. When ``None`` (the production path),
        reads ``settings.RAG_ENABLED`` so the gate matches v1's.

    Returns ``[]`` on any of:
      - RAG disabled,
      - empty/missing JD text,
      - retrieval raises,
      - retrieval returns nothing.

    The empty-list return is intentional — every downstream consumer
    of this function MUST tolerate an empty list and produce the same
    output as the pre-KB pipeline. KB is nice-to-have, never load-
    bearing (mirrors v1's behavior — the standards block is the first
    thing dropped under token pressure).
    """
    if enabled is None:
        try:
            from django.conf import settings as _s
            enabled = bool(getattr(_s, "RAG_ENABLED", True))
        except Exception:  # noqa: BLE001 — settings unavailable in some test contexts
            enabled = False
    if not enabled:
        logger.info("kb_integration: RAG disabled — pipeline runs KB-free.")
        return []
    if not (jd_text or "").strip():
        logger.info("kb_integration: empty JD — no KB retrieval.")
        return []

    # Stub classification when caller didn't supply one. The retriever
    # needs a role tag; the stub maps to the same "software_engineer"
    # default the retriever itself would fall back to.
    if classification is None:
        try:
            from profiles.services.role_classifier import RoleClassification
            classification = RoleClassification(
                primary_role="Software Engineer",
                seniority="mid",
                tech_stack_signals=[],
                region="global",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb_integration: classifier stub unavailable (%s); "
                "skipping KB.", type(exc).__name__,
            )
            return []

    try:
        from profiles.services.knowledge_retriever import retrieve_chunks
        chunks = retrieve_chunks(
            jd_text,
            classification,
            k=k,
            universal_share=universal_share,
        )
    except Exception as exc:  # noqa: BLE001 — retrieval failure must never break gen
        logger.warning(
            "kb_integration: retrieval failed (%s); pipeline runs KB-free.",
            type(exc).__name__,
        )
        return []

    chunks = list(chunks or [])
    logger.info(
        "kb_integration: pre-fetched %d KB chunk(s) for pipeline run "
        "(by type: %s).",
        len(chunks),
        _by_type_summary(chunks),
    )
    return chunks


def split_kb_chunks(chunks: list) -> tuple[list, list]:
    """Split a pre-fetched KB chunk list into:
      - ``calibration`` — for the planner (seniority + industry norms).
      - ``phrasing`` — for the generator (bullet patterns, banned
        patterns, action verbs).

    Chunks whose type matches neither (ats_rule / mena_context) are
    treated as PHRASING — they're universally applicable formatting
    rules the generator should see (ATS section names, MENA file
    naming, etc.). Better to err on the side of "the generator gets
    them" than to silently discard them."""
    calibration: list = []
    phrasing: list = []
    for c in chunks or []:
        t = getattr(c, "type", "") or ""
        if t in _PLANNER_CALIBRATION_TYPES:
            calibration.append(c)
        else:
            phrasing.append(c)
    return calibration, phrasing


def seniority_calibration(seniority: str) -> Optional[dict[str, int]]:
    """Deterministic section-cap overrides for a seniority string.

    Returns ``None`` for unknown / missing seniority so the planner
    falls back to its existing ``DEFAULT_SECTION_CAPS`` — the
    CONSERVATIVE-DEFAULT rule the integration spec requires.
    """
    if not seniority:
        return None
    key = str(seniority).strip().lower()
    return _SENIORITY_CAP_OVERRIDES.get(key)


# ---------------------------------------------------------------------------
# Writing-rules block — the labeled boundary section the generator
# injects into the per-bullet prompt.
# ---------------------------------------------------------------------------


_WRITING_RULES_HEADER = (
    "=== WRITING RULES (general resume conventions — apply to PHRASING; "
    "these are NOT facts about the candidate, NEVER state anything here "
    "as the candidate's accomplishment) ==="
)
_WRITING_RULES_FOOTER = (
    "=== END WRITING RULES — what follows are the candidate's FACTS, "
    "use ONLY those for content ==="
)


def format_writing_rules_block(phrasing_chunks: list) -> str:
    """Render phrasing chunks as a clearly-labeled prompt section.

    The header + footer are the hardened boundary: the prompt
    structure tells the LLM "rules above this line, facts below it".
    Even if a future KB chunk contains an example bullet with
    numbers, the generator's number-lock will still drop any bullet
    whose number isn't in the allocated-facts pool — the boundary is
    enforced structurally, not just by the prompt's wording.

    Returns the empty string when no phrasing chunks are supplied so
    the caller can drop the block entirely without any "(no rules)"
    placeholder text leaking into the prompt.
    """
    if not phrasing_chunks:
        return ""
    lines = [_WRITING_RULES_HEADER, ""]
    for i, c in enumerate(phrasing_chunks, start=1):
        title = getattr(c, "title", "") or getattr(c, "kb_id", f"rule_{i}")
        rule_text = (
            getattr(c, "concrete_rule", None)
            or getattr(c, "body", None)
            or ""
        ).strip()
        if not rule_text:
            continue
        lines.append(f"[R{i}] {title}")
        lines.append(rule_text)
        lines.append("")
    lines.append(_WRITING_RULES_FOOTER)
    return "\n".join(lines).rstrip()


def _by_type_summary(chunks: list) -> str:
    """Diagnostic — `{type: count}` string for the prefetch log."""
    counts: dict[str, int] = {}
    for c in chunks or []:
        t = getattr(c, "type", "") or "unknown"
        counts[t] = counts.get(t, 0) + 1
    if not counts:
        return "{}"
    return "{" + ", ".join(
        f"{t}={n}" for t, n in sorted(counts.items())
    ) + "}"
