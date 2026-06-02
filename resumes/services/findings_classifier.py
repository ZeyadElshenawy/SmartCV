"""Classification policy for resume-validation findings.

Every finding maps to ONE of three buckets, decided by a single principle:

    Can the system fix this from information it ALREADY HAS?

- BUCKET_AUTO_FIX     : phrasing / structure / lexical — the LLM has
                        what it needs (banned phrase, em-dash, duty-
                        opener, verb diversity, leaked-skill deletion,
                        ATS/relevance rephrasing). Feed into the
                        fix-#3 supervised regen loop.
- BUCKET_USER_INPUT   : the fix needs a fact ONLY THE USER has — a
                        number to verify, an end date, a judgment on
                        whether a claim is real. MUST NOT enter the
                        regen loop (regenerating = fabricating). Shown
                        to the user under a "Confirm or complete"
                        treatment, NOT as an error.
- BUCKET_ADVISORY     : optional polish; never drives regen.

CRITICAL FAIL-SAFE: when the kind / rule_id / category is unknown,
ambiguous, or missing, classify as BUCKET_USER_INPUT (shown to user,
NEVER sent to the loop). Misclassifying a user-input finding as
auto-fixable makes the loop try to "fix" it — which means fabricate
or delete the user's real content. The safe direction of error is
"ask the user", never "let the loop invent".
"""

from __future__ import annotations

BUCKET_AUTO_FIX = 'auto_fix'
BUCKET_USER_INPUT = 'user_input'
BUCKET_ADVISORY = 'advisory'


_BULLET_RULE_BUCKET: dict[str, str] = {
    # Phrasing / lexical — LLM rewrites the bullet.
    'A1_banned_phrase':        BUCKET_AUTO_FIX,
    'A1_banned_jargon':        BUCKET_AUTO_FIX,
    'A2_action_verb_start':    BUCKET_AUTO_FIX,
    'A3_duty_opener':          BUCKET_AUTO_FIX,
    'A4_inside_out_summary':   BUCKET_AUTO_FIX,
    'A5_length_long':          BUCKET_AUTO_FIX,    # trim to fit
    'A5_length_short':         BUCKET_USER_INPUT,  # need more real content
    'A6_em_dash':              BUCKET_AUTO_FIX,
    'A7_demonstrating_closer': BUCKET_AUTO_FIX,
    # Role-aggregate.
    'B1_quantification':       BUCKET_USER_INPUT,  # need real numbers
    'B2_verb_diversity':       BUCKET_AUTO_FIX,
    'B3_structure_variation':  BUCKET_AUTO_FIX,
    # Resume-level.
    'C1_resume_length':        BUCKET_USER_INPUT,  # user-judgment on content
    'C2_buzzword_saturation':  BUCKET_AUTO_FIX,
}


_GROUNDING_BUCKET: dict[str, str] = {
    'unsupported_skill':  BUCKET_USER_INPUT,
    'unsupported_metric': BUCKET_USER_INPUT,
    'drop_skill_leak':    BUCKET_AUTO_FIX,
}


