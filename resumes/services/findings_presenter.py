"""Translate raw validation_report + supervisor_review into a small
user-facing summary the edit page renders. Read-only; never mutates the
resume.

The validators / supervisor write structured findings that a human
shouldn't see verbatim: internal field names ('drop_skill_leak'),
high-volume noise (20 'unsupported_skill' entries on an acceptable
resume), and overlapping content across two storage keys. This
presenter is the single place that:

  1. Reads validation_report (with keys passed/findings/stats/grounding_findings/
     supervisor_findings) and content['supervisor_review'] (with
     keys verdict/summary/rounds/findings).
  2. Classifies each finding into one of three user-facing tiers per
     the policy in BUILD_REVIEW_SUMMARY_POLICY below.
  3. Collapses high-volume same-kind findings into a count + examples
     ("5 skills not backed by your profile: A, B, C and 2 more")
     instead of a wall.
  4. Returns a flat dict the template renders without any business logic.

Out of scope: changing how findings are generated, blocking shipping,
or auto-fixing anything. Surfacing only.
"""
from __future__ import annotations

import re
from typing import Any

from resumes.services.findings_classifier import (
    BUCKET_ADVISORY,
    BUCKET_AUTO_FIX,
    BUCKET_USER_INPUT,
    classify_bullet_rule,
    classify_grounding,
    classify_regression,
    classify_supervisor,
)


# Caps so a pathological run can't bloat the rendered banner.
_MAX_BLOCKING_ITEMS = 5
_MAX_ADVISORY_ITEMS = 5
# When collapsing a list of same-kind findings into "X and N more",
# show this many concrete examples before the ellipsis.
_COLLAPSE_EXAMPLE_COUNT = 3


# ----------------------------------------------------------------------
# Severity policy — single source of truth
# ----------------------------------------------------------------------
# BLOCKING (red banner — action needed before send):
#   - supervisor finding with severity='blocking' AND layer='content'
#     (the regen path could fix these; they're the factual content
#     issues the supervisor flagged)
#   - supervisor verdict == 'revise' WHEN no specific blocking finding
#     fires (generic "flagged for revision" with the supervisor's own
#     summary text)
#   - grounding finding with kind in {'unsupported_metric',
#     'drop_skill_leak'} — a numeric claim with no profile evidence,
#     or a skill the plan marked do-not-claim that the LLM emitted
#     anyway. Both are factual hallucinations, not stylistic notes.
#   - bullet validator finding with severity='error' (banned phrase,
#     missing action-verb opener, etc. — fixable in the editor)
#
# ADVISORY (yellow banner — concise count + a few examples):
#   - grounding finding with kind='unsupported_skill' (high-volume
#     and frequently noisy; always collapsed)
#   - supervisor finding with severity='warning' (collapsed by category)
#   - supervisor finding with severity='blocking' AND layer='render'
#     (layout issue — actionable in the editor but not a factual problem)
#   - bullet validator finding with severity='warn' (collapsed)
#
# CLEAN (small green pill, no banner):
#   - nothing above fired, and supervisor_review.verdict == 'advance'
#     (or no supervisor ran at all and no other findings present)
BUILD_REVIEW_SUMMARY_POLICY = """see module docstring"""


# Plain-language labels for internal kind names. Keep brief — they
# appear inline in the banner.
_GROUNDING_KIND_LABEL = {
    'unsupported_metric': 'numeric claim not backed by your profile evidence',
    'drop_skill_leak': 'skill the plan said not to claim',
    'unsupported_skill': 'skill not matched to evidence in your profile',
}

def _bullet_rule_prefix(rule_id: str) -> str:
    """Bullet validator emits rule_ids with suffixes like 'A1_banned_phrase'
    or 'B1_quantification'; the canonical lookup uses just the A1/B1
    prefix. Strip the suffix before looking up the label so the rendered
    banner shows plain-language labels instead of falling back to
    'bullet rule violation' for every finding."""
    if not rule_id:
        return ''
    return str(rule_id).strip().upper().split('_', 1)[0]


# Bullet validator rule_id → user-readable group. The validator emits
# 12+ rule_ids (A1-A7, B1-B3, C1-C2) with suffixes; _bullet_rule_prefix
# canonicalises before lookup.
_BULLET_RULE_GROUP = {
    # Tier A — per-bullet wording.
    'A1': 'banned phrase / jargon',
    'A2': 'bullet missing a strong action verb',
    'A3': 'bullet starts with a "responsibility" opener',
    'A4': 'inside-out summary opener',
    'A5': 'bullet length out of range',
    'A6': 'em-dash inside a bullet',
    'A7': 'closer like "demonstrating ..."',
    # Tier B — per-role.
    'B1': 'role missing quantification',
    'B2': 'role re-uses the same action verb',
    'B3': 'role lacks structural variation',
    # Tier C — resume-level.
    'C1': 'resume length not aligned with seniority',
    'C2': 'buzzword saturation',
}


