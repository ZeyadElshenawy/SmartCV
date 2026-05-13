---
id: banned_patterns_001_overused_buzzwords
type: banned_pattern
title: Overused Buzzwords — Banned and Their Replacements
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Overused Buzzwords — Banned and Their Replacements

This file is the canonical banned-buzzword list for the SmartCV bullet validator. It extends the existing `HUMAN_VOICE_RULE` constant in `profiles/services/prompt_guards.py`. The phrases below are banned because (a) they are AI-generation tells, (b) recruiter surveys consistently rank them as the most-disliked resume language, or (c) they replace concrete information with empty self-praise.

**Banned verbs (extending `prompt_guards.py`):**
- leverage, leveraging, leveraged → use, used, using
- utilize, utilizing, utilized → use, using, used
- spearhead, spearheaded → led, started, founded
- embark, embarked, embarking → start, started, starting
- delve, delving, delved → investigate, study, examine
- unleash, unleashed → release, launch, ship
- elevate, elevated, elevating → improve, raise, lift
- navigate (figurative) → resolve, work through, get past
- foster (figurative) → mentor, coach, build (in concrete sense)

**Banned adjectives (extending `prompt_guards.py`):**
- robust → reliable, strong, well-tested
- seamless, seamlessly → smooth, integrated (or delete; "seamless integration" is almost always meaningless)
- holistic → comprehensive, end-to-end (with a defined scope)
- dynamic → fast-changing (with what is changing named)
- innovative → new, novel (with what is new named)
- cutting-edge → modern, current (with the actual technology named)
- world-class → top-tier (with the comparison named)
- best-in-class → leading (with the segment named)
- transformative → significant, substantial (with the scope named)
- game-changing → significant (with the actual change named)
- paradigm-shifting → significant (delete the "paradigm" framing)

**Banned self-description words (delete):**
passionate, highly motivated, results-driven, results-oriented, detail-oriented, self-starter, team player, go-getter, thought leader, visionary, guru, ninja, rockstar, wizard, bring a unique ability.

**Banned nouns / concepts:**
synergy, synergize, ecosystem (figurative), tapestry, paradigm (figurative), unlock potential.

**Recruiter-disliked corporate jargon:**
think outside the box, low-hanging fruit, circle back, touch base, move the needle, boil the ocean, bandwidth (capacity sense), align (no object), liaise / liaised.

**Em-dash tell.** `prompt_guards.py` rule 7 bans em-dashes (—). LLMs over-produce them. Replace with comma or delete.

**Why this matters.** A resume with perfect ATS formatting, quantified bullets, and varied structure can still be rejected if saturated with these buzzwords. Recruiter surveys and career-coaching guidance consistently rank "Highly motivated", "Results-driven", "Passionate", "Spearheaded", "Leverage" in the top 10 most-overused resume words for over a decade.

## Concrete rule for SmartCV

The bullet validator must reject any bullet containing any of the banned verbs, adjectives, self-description words, or nouns above. The replacement table is the substitution rubric: when a candidate's source CV contains a banned word, replace with the listed alternative; when no alternative makes sense (e.g., "passionate"), delete the offending phrase and let the surrounding concrete content carry the bullet.

---
sources:
  - https://www.indeed.com/career-advice/resume-mistakes-to-avoid/  (accessed 2026-05-12)
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
  - https://www.themuse.com/advice/185-powerful-verbs-that-will-make-your-resume-awesome  (accessed 2026-05-12)
