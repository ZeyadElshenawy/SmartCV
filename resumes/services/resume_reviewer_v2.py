"""v2 content-only review/regen orchestrator.

The CONTENT review layer for the v2 pipeline. NOT a visual-render
review (that's the presentation pillar — a future task). This module
critiques bullet TEXT, classifies findings via the unchanged v1
``findings_classifier`` (the source-agnostic, fail-safe-correct policy),
and re-generates AUTO_FIXABLE blockers through v2's OWN guarded
generator — never v1's ``regenerate_section``.

The integrity claim (re-stated structurally):

  - regen path is ``_generate_one_bullet`` (the function with the
    number-lock + writing-rules + regenerate-once-then-drop logic). A
    regenerated bullet has the SAME structural guarantees as the
    original.
  - ``regenerate_section`` is NEVER called from this module. (Verified
    by an explicit test that mocks it to raise.)
  - ``_allowed_numbers_from_facts`` is rebuilt from the bullet's
    allocated facts before each regen attempt — the review's feedback
    can change WORDING, never the allowed-numbers pool.

Loop control mirrors v1: bounded passes; cap-exhaust demotes
unresolved AUTO_FIX findings to ADVISORY. The same fail-safe-correct
classification policy applies: NEEDS_USER_INPUT findings BYPASS the
loop entirely (regenerating them would fabricate or delete the user's
real content) and surface to the user as "Confirm or complete".
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from resumes.services.fact_store import FactStore, FactRecord
from resumes.services.resume_generator_v2 import (
    GeneratedResumeV2,
    GeneratedSection,
    EntityBlock,
    GeneratedBullet,
    FabricationEvent,
    _generate_one_bullet,
    _allowed_numbers_from_facts,
)
from resumes.services.resume_planner_v2 import PlanResult
from resumes.services.findings_classifier import (
    BUCKET_ADVISORY,
    BUCKET_AUTO_FIX,
    BUCKET_USER_INPUT,
    classify_finding,
)

logger = logging.getLogger(__name__)


# Default review-cap. Mirrors v1's SUPERVISOR_MAX_REVISION_ROUNDS
# semantics: cap+1 total passes (initial scan, then up to ``cap``
# regen rounds).
DEFAULT_MAX_REVISION_ROUNDS = 1


# ---------------------------------------------------------------------------
# Bullet-text checks. These match the KB's banned-pattern + action-verb
# concrete_rule contents (action_verbs_001 + banned_patterns_002).
# Hardcoded here because the reviewer needs deterministic regex
# checks, not LLM prose parsing — keep in sync with the KB chunks.
# ---------------------------------------------------------------------------


# Banned bullet openings — from action_verbs_001's "Never start a
# bullet with..." rule. Match at the start of the (lowercased,
# whitespace-stripped) bullet text.
_BANNED_OPENINGS: tuple[str, ...] = (
    "utilized",
    "utilised",
    "leveraged",
    "spearheaded",
    "helped",
    "worked on",
    "was responsible for",
    "responsible for",
    "contributed to",
    "assisted with",
    "tasked with",
    "in charge of",
    "duties included",
)


# AI-tell endings (participial closers) — from banned_patterns_002's
# "any bullet ending in a participial phrase" rule.
_AI_TELL_ENDING_RE = re.compile(
    r"\b(?:demonstrating|showcasing|leveraging|exhibiting|highlighting)"
    r"\b[^.!?]*[.!?]?\s*$",
    re.IGNORECASE,
)


# Empty intensifiers — from banned_patterns_002's "empty adverb
# intensifiers" rule.
_EMPTY_INTENSIFIER_RE = re.compile(
    r"\b(?:successfully|effectively|efficiently|strategically|seamlessly)\b",
    re.IGNORECASE,
)


# Where-string patterns the orchestrator parses to locate a bullet:
#
#   "summary[i]"                  → resume.sections['summary'].bullets[i]
#   "experience/{entity_id}[i]"   → an experience entity's bullets[i]
#   "projects/{entity_id}[i]"     → a project entity's bullets[i]
#
# The entity_id may contain slashes (it's typically a URL or a
# canonical path), so the regex is non-greedy on entity_id and anchored
# on the trailing ``[N]``.
_BULLET_WHERE_RE = re.compile(
    r"^(?P<section>summary|experience|projects)"
    r"(?:/(?P<entity_id>.+?))?"
    r"\[(?P<i>\d+)\]$"
)


# ---------------------------------------------------------------------------
# 1. Review — produce findings from the generated v2 resume.
# ---------------------------------------------------------------------------


def _scan_bullet(text: str) -> list[dict]:
    """Deterministic text checks on one bullet → list of bullet
    finding dicts in the v1 validation_report['findings'] shape
    (``rule_id``, ``severity``, ``where``, ``detail``, ``fix``).
    Caller assigns ``where``.

    Findings produced here all map to AUTO_FIX in the classifier's
    bullet table (``_BULLET_RULE_BUCKET``) — phrasing, not facts.
    """
    findings: list[dict] = []
    text = (text or "").strip()
    if not text:
        return findings
    lower = text.lower()
    # Banned-opening check — strip leading punctuation/whitespace
    # before testing so "Utilized..." catches even if the LLM
    # prefixed it with a stray bullet character or quote.
    cleaned_lower = re.sub(r"^[\s\"'•\-\*]+", "", lower)
    for banned in _BANNED_OPENINGS:
        if cleaned_lower.startswith(banned):
            findings.append({
                "rule_id": "A1_banned_phrase",
                "severity": "blocking",
                "where": "",
                "detail": f"bullet starts with banned opening {banned!r}",
                "fix": "Replace the opening with a strong outcome-leading verb.",
            })
            break
    # AI-tell ending.
    if _AI_TELL_ENDING_RE.search(text):
        findings.append({
            "rule_id": "A7_demonstrating_closer",
            "severity": "blocking",
            "where": "",
            "detail": "bullet ends with an AI-tell participial phrase",
            "fix": (
                "Drop the trailing 'demonstrating/showcasing/leveraging X' "
                "tail; end on the concrete outcome."
            ),
        })
    # Empty intensifier — match maps to A1_banned_phrase per the KB's
    # "buzzword saturation" rule.
    if _EMPTY_INTENSIFIER_RE.search(text):
        findings.append({
            "rule_id": "A1_banned_phrase",
            "severity": "blocking",
            "where": "",
            "detail": (
                "bullet contains an empty intensifier "
                "(successfully/effectively/efficiently/strategically/seamlessly)"
            ),
            "fix": "Remove the intensifier; let the outcome speak.",
        })
    return findings


def _grounding_findings_from_events(
    events: list[FabricationEvent] | None,
) -> list[dict]:
    """Turn the generator's fabrication_events into v1-shaped
    grounding_findings.

      - ``action == 'dropped'``  → BLOCKING. A bullet vanished because
        a number couldn't be grounded after regen. Maps to
        ``unsupported_metric`` (USER_INPUT in the classifier — the
        user must verify whether the metric is real before adding back).
      - ``action == 'regenerated'`` → WARNING. The guard caught it on
        the first attempt and re-tried successfully. Advisory.
    """
    out: list[dict] = []
    for ev in events or []:
        action = getattr(ev, "action", "") or ""
        nums = getattr(ev, "ungrounded_numbers", []) or []
        section = getattr(ev, "section", "") or ""
        entity_id = getattr(ev, "entity_id", "") or ""
        bullet_text = getattr(ev, "bullet_text", "") or ""
        out.append({
            "kind": "unsupported_metric",
            "severity": "blocking" if action == "dropped" else "warning",
            "where": f"{section}/{entity_id}" if entity_id else section,
            "detail": (
                f"action={action} ungrounded numbers={nums} "
                f"original bullet: {bullet_text[:120]!r}"
            ),
        })
    return out


def build_v2_validation_report(resume: GeneratedResumeV2) -> dict:
    """Assemble a v1-shaped ``validation_report`` from a v2 resume.

    This is the SHIM that lets the unchanged ``findings_classifier``
    + ``findings_presenter`` work on v2 output. Keys produced match
    v1's validation_report contract:

      - ``findings``             — bullet-rule findings (rule_id-based)
      - ``grounding_findings``   — from fabrication_events
      - ``regression_findings``  — empty for v2 (no v1-style diff)
      - ``supervisor_findings``  — empty here (no visual review)
    """
    findings: list[dict] = []

    # Summary — one bullet.
    summary_sec = resume.sections.get("summary")
    if summary_sec is not None:
        for i, b in enumerate(summary_sec.bullets or []):
            for f in _scan_bullet(b.text):
                f["where"] = f"summary[{i}]"
                findings.append(f)

    # Experience + projects — per-entity, per-bullet.
    for section_name in ("experience", "projects"):
        sec = resume.sections.get(section_name)
        if sec is None or not sec.entities:
            continue
        for ent in sec.entities:
            for i, b in enumerate(ent.bullets):
                for f in _scan_bullet(b.text):
                    f["where"] = f"{section_name}/{ent.entity_id}[{i}]"
                    findings.append(f)

    grounding = _grounding_findings_from_events(resume.fabrication_events)

    return {
        "findings": findings,
        "grounding_findings": grounding,
        "regression_findings": [],
        "supervisor_findings": [],
    }


# ---------------------------------------------------------------------------
# 2. Classify — runs the v1 findings_classifier UNCHANGED.
# ---------------------------------------------------------------------------


def classify_v2_findings(vr: dict) -> dict[str, list[tuple[str, dict]]]:
    """Bucket the v2 validation_report's findings via the unchanged v1
    classifier (source-agnostic; fail-safe NEEDS_USER_INPUT on unknown).

    Returns ``{bucket: [(source, finding_dict), ...]}`` so the regen
    step can target specific findings and the surfacing step can
    enumerate USER_INPUT items.
    """
    buckets: dict[str, list[tuple[str, dict]]] = {
        BUCKET_AUTO_FIX: [],
        BUCKET_USER_INPUT: [],
        BUCKET_ADVISORY: [],
    }
    for source, key in (
        ("bullet", "findings"),
        ("grounding", "grounding_findings"),
        ("supervisor", "supervisor_findings"),
        ("regression", "regression_findings"),
    ):
        for f in (vr.get(key) or []):
            bucket = classify_finding(source, f)
            buckets[bucket].append((source, f))
    return buckets


# ---------------------------------------------------------------------------
# 3. Regen — v2-NATIVE, the load-bearing part.
#
# This function is the entire reason the v2 reviewer exists as its own
# module rather than reusing v1's supervisor: regen routes through
# _generate_one_bullet (number-lock + writing-rules + regen-once-drop),
# never through v1's regenerate_section (no structural guards).
# ---------------------------------------------------------------------------


def _facts_for_bullet(
    store: FactStore, bullet: GeneratedBullet,
) -> list[FactRecord]:
    """Look up the allocated FactRecords for a bullet by fact_ids.
    Skips ids missing from the store (defensive)."""
    out: list[FactRecord] = []
    for fid in bullet.fact_ids or []:
        f = store.get(fid)
        if f is not None:
            out.append(f)
    return out


def _regenerate_v2_bullet(
    *,
    store: FactStore,
    section_name: str,
    entity_id: str,
    entity_display: str,
    bullet: GeneratedBullet,
    feedback_text: str,
    writing_rules_block: str = "",
    events: list[FabricationEvent],
    job_title: str = "",
) -> Optional[GeneratedBullet]:
    """Regenerate ONE bullet through v2's GUARDED generator.

    NEVER calls v1's ``regenerate_section``. Builds the
    ``allowed_numbers`` pool from the bullet's ORIGINAL allocated
    facts so a regen can't reach for a number outside that pool —
    even if the review feedback (a free-form string) mentions one.

    Returns the new ``GeneratedBullet`` or ``None`` if the guard
    dropped it. Caller decides whether to replace or remove.
    """
    facts = _facts_for_bullet(store, bullet)
    if not facts:
        logger.warning(
            "resume_reviewer_v2: cannot regenerate bullet — no facts "
            "found in store for fact_ids=%s", bullet.fact_ids,
        )
        return None
    allowed_numbers = _allowed_numbers_from_facts(facts)
    role_hint = (
        f"a {section_name} entry: {entity_display!r}"
        + (f" (targeting {job_title})" if job_title else "")
    )
    return _generate_one_bullet(
        section=section_name,
        entity_id=entity_id,
        role_hint=role_hint,
        facts=facts,
        allowed_numbers=allowed_numbers,
        events=events,
        writing_rules_block=writing_rules_block,
        regen_feedback=feedback_text,
    )


# ---------------------------------------------------------------------------
# Locator — parse a finding's ``where`` string into (section, entity_id,
# bullet_index). Returns None for shapes the reviewer can't act on.
# ---------------------------------------------------------------------------


def _locate_bullet(
    where: str, resume: GeneratedResumeV2,
) -> Optional[tuple[str, Optional[int], int, EntityBlock | None]]:
    """Map a ``where`` string to (section_name, entity_idx, bullet_idx,
    entity_block) — or None when the location isn't a bullet position.

    ``entity_idx`` is None for the summary section; the bullet still
    lives at ``resume.sections['summary'].bullets[bullet_idx]``.
    """
    if not where:
        return None
    m = _BULLET_WHERE_RE.match(where)
    if not m:
        return None
    section_name = m.group("section")
    entity_id = m.group("entity_id") or ""
    bullet_idx = int(m.group("i"))
    section = resume.sections.get(section_name)
    if section is None:
        return None
    if section_name == "summary":
        if bullet_idx >= len(section.bullets or []):
            return None
        return section_name, None, bullet_idx, None
    # experience / projects.
    if not section.entities:
        return None
    for idx, ent in enumerate(section.entities):
        if ent.entity_id == entity_id:
            if bullet_idx >= len(ent.bullets):
                return None
            return section_name, idx, bullet_idx, ent
    return None


# ---------------------------------------------------------------------------
# 4. Orchestrator entry point.
# ---------------------------------------------------------------------------


def review_and_regenerate(
    resume: GeneratedResumeV2,
    *,
    store: FactStore,
    plan: PlanResult,
    job_title: str = "",
    writing_rules_block: str = "",
    max_rounds: int = DEFAULT_MAX_REVISION_ROUNDS,
) -> tuple[GeneratedResumeV2, dict]:
    """Review the v2 resume's BULLET TEXT (content-only; no visual
    review), classify findings, regenerate AUTO_FIXABLE blockers
    through v2's guarded generator, surface NEEDS_USER_INPUT to the
    user, demote unresolved AUTO_FIX on cap-exhaust.

    Args:
      resume: the GeneratedResumeV2 emitted by ``generate_resume_v2``.
      store: the same FactStore the plan was built from (must contain
        every fact id every bullet references — needed for regen).
      plan: the PlanResult that produced ``resume`` (kept in the
        signature for symmetry with the rest of the pipeline; the
        current reviewer doesn't read it but per-section-aware
        rule-extensions will).
      job_title: optional, threaded into the regen role_hint.
      writing_rules_block: the KB labelled-boundary block already in
        use for generation. Passed verbatim into regen so the same
        WRITING RULES apply.
      max_rounds: cap on regen rounds. Mirrors v1's
        SUPERVISOR_MAX_REVISION_ROUNDS semantics: cap+1 passes
        (initial scan, then up to ``cap`` regen rounds).

    Returns ``(revised_resume, report_dict)``. The report shape is
    v1-shape-compatible so ``findings_presenter.build_review_summary``
    can consume it via the shim.
    """
    current = resume
    rounds_run = 0
    resolved: list[dict] = []
    demoted: list[dict] = []
    user_input_findings: list[dict] = []
    advisory_findings: list[dict] = []

    for round_i in range(max_rounds + 1):
        rounds_run = round_i + 1
        vr = build_v2_validation_report(current)
        buckets = classify_v2_findings(vr)
        auto_fix = buckets[BUCKET_AUTO_FIX]
        # USER_INPUT + ADVISORY snapshot on the FIRST pass so the
        # report surfaces what the user needs to do — even if later
        # regens change resolution count.
        if round_i == 0:
            user_input_findings = [f for _src, f in buckets[BUCKET_USER_INPUT]]
            advisory_findings = [f for _src, f in buckets[BUCKET_ADVISORY]]

        if not auto_fix:
            # Nothing fixable left → terminate cleanly.
            logger.info(
                "resume_reviewer_v2: round %d — 0 auto-fix findings; "
                "review complete.", round_i,
            )
            break

        if round_i >= max_rounds:
            # Cap-exhaust: demote unresolved AUTO_FIX to ADVISORY.
            # Mirrors v1's supervised loop's cap-exhaust demotion.
            for _src, finding in auto_fix:
                d = dict(finding)
                d["original_severity"] = finding.get("severity", "")
                d["severity"] = "warning"
                d["demoted_reason"] = "review_cap_exhausted"
                demoted.append(d)
            logger.info(
                "resume_reviewer_v2: cap (%d) exhausted; demoted %d "
                "unresolved AUTO_FIX finding(s) to ADVISORY.",
                max_rounds, len(auto_fix),
            )
            break

        current, fixed_this_round = _apply_regen_round(
            current,
            auto_fix_findings=[f for _src, f in auto_fix],
            store=store,
            writing_rules_block=writing_rules_block,
            job_title=job_title,
        )
        resolved.extend(fixed_this_round)
        if not fixed_this_round:
            # No bullet-locatable AUTO_FIX → nothing actionable; demote
            # the unresolved set and break. Prevents infinite loops on
            # findings whose ``where`` doesn't parse.
            for _src, finding in auto_fix:
                d = dict(finding)
                d["original_severity"] = finding.get("severity", "")
                d["severity"] = "warning"
                d["demoted_reason"] = "no_actionable_target"
                demoted.append(d)
            logger.info(
                "resume_reviewer_v2: %d AUTO_FIX finding(s) had no "
                "actionable bullet target; demoted to ADVISORY.",
                len(auto_fix),
            )
            break

    final_vr = build_v2_validation_report(current)
    report = {
        "rounds_run": rounds_run,
        "validation_report": final_vr,
        "resolved": resolved,
        "demoted": demoted,
        "user_input": user_input_findings,
        "advisory": advisory_findings,
    }
    return current, report


def _apply_regen_round(
    resume: GeneratedResumeV2,
    *,
    auto_fix_findings: list[dict],
    store: FactStore,
    writing_rules_block: str,
    job_title: str,
) -> tuple[GeneratedResumeV2, list[dict]]:
    """Apply one round of regenerations.

    Pydantic models are immutable; rebuild new sections rather than
    mutate. Findings within the same entity are processed in
    DESCENDING bullet-index order so a drop doesn't shift earlier
    indices for other findings in the same entity.

    Returns ``(new_resume, resolved_findings)``.
    """
    # Bucket findings by (section_name, entity_id_or_None) and sort
    # each bucket by descending bullet_idx.
    by_entity: dict[tuple[str, Optional[int]], list[tuple[int, dict]]] = {}
    section_summary_findings: dict[int, dict] = {}

    resolved: list[dict] = []
    new_sections: dict[str, GeneratedSection] = dict(resume.sections)

    for finding in auto_fix_findings:
        loc = _locate_bullet(finding.get("where", ""), resume)
        if loc is None:
            continue
        section_name, entity_idx, bullet_idx, _entity = loc
        if section_name == "summary":
            # Multiple findings for the same summary bullet collapse —
            # the latest wins (the feedback text is concatenated below).
            section_summary_findings.setdefault(bullet_idx, finding)
        else:
            key = (section_name, entity_idx)
            by_entity.setdefault(key, []).append((bullet_idx, finding))

    # --- Summary section regen (no entity) ---
    if section_summary_findings:
        summary_sec = new_sections.get("summary")
        if summary_sec is not None and summary_sec.bullets:
            # Process descending so drops don't shift earlier indices.
            for bullet_idx in sorted(
                section_summary_findings.keys(), reverse=True,
            ):
                finding = section_summary_findings[bullet_idx]
                if bullet_idx >= len(summary_sec.bullets):
                    continue
                bullet = summary_sec.bullets[bullet_idx]
                feedback = (
                    (finding.get("detail") or "").strip()
                    + (
                        " — " + finding.get("fix", "")
                        if finding.get("fix") else ""
                    )
                ).strip()
                events: list[FabricationEvent] = []
                new_bullet = _regenerate_v2_bullet(
                    store=store,
                    section_name="summary",
                    entity_id="",
                    entity_display="professional summary",
                    bullet=bullet,
                    feedback_text=feedback,
                    writing_rules_block=writing_rules_block,
                    events=events,
                    job_title=job_title,
                )
                new_bullets = list(summary_sec.bullets)
                if new_bullet is not None:
                    new_bullets[bullet_idx] = new_bullet
                    resolved.append(dict(
                        finding,
                        resolved_to=new_bullet.text,
                    ))
                else:
                    new_bullets.pop(bullet_idx)
                    resolved.append(dict(finding, resolved_to="(dropped)"))
                # Update both summary_text and bullets.
                new_summary_text = (
                    new_bullets[0].text if new_bullets else ""
                )
                summary_sec = summary_sec.model_copy(update={
                    "bullets": new_bullets,
                    "summary_text": new_summary_text,
                })
        if summary_sec is not None:
            new_sections["summary"] = summary_sec

    # --- Experience + Projects entity regen ---
    for (section_name, entity_idx), findings_list in by_entity.items():
        section = new_sections.get(section_name)
        if section is None or not section.entities:
            continue
        entities = list(section.entities)
        entity = entities[entity_idx]
        # Sort findings descending by bullet_idx so drops are safe.
        findings_list.sort(key=lambda pair: -pair[0])
        new_bullets = list(entity.bullets)
        for bullet_idx, finding in findings_list:
            if bullet_idx >= len(new_bullets):
                continue
            bullet = new_bullets[bullet_idx]
            feedback = (
                (finding.get("detail") or "").strip()
                + (
                    " — " + finding.get("fix", "")
                    if finding.get("fix") else ""
                )
            ).strip()
            events: list[FabricationEvent] = []
            new_bullet = _regenerate_v2_bullet(
                store=store,
                section_name=section_name,
                entity_id=entity.entity_id,
                entity_display=entity.entity_display,
                bullet=bullet,
                feedback_text=feedback,
                writing_rules_block=writing_rules_block,
                events=events,
                job_title=job_title,
            )
            if new_bullet is not None:
                new_bullets[bullet_idx] = new_bullet
                resolved.append(dict(finding, resolved_to=new_bullet.text))
            else:
                new_bullets.pop(bullet_idx)
                resolved.append(dict(finding, resolved_to="(dropped)"))
        new_entity = entity.model_copy(update={"bullets": new_bullets})
        entities[entity_idx] = new_entity
        new_section = section.model_copy(update={"entities": entities})
        new_sections[section_name] = new_section

    new_resume = resume.model_copy(update={"sections": new_sections})
    return new_resume, resolved