def _coerce_list(value) -> list:
    return list(value) if isinstance(value, list) else []


def _collapse_examples(names: list[str], max_inline: int = _COLLAPSE_EXAMPLE_COUNT) -> str:
    """Format a list of names as 'A, B, C and N more' / 'A and B' / 'A'."""
    clean = [str(n).strip() for n in names if str(n).strip()]
    if not clean:
        return ''
    if len(clean) <= max_inline:
        if len(clean) == 1:
            return clean[0]
        if len(clean) == 2:
            return f"{clean[0]} and {clean[1]}"
        return ', '.join(clean[:-1]) + f", and {clean[-1]}"
    head = ', '.join(clean[:max_inline])
    extra = len(clean) - max_inline
    return f"{head} and {extra} more"


def _supervisor_findings(validation_report: dict, supervisor_review: dict) -> list[dict]:
    """Dedupe supervisor findings between the two storage keys.

    The writer at resume_generator.py:1019-1024 puts the SAME list under
    validation_report['supervisor_findings'] AND content['supervisor_review']
    ['findings'], so picking one source is sufficient. Prefer
    validation_report (it's the more authoritative top-level slot).
    """
    fs = _coerce_list(validation_report.get('supervisor_findings'))
    if fs:
        return fs
    return _coerce_list((supervisor_review or {}).get('findings'))


def _bullet_findings(validation_report: dict) -> list[dict]:
    """Read the bullet validator's findings. Filter out non-dict and
    rule_ids we don't have a label for (defensive against schema drift)."""
    return [
        f for f in _coerce_list(validation_report.get('findings'))
        if isinstance(f, dict)
    ]


def _grounding_findings(validation_report: dict) -> list[dict]:
    return [
        f for f in _coerce_list(validation_report.get('grounding_findings'))
        if isinstance(f, dict)
    ]


def _blocking_supervisor(findings: list[dict]) -> list[dict]:
    """Supervisor findings the user must act on: severity='blocking' AND
    layer='content'. Render-layer blockers (layout / spacing) are
    advisory in this view — the editor can fix them but they don't
    indicate factual problems."""
    return [
        f for f in findings
        if (f.get('severity') or '').lower() == 'blocking'
        and (f.get('layer') or 'content').lower() == 'content'
    ]


def _advisory_supervisor(findings: list[dict]) -> list[dict]:
    """Supervisor findings that aren't user-blocking: warnings, plus
    render-layer blockers (layout)."""
    out = []
    for f in findings:
        sev = (f.get('severity') or '').lower()
        layer = (f.get('layer') or 'content').lower()
        if sev == 'warning':
            out.append(f)
        elif sev == 'blocking' and layer != 'content':
            out.append(f)
    return out


def _by_category(findings: list[dict]) -> dict[str, list[dict]]:
    """Bucket findings by their `category` field; '' becomes 'general'."""
    out: dict[str, list[dict]] = {}
    for f in findings:
        key = (f.get('category') or '').strip().lower() or 'general'
        out.setdefault(key, []).append(f)
    return out


def _grounding_buckets(findings: list[dict]) -> dict[str, list[dict]]:
    """Bucket grounding findings by `kind`."""
    out: dict[str, list[dict]] = {}
    for f in findings:
        kind = (f.get('kind') or '').strip().lower()
        if kind:
            out.setdefault(kind, []).append(f)
    return out


def _regression_findings(validation_report: dict) -> list[dict]:
    """Fix #1 — regression-check findings against the user's last
    exported version. Same shape conventions:
      kind ∈ {'metric_loss', 'bullet_count_drop', 'skill_loss'}
      severity: 'blocking' (deterministic loss; supervised loop must
                restore) | 'warning' (advisory / cap-demoted)."""
    return [
        f for f in _coerce_list(validation_report.get('regression_findings'))
        if isinstance(f, dict)
    ]


_REGRESSION_KIND_LABEL = {
    'metric_loss': 'numeric metric from your last export',
    'bullet_count_drop': 'bullet count below your last export',
    'skill_loss': 'skill from your last export not on this draft',
}


