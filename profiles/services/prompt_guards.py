"""Shared anti-AI-tell prompt fragments for prose generation (resume bullets,
cover letters, LinkedIn/email outreach).

Captures the four common AI tells real recruiters notice:

1. "Robotic" vocabulary that no human uses unprompted.
2. The "<verb-ed> <object>, demonstrating <skill set>" closer.
3. Repetitive "<action> resulting in <outcome>" sentence templates across
   consecutive bullets.
4. "Inside-out" opener clichés like "With N years of experience in X, I bring
   a unique ability to Y." — the LinkedIn About-section giveaway.

Single source of truth so generators can't drift apart.

This module exposes three artifacts:
  - HUMAN_VOICE_RULE  — narrative prompt block (LLM-facing).
  - BANNED_PHRASES    — dict mapping each banned token to its replacement
                        (or None when the right action is "delete"). Used by
                        the bullet validator's deterministic A1 rule and by
                        benchmarks/llm_judge.banned_phrase_hits.
  - BANNED_JARGON_PHRASES — extra corporate-jargon set with higher false-
                        positive risk ("align", "bandwidth"); opt-in only.
"""


HUMAN_VOICE_RULE = """=== HUMAN VOICE — DO NOT SOUND LIKE AI ===
1. BANNED WORDS (do not use, ever):
   - leverage, leveraging, leveraged, utilize, utilizing, utilized, synergy,
     synergize, robust, seamless, seamlessly, delve, delving, unleash,
     elevate, navigate (figurative), cutting-edge, world-class, best-in-class,
     game-changer, paradigm, tapestry, holistic, ecosystem (figurative),
     spearhead, embark, foster (figurative), unlock potential, transformative,
     dynamic, innovative, passionate, results-driven, go-getter, thought leader.
   - When tempted, use plain English: "leveraged" → "used"; "utilized" → "used";
     "robust" → "reliable" or "strong"; "spearheaded" → "led".
2. NEVER use the closer pattern "<action>, demonstrating <skill/ability>".
   - Wrong: "Built a CI pipeline, demonstrating strong DevOps skills."
   - Right: "Built a CI pipeline that cut release time from 40 min to 6 min."
   - If you must reference the skill applied, name the concrete result instead.
   - Replacing "demonstrating" with "leveraging" is also banned — both are AI tells.
3. SPECIFICITY — every bullet must name at least one concrete thing.
   Concrete = a named tool / framework / system / dataset / model / API,
   a measurable outcome (%, ms, $, users, requests/sec, error rate, team size),
   or a time-scoped result ("cut deploy time from 40 min to 6 min").
   - Wrong: "Built reusable components to improve team productivity." (no
     framework, no metric, no system — could apply to any frontend job).
   - Right: "Built a Storybook component library used by 4 product teams,
     replacing 12 duplicated snippets across the codebase."
   A bullet that could be pasted into any other resume in the same field is
   too generic — rewrite it around the specific evidence in the source CV,
   or drop it. Do NOT invent metrics that aren't in the source.
4. VARY SENTENCE STRUCTURE — this is the #1 AI tell after banned words.
   - Do not put two bullets / sentences in a row with the same shape, e.g.,
     "<verb-ed> <thing> resulting in <outcome>" twice. Mix in short sentences,
     ones that lead with the outcome, ones that name the tool first, etc.
   - Of any 3 consecutive bullets in the same role, AT LEAST ONE must NOT
     start with a verb — lead it with the system name ("React + TypeScript
     stack ..."), with the outcome ("p95 dropped from 2.3s to 480ms after..."),
     or with the scale ("Across 12 microservices ...").
   - Vary opening verbs across consecutive bullets (no two start with "Led").
5. NO INSIDE-OUT OPENERS for summaries / about / cover-letter intros.
   - Banned templates:
     "With <N> years of experience in <X>, I bring a unique ability to <Y>."
     "As a <role> with <expertise>, I am passionate about <thing>."
     "Driven by <quality>, I excel at <activity>."
   - Write like a human would actually talk: lead with what you did or what
     you're after, in plain words. Short, direct, specific.
6. SUMMARY TONE — a senior recruiter's eye-roll list:
   - "Highly motivated", "results-oriented", "detail-oriented", "team player",
     "self-starter", "fast-paced environment", "go-getter", "passionate about" —
     all banned. Show, don't claim.
   - The summary should read like 2-3 sentences a real engineer would say
     about themselves over coffee, not a LinkedIn About section. Lead with
     role + years, then ONE concrete proof point taken from the CV.
7. Replace em dashes (—) with a comma or delete them.
8. No first-person "I am writing to express my interest" filler in prose.
"""


