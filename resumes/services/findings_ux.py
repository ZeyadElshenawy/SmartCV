"""User-facing findings buckets + plain-language copy.

Wraps the existing ``findings_presenter.build_review_summary`` /
``findings_classifier.classify_*`` pipeline into a 3-bucket view the
editor UI consumes via a single JSON endpoint:

  - auto_fix    → "Can auto-fix"     (amber)   action: Fix it
  - user_input  → "Needs your input" (red)     action: Add/Confirm
  - advisory    → "Suggestion"       (gray)    action: Dismiss

Per the locked design:

  * The internal ``rule_id`` and severity vocabulary NEVER leak into
    the user-facing ``message`` field. The mapping table below is the
    single source of truth for "what does the user see".
  * Each finding carries a stable ``id`` (hash of bucket + location +
    rule_id) so the Fix-It endpoint can address it independently of
    list ordering.
  * The fail-safe in ``findings_classifier`` (unknown → user_input)
    flows through unchanged.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from resumes.services.findings_classifier import (
    BUCKET_ADVISORY,
    BUCKET_AUTO_FIX,
    BUCKET_USER_INPUT,
)


BUCKET_META = {
    # ``label`` and ``action`` are USER-FACING copy. The honest model
    # behind "auto_fix" is *propose → user approves*: the system
    # generates a guarded regen (number-lock + grounding intact), then
    # surfaces a before/after panel; the user accepts or rejects. The
    # label was "Can auto-fix" — misleading because the rewrite never
    # auto-applies. "Suggested rewrite" matches the actual interaction.
    BUCKET_AUTO_FIX:   {"label": "Suggested rewrite", "color": "amber", "action": "Suggest a rewrite", "fixable": True},
    BUCKET_USER_INPUT: {"label": "Needs your input",  "color": "red",   "action": "Add/Confirm",       "fixable": False},
    BUCKET_ADVISORY:   {"label": "Suggestion",        "color": "gray",  "action": "Dismiss",           "fixable": False},
}


# Internal rule prefixes / kinds → user-facing instruction text. The
# copy says what to DO, not the rule name. Keep these short (one line);
# the editor renders them as inline hints, no jargon.
_PLAIN_MESSAGES = {
    # Bullet rules — every entry is an INSTRUCTION the user can act on,
    # not the rule's internal name. Avoid the rule-name vocabulary
    # ("inside-out opener", "structural variation", "quantification") —
    # the user should never see those phrases.
    "A1_banned_phrase":        "Open with what you built, not 'Utilized' / 'Leveraged' — name the system or the outcome.",
    "A1_banned_jargon":        "Cut the buzzword and keep the concrete outcome.",
    "A2_action_verb_start":    "Start with a strong action verb — Shipped, Cut, Designed, Built.",
    "A3_duty_opener":          "Reframe as a result, not a duty — drop 'Responsible for…'.",
    "A4_inside_out_summary":   "Open with what you built or the outcome — not 'with over X years of experience…'.",
    "A5_length_long":          "Trim to one line — 15–25 words.",
    "A5_length_short":         "This bullet is too thin to land — add a concrete outcome.",
    "A6_em_dash":              "Replace the em-dash with a comma, or rewrite the sentence.",
    "A7_demonstrating_closer": "End on the concrete outcome — drop the 'demonstrating / showcasing X' tail.",
    "B1_quantification":       "Add a number, metric, or timeframe to this role if you have one.",
    "B2_verb_diversity":       "These bullets all open with the same verb — vary the opening word.",
    "B3_structure_variation":  "These bullets all start the same way — vary the opening word.",
    "C1_resume_length":        "Trim the résumé to one page if you can — your current length reads as padding.",
    "C2_buzzword_saturation":  "Too many buzzwords — keep one or two and let the outcomes carry the rest.",
    # Grounding ------------------------------------------------------
    "unsupported_skill":  "Confirm this skill appears in your CV or projects — or remove it.",
    "unsupported_metric": "Confirm this number against your original source — or remove the figure.",
    "drop_skill_leak":    "A non-relevant skill leaked through — the editor can clean it up.",
    # Regression -----------------------------------------------------
    "skill_loss":         "A skill from your previous résumé is missing — review whether to keep it.",
    "bullet_count_drop":  "Fewer bullets than the previous version — confirm the trim was intentional.",
    "metric_loss":        "A metric from the previous version is gone — confirm the source still supports it.",
    # Supervisor categories ------------------------------------------
    "supervisor":         "The reviewer flagged this — read the note and decide.",
}


# Phrases the user must NEVER see, even when the friendly map below
# misses a key. Used by ``enrich_annotations_with_plain_messages`` to
# scrub any rule-name vocabulary from the displayed fallback text.
# Supervisor `issue` echo guard. The LLM occasionally returns the
# resume's own content (the summary's text, an experience bullet)
# verbatim in the `issue` field — the chip then renders that content
# back at the user as "advice" with a Dismiss button. The check is
# content-substring based (NOT length): we build a normalized corpus
# from the resume's prose (summary + experience / projects
# descriptions) and SUPPRESS any supervisor finding whose `detail`
# overlaps with that corpus in either direction. Length plays no role
# — a genuine 400-character critique survives, a 30-character echo
# of a bullet's text does not.
_SUPERVISOR_ECHO_FALLBACK = (
    "The reviewer flagged this — review or dismiss."
)


def _normalize_for_echo(s: str) -> str:
    """Lowercase + collapse whitespace. Used on BOTH sides of the
    substring check so the comparison is whitespace-insensitive and
    case-insensitive."""
    if not isinstance(s, str):
        return ""
    import re as _re
    return _re.sub(r"\s+", " ", s.lower()).strip()


def _collect_resume_content_corpus(content) -> str:
    """Concatenate the resume's prose into a normalized corpus the
    echo guard can substring-check against.

    Covers:
      * professional_summary
      * experience[].description  (str or list[str])
      * projects[].description    (str or list[str])
    """
    if not isinstance(content, dict):
        return ""
    parts: list[str] = []
    summary = content.get("professional_summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(summary)
    for section_key in ("experience", "projects"):
        section = content.get(section_key) or []
        if not isinstance(section, list):
            continue
        for item in section:
            if not isinstance(item, dict):
                continue
            desc = item.get("description")
            if isinstance(desc, str):
                parts.append(desc)
            elif isinstance(desc, list):
                for line in desc:
                    if isinstance(line, str):
                        parts.append(line)
    return _normalize_for_echo(" ".join(parts))


_MIN_ECHO_OVERLAP_CHARS = 20  # ~4-5 words; ignores incidental 1-3 char overlaps


def _is_echo_of_content(detail: str, corpus: str) -> bool:
    """True when ``detail`` and ``corpus`` overlap by substring in
    either direction AND the matched piece is non-trivially long —
    i.e. the supervisor's ``issue`` field genuinely echoes a chunk of
    the resume's content.

    The minimum-overlap rule is a quality check on the MATCH, not a
    length filter on ``detail`` (so a 250-char real critique still
    passes through verbatim — length of detail plays no role). It
    rules out absurd matches like a corpus of "s" appearing inside
    every detail that contains the letter s.

    Both sides are normalized (lowercased, whitespace collapsed)
    before comparison. An empty input returns False so the empty-
    detail path uses the separate fallback-stub branch.
    """
    if not detail or not corpus:
        return False
    d = _normalize_for_echo(detail)
    if not d:
        return False
    # detail ⊆ corpus → the matched piece is detail itself
    if d in corpus and len(d) >= _MIN_ECHO_OVERLAP_CHARS:
        return True
    # corpus ⊆ detail → the matched piece is corpus itself
    if corpus in d and len(corpus) >= _MIN_ECHO_OVERLAP_CHARS:
        return True
    return False


_RULE_NAME_PHRASES_TO_SCRUB = (
    "inside-out", "inside out",
    "structural variation",
    "quantification",
    "opener pattern", "duty opener",
    "banned phrase", "banned jargon",
    "action verb start",
    "verb diversity",
    "buzzword saturation",
    "resume length",
    "em-dash inside",
    "demonstrating closer",
)


def enrich_annotations_with_plain_messages(review_summary: dict,
                                            resume_content=None) -> dict:
    """Walk ``review_summary['annotations']`` and add a
    ``plain_message`` field to each item (and to each annotation) so
    the chip template can show user-facing copy instead of the
    presenter's rule-name labels.

    Also de-dupes items inside each annotation by (kind, plain_message)
    so identical findings collapse to a single line with a count
    suffix.

    ``resume_content`` is the resume's content dict. When provided, a
    content-substring guard SUPPRESSES supervisor findings whose
    ``detail`` is the resume's own prose echoed back as advice. When
    omitted (e.g. unit tests of the dedup logic), the echo guard
    becomes a no-op.

    Returns a NEW review_summary dict (does not mutate the input).
    An annotation whose every item is suppressed is dropped from the
    output entirely — no empty cards rendered.
    """
    if not isinstance(review_summary, dict):
        return review_summary
    annotations = review_summary.get("annotations") or []
    # Build the corpus ONCE per call — cheap (just text concatenation)
    # but no reason to redo it per item.
    corpus = _collect_resume_content_corpus(resume_content)
    new_annotations = []
    for ann in annotations:
        items = list(ann.get("items") or [])
        seen: dict = {}
        deduped: list = []
        for item in items:
            kind = (item.get("kind") or ann.get("kind") or "").strip()
            detail = (item.get("detail") or "").strip()
            # Supervisor finding routing:
            #   - empty detail → generic stub (nothing to render)
            #   - detail echoes the resume's own content → SUPPRESS
            #     (drop the item; no chip line). Length plays NO role
            #     — a genuine long critique survives, a short echo of
            #     a bullet does not.
            #   - otherwise → render verbatim regardless of length.
            if kind == "supervisor":
                if not detail:
                    plain = _SUPERVISOR_ECHO_FALLBACK
                elif _is_echo_of_content(detail, corpus):
                    # Drop the echoed item entirely.
                    continue
                else:
                    plain = detail
            else:
                plain = _plain_message(kind, detail)
            scrub_lower = plain.lower()
            for bad in _RULE_NAME_PHRASES_TO_SCRUB:
                if bad in scrub_lower:
                    plain = "This needs a look — the reviewer flagged it."
                    break
            key = (kind, plain)
            if key in seen:
                seen[key]["dup_count"] = seen[key].get("dup_count", 1) + 1
                continue
            # Stable id matches build_buckets_for_ui's _stable_finding_id
            # so the chip's Fix-it button targets the same finding the
            # propose-fix endpoint resolves.
            finding_id = _stable_finding_id(ann.get("bucket") or "", {
                "section":    ann.get("section"),
                "item_idx":   ann.get("item_idx"),
                "bullet_idx": ann.get("bullet_idx") if ann.get("bullet_idx") is not None else item.get("bullet_idx"),
                "kind":       kind,
            })
            new_item = dict(item)
            new_item["plain_message"] = plain
            new_item["dup_count"] = 1
            new_item["finding_id"] = finding_id
            seen[key] = new_item
            deduped.append(new_item)
        # An annotation whose every item was an echo (or arrived empty)
        # has nothing to render — don't surface an empty chip.
        if not deduped:
            continue
        new_ann = dict(ann)
        new_ann["items"] = deduped
        # Total of de-duplicated count for the chip badge — sum of
        # dup_counts so a (3×) line still contributes 3 to the badge.
        new_ann["count"] = sum(it.get("dup_count", 1) for it in deduped)
        new_annotations.append(new_ann)
    out = dict(review_summary)
    out["annotations"] = new_annotations
    return out


def _plain_message(rule_or_kind: str, detail: str = "") -> str:
    """Resolve a finding to user-facing copy. Falls back to a generic
    "needs review" line — never leaks rule_id."""
    key = (rule_or_kind or "").strip()
    # Try exact then a longest-prefix match (rule_ids sometimes carry
    # disambiguating suffixes the validator added).
    if key in _PLAIN_MESSAGES:
        return _PLAIN_MESSAGES[key]
    for prefix in sorted(_PLAIN_MESSAGES, key=len, reverse=True):
        if key.startswith(prefix):
            return _PLAIN_MESSAGES[prefix]
    # Final fallback — honest plain copy, no rule_id leak.
    if (detail or "").strip():
        return "This needs a look — the reviewer flagged it."
    return "This needs a look — the reviewer flagged it."


def _stable_finding_id(bucket: str, raw: dict) -> str:
    """Deterministic finding id — survives a page reload because it's
    a hash of (bucket, section, item_idx, bullet_idx, rule_id). Two
    findings with the same payload collapse to the same id (acceptable
    — they'd be duplicates anyway)."""
    parts = (
        bucket or "",
        str(raw.get("section") or ""),
        str(raw.get("item_idx") or ""),
        str(raw.get("bullet_idx") or ""),
        str(raw.get("kind") or raw.get("rule_id") or ""),
    )
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def build_buckets_for_ui(resume) -> dict:
    """Return a UI-ready 3-bucket view of the resume's findings.

    Shape::

        {
          "auto_fix":   [Finding, ...],   # actionable via Fix It
          "user_input": [Finding, ...],   # user must confirm/add
          "advisory":   [Finding, ...],   # optional polish
          "counts":     {auto_fix: n, user_input: n, advisory: n},
        }

    Each ``Finding`` is::

        {
          "id":       "<stable_hash>",
          "bucket":   "auto_fix" | "user_input" | "advisory",
          "label":    "Can auto-fix" | "Needs your input" | "Suggestion",
          "color":    "amber" | "red" | "gray",
          "action":   "Fix it" | "Add/Confirm" | "Dismiss",
          "message":  "<user-facing instruction, no rule_id>",
          "fixable":  True | False,
          "location": {section, item_idx, bullet_idx, anchor_kind},
        }
    """
    from resumes.services.findings_presenter import build_review_summary

    review = build_review_summary(
        getattr(resume, "content", None) or {},
        getattr(resume, "validation_report", None) or {},
    )
    annotations = (review or {}).get("annotations") or []

    out = {
        BUCKET_AUTO_FIX:   [],
        BUCKET_USER_INPUT: [],
        BUCKET_ADVISORY:   [],
    }

    for ann in annotations:
        bucket = ann.get("bucket")
        if bucket not in out:
            # Honest fail-safe: anything unrecognised goes to USER_INPUT
            # (the regen loop won't touch it, the user will see it).
            bucket = BUCKET_USER_INPUT
        meta = BUCKET_META[bucket]
        # Surface per-item examples from the annotation (each item dict
        # may carry label / detail / token / rule_id).
        items = ann.get("items") or [{}]
        for item in items:
            # ``.strip()`` the kind so the id hash matches the one
            # computed at chip-render time in
            # ``enrich_annotations_with_plain_messages`` — any
            # whitespace drift between the two paths would surface as
            # "Cannot resolve finding id" on click-through.
            rule_or_kind = (item.get("kind") or ann.get("kind") or "").strip()
            detail = (item.get("detail") or "")
            raw_for_id = {
                "section":    ann.get("section"),
                "item_idx":   ann.get("item_idx"),
                "bullet_idx": ann.get("bullet_idx") if ann.get("bullet_idx") is not None else item.get("bullet_idx"),
                "kind":       rule_or_kind,
            }
            finding = {
                "id":       _stable_finding_id(bucket, raw_for_id),
                "bucket":   bucket,
                "label":    meta["label"],
                "color":    meta["color"],
                "action":   meta["action"],
                "fixable":  meta["fixable"],
                "message":  _plain_message(rule_or_kind, detail),
                "location": {
                    "section":     ann.get("section") or "",
                    "item_idx":    ann.get("item_idx"),
                    "bullet_idx":  ann.get("bullet_idx") if ann.get("bullet_idx") is not None else item.get("bullet_idx"),
                    "anchor_kind": ann.get("anchor_kind") or "resume",
                },
            }
            out[bucket].append(finding)

    return {
        "auto_fix":   out[BUCKET_AUTO_FIX],
        "user_input": out[BUCKET_USER_INPUT],
        "advisory":   out[BUCKET_ADVISORY],
        "counts": {
            "auto_fix":   len(out[BUCKET_AUTO_FIX]),
            "user_input": len(out[BUCKET_USER_INPUT]),
            "advisory":   len(out[BUCKET_ADVISORY]),
        },
    }


def find_finding_by_id(buckets: dict, finding_id: str) -> Optional[dict]:
    """Return the finding with the given stable id, or None."""
    for bucket_key in ("auto_fix", "user_input", "advisory"):
        for f in (buckets.get(bucket_key) or []):
            if f.get("id") == finding_id:
                return f
    return None


def message_is_jargon_free(message: str) -> bool:
    """Defensive check for rule_id / internal-vocab leak. Used by tests
    to assert user-facing copy stays plain."""
    if not isinstance(message, str):
        return True
    leakage_markers = (
        # Internal rule_id codes.
        "A1_", "A2_", "A3_", "A4_", "A5_", "A6_", "A7_",
        "B1_", "B2_", "B3_", "C1_", "C2_",
        "rule_id", "rule_id=",
        # Internal bucket constants.
        "BUCKET_", "auto_fix", "user_input", "advisory",
        # Rule NAMES the presenter used to leak as labels — these are
        # the rule's internal English name, not user-facing instructions
        # (BUG 3 from the click-through audit).
        "inside-out", "inside out",
        "structural variation",
        "quantification",
        "opener pattern", "duty opener",
        "banned phrase", "banned jargon",
        "action verb start",
        "verb diversity",
        "buzzword saturation",
        "em-dash inside",
        "demonstrating closer",
    )
    lower = message.lower()
    for m in leakage_markers:
        if m.lower() in lower:
            return False
    return True