def _example_skill_from_grounding(detail: str) -> str:
    """The grounding detail strings look like
        "Possible unsupported skill 'PyTorch' — not in the inclusion ..."
    Extract the quoted skill name for the collapsed display. Falls back
    to the empty string when the shape doesn't match."""
    if not isinstance(detail, str):
        return ''
    # Single-quoted token after the first "'" is the skill name.
    a = detail.find("'")
    if a < 0:
        return ''
    b = detail.find("'", a + 1)
    if b <= a:
        return ''
    return detail[a + 1:b].strip()


def _example_metric_from_grounding(detail: str) -> str:
    """Same pattern for unsupported_metric details:
        "Metric '92%' doesn't trace ..."."""
    return _example_skill_from_grounding(detail)


# Known section keys the edit template can scroll to. Any annotation
# whose `section` isn't in this set degrades to a resume-level anchor
# (no section to point at).
_SECTION_IDS = frozenset({
    'summary', 'skills', 'experience', 'education', 'projects',
    'certifications', 'languages', 'awards',
})

# Supervisor finding categories → edit-template section keys. The
# supervisor's `category` field is free text (the LLM picks from a
# documented vocab in SUPERVISOR_PROMPT section A-F but not enforced);
# this map covers the documented vocab + obvious synonyms. Anything
# not mapped degrades to resume-level — honest fallback per the brief.
_SUPERVISOR_CATEGORY_TO_SECTION = {
    'summary': 'summary',
    'professional_summary': 'summary',
    'objective': 'summary',
    'skills': 'skills',
    'experience': 'experience',
    'work_experience': 'experience',
    'projects': 'projects',
    'project': 'projects',
    'certs': 'certifications',
    'certifications': 'certifications',
    'certification': 'certifications',
    'education': 'education',
    'languages': 'languages',
    'awards': 'awards',
    # Resume-level / no specific section.
    'ats': None,
    'layout': None,
    'ordering': None,
    'grounding': None,
    'relevance': None,
    'redundancy': None,
}


# Path patterns the deterministic finding sources emit:
#   "experience[0].description[2]"   (bullet validator A1-A7, grounding)
#   "experience[0].description"      (bullet validator B1-B3)
#   "professional_summary"           (bullet validator A4)
#   "skills"                         (regression skill_loss)
#   "experience[AI Trainee @ DEPI]"  (regression metric_loss — name not idx)
#   "projects[SmartCV]"              (regression projects — name not idx)
_PATH_NUMERIC_RE = re.compile(
    r"^(?P<section>[a-z_]+)(?:\[(?P<item>\d+)\])?(?:\.(?P<sub>[a-z_]+))?(?:\[(?P<bullet>\d+)\])?"
)
_PATH_NAMED_RE = re.compile(
    r"^(?P<section>experience|projects)\[(?P<name>[^\]]+)\]$"
)


def _parse_location_path(path: str) -> dict:
    """Parse the deterministic-source path strings into anchor fields.

    Returns ``{section, item_idx, bullet_idx, anchor_kind}`` where:
      anchor_kind ∈ {'bullet','item','section','resume'}, picking the
      finest level the path actually carries.

    Honest fallback: an unparseable path → resume-level anchor (the
    finding still surfaces in the top summary; just not at an inline
    target it can't actually locate)."""
    if not isinstance(path, str) or not path.strip():
        return {'section': '', 'item_idx': None, 'bullet_idx': None,
                'anchor_kind': 'resume'}
    m = _PATH_NUMERIC_RE.match(path.strip())
    if not m:
        return {'section': '', 'item_idx': None, 'bullet_idx': None,
                'anchor_kind': 'resume'}
    section = (m.group('section') or '').lower()
    # 'professional_summary' is the bullet-validator's section name for
    # the summary; normalise to 'summary' (the section ID used in the form).
    if section == 'professional_summary':
        section = 'summary'
    item_str = m.group('item')
    bullet_str = m.group('bullet')
    item_idx = int(item_str) if item_str is not None else None
    bullet_idx = int(bullet_str) if bullet_str is not None else None
    if section not in _SECTION_IDS:
        return {'section': '', 'item_idx': None, 'bullet_idx': None,
                'anchor_kind': 'resume'}
    if bullet_idx is not None:
        kind = 'bullet'
    elif item_idx is not None:
        kind = 'item'
    else:
        kind = 'section'
    return {'section': section, 'item_idx': item_idx,
            'bullet_idx': bullet_idx, 'anchor_kind': kind}