def append_human_voice(prompt: str) -> str:
    """Append the human-voice rule block to a prompt. Use when an existing
    prompt has its own STRICT block — placement at the very end keeps the rule
    fresh in the model's last-attention window.
    """
    return f"{prompt.rstrip()}\n\n{HUMAN_VOICE_RULE}"


# ---------------------------------------------------------------------------
# Banned-phrase contract — machine-readable form of the HUMAN_VOICE_RULE rule 1.
#
# Sourced from profiles/knowledge/banned_patterns/001_overused_buzzwords.md
# (the canonical KB list). Keys are lowercase substrings the validator
# matches case-insensitively; values are the suggested replacement, or None
# when the right action is "delete the offending word and let the surrounding
# concrete content carry the bullet" (e.g. "passionate", "results-driven").
# ---------------------------------------------------------------------------

# Type hint uses str | None for Python 3.10+ compat (project pins 3.13).
BANNED_PHRASES: dict[str, str | None] = {
    # --- Verbs (with substitutions) ---
    "leverage": "use", "leveraging": "using", "leveraged": "used",
    "utilize": "use", "utilizing": "using", "utilized": "used",
    "spearhead": "led", "spearheaded": "led", "spearheading": "leading",
    "embark": "start", "embarked": "started", "embarking": "starting",
    "delve": "investigate", "delving": "investigating", "delved": "investigated",
    "unleash": "release", "unleashed": "released", "unleashing": "releasing",
    "elevate": "improve", "elevated": "improved", "elevating": "improving",
    "foster": None,        # figurative sense; deletion is right when ambiguous
    "navigate": None,      # figurative sense
    "demonstrating": None, # also caught structurally by A7 closer regex

    # --- Adjectives (with substitutions or deletion) ---
    "robust": "reliable",
    "seamless": "smooth", "seamlessly": "smoothly",
    "holistic": "comprehensive",
    "cutting-edge": "modern",
    "world-class": "top-tier",
    "best-in-class": "leading",
    "transformative": "significant",
    "game-changing": "significant",
    "game-changer": "significant",
    "paradigm-shifting": "significant",
    "dynamic": None,
    "innovative": None,

    # --- Self-description filler (delete) ---
    "passionate": None,
    "highly motivated": None,
    "results-driven": None,
    "results-oriented": None,
    "detail-oriented": None,
    "self-starter": None,
    "team player": None,
    "go-getter": None,
    "thought leader": None,
    "visionary": None,
    "guru": None,
    "ninja": None,
    "rockstar": None,
    "wizard": None,

    # --- Nouns / concepts (delete; almost always figurative) ---
    "synergy": None,
    "synergize": None,
    "tapestry": None,
    "paradigm": None,
    "unlock potential": None,
}

# Higher false-positive risk — corporate jargon. Gated by settings.BULLET_VALIDATOR_STRICT.
BANNED_JARGON_PHRASES: frozenset[str] = frozenset({
    "think outside the box",
    "low-hanging fruit",
    "circle back",
    "touch base",
    "move the needle",
    "boil the ocean",
    "bandwidth",   # capacity sense; "network bandwidth" is a real engineering term
    "align",       # often legitimate; gate behind strict
    "liaise",
    "liaised",
})
