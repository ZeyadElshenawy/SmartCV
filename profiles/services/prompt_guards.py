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
3. VARY SENTENCE STRUCTURE.
   - Do not put two bullets / sentences in a row with the same shape, e.g.,
     "<verb-ed> <thing> resulting in <outcome>" twice. Mix in short sentences,
     ones that lead with the outcome, ones that name the tool first, etc.
   - Vary opening verbs across consecutive bullets (no two start with "Led").
4. NO INSIDE-OUT OPENERS for summaries / about / cover-letter intros.
   - Banned templates:
     "With <N> years of experience in <X>, I bring a unique ability to <Y>."
     "As a <role> with <expertise>, I am passionate about <thing>."
     "Driven by <quality>, I excel at <activity>."
   - Write like a human would actually talk: lead with what you did or what
     you're after, in plain words. Short, direct, specific.
5. Replace em dashes (—) with a comma or delete them.
6. No first-person "I am writing to express my interest" filler in prose.
"""


def append_human_voice(prompt: str) -> str:
    """Append the human-voice rule block to a prompt. Use when an existing
    prompt has its own STRICT block — placement at the very end keeps the rule
    fresh in the model's last-attention window.
    """
    return f"{prompt.rstrip()}\n\n{HUMAN_VOICE_RULE}"