def _resolve_regression_where(where: str, content: dict | None) -> tuple[str, int | None]:
    """Regression findings carry item identity by NAME, not index:
    ``experience[AI Trainee @ DEPI]`` or ``projects[SmartCV]``. To anchor
    them at an item in the rendered form we walk content's
    experience/projects list looking for a match. Returns
    ``(section, item_idx)``; ``item_idx`` is ``None`` when the named
    item isn't in the new content (e.g. the role was renamed in this
    regen — section-level fallback per the brief, no faked anchor)."""
    if not isinstance(where, str) or not isinstance(content, dict):
        return '', None
    m = _PATH_NAMED_RE.match(where.strip())
    if not m:
        return '', None
    section = m.group('section')
    name = (m.group('name') or '').strip()
    items = content.get(section) or []
    if not isinstance(items, list):
        return section, None
    name_lower = name.lower()
    if section == 'experience':
        # name format: "<title> @ <company>"
        for i, e in enumerate(items):
            if not isinstance(e, dict):
                continue
            t = (e.get('title') or '').strip()
            c = (e.get('company') or '').strip()
            if f"{t} @ {c}".lower() == name_lower:
                return section, i
    else:  # projects
        for i, p in enumerate(items):
            if not isinstance(p, dict):
                continue
            n = (p.get('name') or '').strip().lower()
            if n == name_lower:
                return section, i
    return section, None


# Short user-facing labels for the inline chip. These complement the
# longer banner text the existing summary buckets use.
_INLINE_LABEL = {
    # bullet_validator suffixed rule_ids share their prefix's label via
    # _bullet_rule_prefix + _BULLET_RULE_GROUP — handled inline below.
    'unsupported_skill': 'unverified skill',
    'unsupported_metric': 'unverified number',
    'drop_skill_leak': 'do-not-claim skill mentioned',
    'metric_loss': 'metric lost from your last export',
    'bullet_count_drop': 'fewer bullets than your last export',
    'skill_loss': 'skill from last export missing',
}