_SUPERVISOR_BUCKET: dict[str, str] = {
    # Section-keyed categories — the LLM has the user's profile + JD,
    # so rewriting a section is well-formed and within scope.
    'summary':             BUCKET_AUTO_FIX,
    'professional_summary': BUCKET_AUTO_FIX,
    'objective':           BUCKET_AUTO_FIX,
    # Skills has NO LLM-regen path in v2 — _generate_skills_line is a
    # deterministic comma-join from planner-allocated facts (no number
    # guard applies because there's no prose to ground). Adding or
    # removing a skill is something only the user can do, so supervisor
    # findings on the skills section route to USER_INPUT rather than
    # offering an auto-fix the system can't safely deliver.
    'skills':              BUCKET_USER_INPUT,
    'experience':          BUCKET_AUTO_FIX,
    'work_experience':     BUCKET_AUTO_FIX,
    'projects':            BUCKET_AUTO_FIX,
    'project':             BUCKET_AUTO_FIX,
    'certs':               BUCKET_AUTO_FIX,
    'certifications':      BUCKET_AUTO_FIX,
    'certification':       BUCKET_AUTO_FIX,
    'education':           BUCKET_AUTO_FIX,
    'languages':           BUCKET_AUTO_FIX,
    'awards':              BUCKET_AUTO_FIX,
    # Resume-level categories.
    'redundancy': BUCKET_AUTO_FIX,
    'ats':        BUCKET_AUTO_FIX,
    'ordering':   BUCKET_AUTO_FIX,
    'relevance':  BUCKET_AUTO_FIX,
    'grounding':  BUCKET_USER_INPUT,   # claim is unsupported; user must verify
    'layout':     BUCKET_ADVISORY,
}


_REGRESSION_BUCKET: dict[str, str] = {
    'skill_loss':        BUCKET_ADVISORY,
    'bullet_count_drop': BUCKET_USER_INPUT,
    'metric_loss':       BUCKET_USER_INPUT,
}


def _bullet_rule_prefix(rule_id: str) -> str:
    """Normalise a bullet rule_id to its canonical key.

    Validator emits rule_id values like 'A1_banned_phrase' or
    sometimes a longer variant 'A1_banned_phrase_X'. We match on
    the longest registered prefix; falling back to the leading
    two underscore-separated tokens (the convention is e.g. 'A1' +
    descriptor).
    """
    rid = str(rule_id or '')
    if not rid:
        return ''
    for canonical in sorted(_BULLET_RULE_BUCKET, key=len, reverse=True):
        if rid.startswith(canonical):
            return canonical
    parts = rid.split('_', 2)
    return '_'.join(parts[:2]) if len(parts) >= 2 else rid


def classify_bullet_rule(rule_id: str) -> str:
    return _BULLET_RULE_BUCKET.get(_bullet_rule_prefix(rule_id), BUCKET_USER_INPUT)


def classify_grounding(kind: str) -> str:
    return _GROUNDING_BUCKET.get(str(kind or '').lower(), BUCKET_USER_INPUT)


def classify_supervisor(category: str, severity: str = '', layer: str = '') -> str:
    sev = str(severity or '').lower()
    cat = str(category or '').lower().strip()
    lyr = str(layer or '').lower()
    if lyr and lyr != 'content':
        return BUCKET_ADVISORY
    if sev == 'warning':
        return BUCKET_ADVISORY
    return _SUPERVISOR_BUCKET.get(cat, BUCKET_USER_INPUT)


def classify_regression(kind: str) -> str:
    return _REGRESSION_BUCKET.get(str(kind or '').lower(), BUCKET_USER_INPUT)


def classify_finding(source: str, finding: dict | None) -> str:
    """Dispatch by source.

    source ∈ {'bullet', 'grounding', 'supervisor', 'regression'}.
    Unknown source / identifier → BUCKET_USER_INPUT (fail-safe).
    """
    src = str(source or '').lower()
    f = finding or {}
    if src == 'bullet':
        return classify_bullet_rule(f.get('rule_id', ''))
    if src == 'grounding':
        return classify_grounding(f.get('kind', ''))
    if src == 'supervisor':
        return classify_supervisor(
            f.get('category', ''),
            f.get('severity', ''),
            f.get('layer', ''),
        )
    if src == 'regression':
        return classify_regression(f.get('kind', ''))
    return BUCKET_USER_INPUT


def is_auto_fixable(source: str, finding: dict | None) -> bool:
    """Single decision boundary for loop-routing.

    Only AUTO_FIX-bucket findings enter the supervised regen loop's
    blocking set. USER_INPUT and ADVISORY findings bypass the loop.
    """
    return classify_finding(source, finding) == BUCKET_AUTO_FIX