def _build_annotations(content: dict, vr: dict) -> list[dict]:
    """One annotation per (section, item_idx, bullet_idx, tier) group.
    Multiple findings sharing the same anchor + tier collapse into one
    chip showing a count; the popover lists them. This is the inline-
    rendering counterpart to the summarised top-banner buckets."""
    raw: list[dict] = []

    # 1. Bullet validator findings — error → blocking, warn → advisory.
    for f in (vr.get('findings') or []):
        if not isinstance(f, dict):
            continue
        sev = (f.get('severity') or '').lower()
        if sev not in ('error', 'warn'):
            continue
        tier = 'blocking' if sev == 'error' else 'advisory'
        rid_prefix = _bullet_rule_prefix(f.get('rule_id') or '')
        label = _BULLET_RULE_GROUP.get(rid_prefix, 'bullet rule violation')
        parsed = _parse_location_path(f.get('location') or '')
        raw.append({
            'tier': tier, 'label': label,
            'detail': (f.get('issue') or '').strip(),
            'kind': str(f.get('rule_id') or 'bullet'),
            **parsed,
            'token': None,
            'bucket': classify_bullet_rule(f.get('rule_id') or ''),
        })

    # 2. Grounding findings — token search is honest (bullet-precise where
    # the path supports it, with the offending token surfaced in the
    # popover; the textarea limitation means we can't intra-text
    # highlight, so the token lives in the detail text).
    for f in (vr.get('grounding_findings') or []):
        if not isinstance(f, dict):
            continue
        kind = (f.get('kind') or '').lower()
        tier = 'advisory' if kind == 'unsupported_skill' else 'blocking'
        label = _INLINE_LABEL.get(kind, kind.replace('_', ' '))
        parsed = _parse_location_path(f.get('where') or '')
        token = _example_skill_from_grounding(f.get('detail') or '')
        raw.append({
            'tier': tier, 'label': label,
            'detail': (f.get('detail') or '').strip(),
            'kind': kind, **parsed, 'token': token or None,
            'bucket': classify_grounding(kind),
        })

    # 3. Supervisor findings — category-level only, no item idx (the
    # location field is LLM free-text; not safe to parse).
    for f in (vr.get('supervisor_findings') or []):
        if not isinstance(f, dict):
            continue
        sev = (f.get('severity') or '').lower()
        layer = (f.get('layer') or 'content').lower()
        if sev == 'blocking' and layer == 'content':
            tier = 'blocking'
        elif sev == 'warning':
            tier = 'advisory'
        elif sev == 'blocking' and layer != 'content':
            tier = 'advisory'   # render-layer blockers — editor-fixable
        else:
            continue
        category = (f.get('category') or '').lower().strip()
        section = _SUPERVISOR_CATEGORY_TO_SECTION.get(category, '')
        anchor_kind = 'section' if section in _SECTION_IDS else 'resume'
        cat_title = (category.replace('_', ' ').title() if category else 'Reviewer note')
        raw.append({
            'tier': tier,
            'label': f"{cat_title} note from the AI reviewer",
            'detail': (f.get('issue') or '').strip(),
            'kind': 'supervisor',
            'section': section if section in _SECTION_IDS else '',
            'item_idx': None, 'bullet_idx': None,
            'anchor_kind': anchor_kind,
            'token': None,
            'bucket': classify_supervisor(
                category, f.get('severity') or '', f.get('layer') or '',
            ),
        })

    # 4. Regression findings — item-level when the named item is still in
    # content, otherwise section-level. NEVER fake a bullet_idx; regression
    # findings legitimately don't carry one (role-level diff).
    for f in (vr.get('regression_findings') or []):
        if not isinstance(f, dict):
            continue
        sev = (f.get('severity') or '').lower()
        tier = 'blocking' if sev == 'blocking' else 'advisory'
        kind = (f.get('kind') or '').lower()
        label = _INLINE_LABEL.get(kind, kind.replace('_', ' '))
        where = f.get('where') or ''
        if kind == 'skill_loss':
            section, item_idx = 'skills', None
            anchor_kind = 'section'
        else:
            section, item_idx = _resolve_regression_where(where, content or {})
            anchor_kind = 'item' if item_idx is not None else ('section' if section else 'resume')
        raw.append({
            'tier': tier, 'label': label,
            'detail': (f.get('detail') or '').strip(),
            'kind': kind, 'section': section,
            'item_idx': item_idx, 'bullet_idx': None,
            'anchor_kind': anchor_kind, 'token': None,
            'bucket': classify_regression(kind),
        })

    # Group by (section, item_idx, bullet_idx, tier, anchor_kind, bucket).
    # Bucket is part of the key so an AUTO_FIX blocker and a USER_INPUT
    # blocker at the same anchor stay as separate chips with separate
    # treatments — red "to fix" vs slate "Confirm or complete" — never
    # mixed under one ambiguous label.
    grouped: dict[tuple, list[dict]] = {}
    for r in raw:
        key = (
            r['section'], r['item_idx'], r['bullet_idx'],
            r['tier'], r['anchor_kind'], r.get('bucket') or BUCKET_USER_INPUT,
        )
        grouped.setdefault(key, []).append(r)

    annotations: list[dict] = []
    for i, (key, items) in enumerate(grouped.items()):
        section, item_idx, bullet_idx, tier, anchor_kind, bucket = key
        section_id = f"section-{section}" if section in _SECTION_IDS else ''
        # The DOM anchor the template attaches the chip to. Item / bullet
        # variants get distinct IDs so multiple items in the same
        # section each get their own chip.
        if anchor_kind == 'bullet' and section and item_idx is not None:
            anchor_target = f"anchor-{section}-item-{item_idx}-bullets"
        elif anchor_kind == 'item' and section and item_idx is not None:
            anchor_target = f"anchor-{section}-item-{item_idx}"
        elif anchor_kind == 'section' and section_id:
            anchor_target = section_id
        else:
            anchor_target = ''  # resume-level — no inline anchor
        annotations.append({
            'id': f'ann-{i + 1}',
            'tier': tier,
            'bucket': bucket,
            'anchor_kind': anchor_kind,
            'section': section,
            'section_id': section_id,
            'item_idx': item_idx,
            'bullet_idx': bullet_idx,
            'anchor_target': anchor_target,
            'count': len(items),
            'items': [
                {'label': it['label'], 'detail': it['detail'],
                 'kind': it['kind'], 'token': it['token']}
                for it in items
            ],
        })
    return annotations


def build_review_summary(content: dict | None, validation_report: dict | None) -> dict:
    """Compute the small user-facing review summary for a resume.

    Returns a flat dict suitable for direct template rendering:

        {
          "tier": "blocking" | "advisory" | "clean",
          "headline": str,         # short top-line headline
          "blocking_items": [{title, body}],
          "advisory_items": [{title, body}],
          "info_items": [{title, body}],     # only populated for "clean"
        }

    Empty / missing input is treated as clean.
    """
    content = content or {}
    vr = validation_report or {}
    supervisor_review = content.get('supervisor_review') or {}

    sup_findings = _supervisor_findings(vr, supervisor_review)
    bullet_findings = _bullet_findings(vr)
    ground_findings = _grounding_findings(vr)
    regression_findings_all = _regression_findings(vr)

    # ---------- Tier A: BLOCKING (red) ----------
    blocking_items: list[dict] = []

    blocking_sup = _blocking_supervisor(sup_findings)
    if blocking_sup:
        # Group by category so 3 summary blockers don't render as 3 lines.
        for cat, items in _by_category(blocking_sup).items():
            count = len(items)
            cat_label = cat.replace('_', ' ').title()
            example = (items[0].get('issue') or '').strip()
            example_short = (example[:140] + '…') if len(example) > 140 else example
            if count == 1:
                title = f"{cat_label}: 1 issue flagged by the AI reviewer"
                body = example_short
            else:
                title = f"{cat_label}: {count} issues flagged by the AI reviewer"
                body = (example_short + f"  (and {count - 1} more)") if example_short else ''
            blocking_items.append({'title': title, 'body': body})

    # If supervisor said revise but we didn't surface a specific blocker
    # from its findings, surface its summary text so the user sees that
    # something flagged it.
    verdict = (supervisor_review.get('verdict') or '').lower()
    if not blocking_sup and verdict == 'revise':
        sup_summary = (supervisor_review.get('summary') or '').strip()
        blocking_items.append({
            'title': 'The AI reviewer flagged this draft for revision.',
            'body': sup_summary or "It didn't pass the supervisor's quality bar — review the resume before sending.",
        })

    # Grounding hallucinations — unsupported metrics and dropped-skill leaks.
    g_buckets = _grounding_buckets(ground_findings)
    if g_buckets.get('unsupported_metric'):
        items = g_buckets['unsupported_metric']
        metrics = [_example_metric_from_grounding(f.get('detail', '')) for f in items]
        metrics = [m for m in metrics if m]
        count = len(items)
        title = (
            f"{count} numeric claim could not be verified against your profile."
            if count == 1
            else f"{count} numeric claims could not be verified against your profile."
        )
        body = (
            f"We couldn't trace: {_collapse_examples(metrics)}. "
            "Confirm the number is real or remove it — recruiters check numbers."
            if metrics else
            "We couldn't trace some numbers in your bullets back to your profile evidence."
        )
        blocking_items.append({'title': title, 'body': body})

    if g_buckets.get('drop_skill_leak'):
        items = g_buckets['drop_skill_leak']
        # The skill name lives in the detail's first single-quoted token.
        leaked = [_example_skill_from_grounding(f.get('detail', '')) for f in items]
        leaked = [s for s in leaked if s]
        count = len(items)
        title = (
            f"1 bullet mentions a skill the plan asked you not to claim."
            if count == 1
            else f"{count} bullets mention skills the plan asked you not to claim."
        )
        if leaked:
            body = f"Remove or rephrase mentions of: {_collapse_examples(leaked)}."
        else:
            body = "Remove or rephrase those mentions before sending."
        blocking_items.append({'title': title, 'body': body})

    # Bullet validator errors.
    bullet_errors = [f for f in bullet_findings if (f.get('severity') or '').lower() == 'error']
    if bullet_errors:
        # Bucket by RESOLVED LABEL so two findings with different rule_id
        # suffixes (e.g. A1_banned_phrase and A1_banned_jargon both map to
        # 'banned phrase / jargon') render as one line, not two. Also
        # protects against future rule_ids that fall back to the generic
        # label all collapsing into a single 'bullet rule violation' line
        # instead of N copies of the same.
        label_counts: dict[str, int] = {}
        for f in bullet_errors:
            prefix = _bullet_rule_prefix(f.get('rule_id') or '')
            label = _BULLET_RULE_GROUP.get(prefix, 'bullet rule violation')
            label_counts[label] = label_counts.get(label, 0) + 1
        for label, n in label_counts.items():
            if n == 1:
                blocking_items.append({
                    'title': f"1 bullet flagged: {label}.",
                    'body': "Fix it in the relevant section below.",
                })
            else:
                blocking_items.append({
                    'title': f"{n} bullets flagged: {label}.",
                    'body': "Review the relevant sections below.",
                })

    # Fix #1 — regression findings against the user's last exported version.
    # metric_loss / bullet_count_drop carry severity='blocking' when the
    # supervised loop couldn't restore them within the round budget AND
    # WERE NOT demoted (i.e. supervisor is off OR cap not exhausted). On
    # cap exhaustion, the loop pre-demotes them to 'warning' so they
    # land in the advisory bucket below — same data, different banner.
    regression_blocking = [
        f for f in regression_findings_all
        if (f.get('severity') or '').lower() == 'blocking'
    ]
    if regression_blocking:
        # Group by (kind, where) so 3 metric losses on the same role
        # render as one line, not three.
        by_loc: dict[tuple[str, str], list[dict]] = {}
        for f in regression_blocking:
            key = ((f.get('kind') or '').lower(),
                   (f.get('where') or '').strip())
            by_loc.setdefault(key, []).append(f)
        for (kind, where), items in by_loc.items():
            label = _REGRESSION_KIND_LABEL.get(kind, 'content from your last export')
            n = len(items)
            if n == 1:
                blocking_items.append({
                    'title': f"Regression: {label}.",
                    'body': f"{(items[0].get('detail') or '').strip()}",
                })
            else:
                blocking_items.append({
                    'title': f"Regression: {n} {label}s.",
                    'body': f"{(items[0].get('detail') or '').strip()}  (and {n - 1} more)",
                })

    # ---------- Tier B: ADVISORY (yellow) ----------
    advisory_items: list[dict] = []

    # Supervisor warnings + render-layer blockers — bucket by category.
    advisory_sup = _advisory_supervisor(sup_findings)
    if advisory_sup:
        for cat, items in _by_category(advisory_sup).items():
            count = len(items)
            cat_label = cat.replace('_', ' ').title()
            example = (items[0].get('issue') or '').strip()
            example_short = (example[:140] + '…') if len(example) > 140 else example
            if count == 1:
                advisory_items.append({
                    'title': f"{cat_label}: 1 suggestion from the AI reviewer",
                    'body': example_short,
                })
            else:
                advisory_items.append({
                    'title': f"{cat_label}: {count} suggestions from the AI reviewer",
                    'body': (example_short + f"  (and {count - 1} more)") if example_short else '',
                })

    # Unsupported skills — always advisory, always collapsed. This is the
    # high-volume one (the round-1 trace showed 20 of these on a fine
    # resume); we surface "<N> skills not backed by your profile: A, B, C
    # and N-3 more" — never a 20-item list.
    if g_buckets.get('unsupported_skill'):
        items = g_buckets['unsupported_skill']
        skills = [_example_skill_from_grounding(f.get('detail', '')) for f in items]
        skills = [s for s in skills if s]
        # Dedupe so "PyTorch" mentioned in 5 bullets doesn't fill the list.
        seen: set[str] = set()
        unique_skills = []
        for s in skills:
            key = s.lower()
            if key not in seen:
                seen.add(key)
                unique_skills.append(s)
        n = len(items)
        title = (
            f"1 skill mentioned in a bullet wasn't matched to evidence in your profile."
            if n == 1
            else f"{n} skill mentions weren't matched to evidence in your profile."
        )
        if unique_skills:
            body = (
                f"Couldn't ground: {_collapse_examples(unique_skills)}. "
                "Many are false positives (proper nouns, tools we didn't recognise). "
                "Verify the ones that matter; ignore the rest."
            )
        else:
            body = "Verify the relevant bullets; most are likely fine."
        advisory_items.append({'title': title, 'body': body})

    # Bullet validator warnings — bucket by RESOLVED LABEL (same fix as
    # the errors loop above: rule_ids carry suffixes the lookup must strip).
    bullet_warns = [f for f in bullet_findings if (f.get('severity') or '').lower() == 'warn']
    if bullet_warns:
        label_counts = {}
        for f in bullet_warns:
            prefix = _bullet_rule_prefix(f.get('rule_id') or '')
            label = _BULLET_RULE_GROUP.get(prefix, 'bullet style nudge')
            label_counts[label] = label_counts.get(label, 0) + 1
        for label, n in label_counts.items():
            if n == 1:
                advisory_items.append({
                    'title': f"1 bullet nudge: {label}.",
                    'body': "Optional — fix in the relevant section.",
                })
            else:
                advisory_items.append({
                    'title': f"{n} bullet nudges: {label}.",
                    'body': "Optional — review when you have time.",
                })

    # Fix #1 — regression findings marked 'warning'. Two paths land here:
    #   (a) skill_loss findings (always warning by design — skills shift
    #       with tailoring, so the user should know but not be blocked)
    #   (b) metric_loss / bullet_count_drop findings DEMOTED by the
    #       supervised loop on cap exhaustion ("we couldn't preserve X")
    regression_warnings = [
        f for f in regression_findings_all
        if (f.get('severity') or '').lower() == 'warning'
    ]
    if regression_warnings:
        kind_counts: dict[str, int] = {}
        kind_examples: dict[str, str] = {}
        for f in regression_warnings:
            kind = (f.get('kind') or 'unknown').lower()
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            if kind not in kind_examples:
                kind_examples[kind] = (f.get('detail') or '').strip()
        for kind, n in kind_counts.items():
            label = _REGRESSION_KIND_LABEL.get(kind, 'content from your last export')
            example = kind_examples.get(kind, '')
            if n == 1:
                advisory_items.append({
                    'title': f"1 {label} not preserved from your last export.",
                    'body': example,
                })
            else:
                advisory_items.append({
                    'title': f"{n} {label}s not preserved from your last export.",
                    'body': f"{example}  (and {n - 1} more)" if example else '',
                })

    # Cap how many we render so a pathological run can't bloat the page.
    blocking_items = blocking_items[:_MAX_BLOCKING_ITEMS]
    advisory_items = advisory_items[:_MAX_ADVISORY_ITEMS]

    # ---------- Tier selection ----------
    if blocking_items:
        tier = 'blocking'
        headline = "This résumé needs a fix before you send it."
        info_items: list[dict] = []
    elif advisory_items:
        tier = 'advisory'
        headline = "A few things worth a second look — none block sending."
        info_items = []
    else:
        tier = 'clean'
        headline = ''
        info_items = []
        if verdict == 'advance':
            info_items.append({
                'title': "AI reviewer: passed",
                'body': "The supervisor checked this draft and didn't flag anything.",
            })
        elif vr.get('passed'):
            info_items.append({
                'title': "Bullet checks: passed",
                'body': "No banned-phrase or structural issues detected.",
            })

    # Inline anchor data — independent of the summary bin collapse.
    # The template uses this to render finding chips AT their targets
    # (section / item / bullet / resume-level), preserving honest
    # fallback where the data doesn't support a finer anchor.
    annotations = _build_annotations(content, vr)
    # Per-section count of OPEN annotations (for the nav rail).
    # Per-bucket counts so the nav rail can show "1 to fix" vs
    # "1 to confirm" vs "1 to review" in distinct treatments later.
    section_counts: dict[str, dict[str, int]] = {}
    for a in annotations:
        sec = a.get('section') or ''
        if not sec:
            continue
        b = section_counts.setdefault(
            sec, {'blocking': 0, 'advisory': 0,
                  'to_fix': 0, 'to_confirm': 0, 'to_review': 0},
        )
        b[a['tier']] = b.get(a['tier'], 0) + 1
        if a.get('bucket') == BUCKET_AUTO_FIX and a['tier'] == 'blocking':
            b['to_fix'] = b.get('to_fix', 0) + 1
        elif a.get('bucket') == BUCKET_USER_INPUT:
            b['to_confirm'] = b.get('to_confirm', 0) + 1
        else:
            b['to_review'] = b.get('to_review', 0) + 1

    # Whole-resume totals split by bucket. "to_fix" counts ONLY the
    # AUTO_FIX blockers that survived the loop — ideally zero after a
    # clean run. "to_confirm" counts NEEDS_USER_INPUT items, which
    # never enter the loop. "to_review" counts advisory + cap-exhausted
    # auto-fix demotes.
    def _count(predicate) -> int:
        return sum(1 for a in annotations if predicate(a))

    total_to_fix = _count(
        lambda a: a.get('bucket') == BUCKET_AUTO_FIX and a['tier'] == 'blocking'
    )
    total_to_confirm = _count(lambda a: a.get('bucket') == BUCKET_USER_INPUT)
    total_to_review = _count(
        lambda a: a.get('bucket') == BUCKET_ADVISORY
                  or (a.get('bucket') == BUCKET_AUTO_FIX and a['tier'] == 'advisory')
    )

    return {
        'tier': tier,
        'headline': headline,
        'blocking_items': blocking_items,
        'advisory_items': advisory_items,
        'info_items': info_items,
        'annotations': annotations,
        'section_counts': section_counts,
        'total_blocking': sum(s.get('blocking', 0) for s in section_counts.values())
                          + sum(1 for a in annotations
                                if a['tier'] == 'blocking' and a['anchor_kind'] == 'resume'),
        'total_advisory': sum(s.get('advisory', 0) for s in section_counts.values())
                          + sum(1 for a in annotations
                                if a['tier'] == 'advisory' and a['anchor_kind'] == 'resume'),
        # Three-bucket pill counts — the loop-wiring & UI reframe surface.
        'total_to_fix': total_to_fix,
        'total_to_confirm': total_to_confirm,
        'total_to_review': total_to_review,
    }
