---
id: bullet_patterns_007_sentence_structure_variation
type: bullet_pattern
title: Sentence Structure Variation Across Consecutive Bullets
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Sentence Structure Variation Across Consecutive Bullets

The single largest AI-detection signal after banned-word use is rhythmic repetition: bullets that all follow the same grammatical shape. SmartCV's `prompt_guards.py` HUMAN_VOICE_RULE rule 4 explicitly addresses this: "VARY SENTENCE STRUCTURE — this is the #1 AI tell after banned words. Of any 3 consecutive bullets in the same role, AT LEAST ONE must NOT start with a verb."

**Four bullet shapes to mix:**

1. **Verb-led (default):** `<Action verb> <object> <context>, <Result>.` Example: "Refactored the auth service from session cookies to JWT, cutting p95 from 280ms to 90ms."
2. **System-led:** `<System> <was changed>; <result>.` Example: "The auth service moved to JWT under a 4-week refactor; p95 fell from 280ms to 90ms."
3. **Outcome-led:** `<Outcome>, <after / by X>.` Example: "p95 dropped from 280ms to 90ms after refactoring auth from session cookies to JWT."
4. **Scale-led:** `Across <N>, <action and outcome>.` Example: "Across 18 services, standardized the deployment pipeline; broken builds dropped from 4–6/week to 0–1/month."

**3-bullet variation rule.** For any 3-consecutive-bullet window:
- No two start with the same verb.
- At least one is non-verb-led.
- At least one has a different length.

**Worked example: bad (uniform) vs. good (varied).**

BAD (all verb-led, same shape):
- Refactored the auth service from session cookies to JWT, cutting p95 latency from 280ms to 90ms.
- Migrated the user DB from MySQL to Postgres, reducing query errors by 67%.
- Implemented Redis caching for hot endpoints, decreasing API response time by 40%.

GOOD (varied):
- Refactored the auth service from session cookies to JWT; p95 login latency fell from 280ms to 90ms.
- The user-DB migration from MySQL to Postgres landed on a 6-week timeline, removing the last MySQL-specific script from the codebase.
- p99 search latency dropped from 800ms to 120ms after re-indexing the 12M-row product table on (category, price).
- Across 18 services, standardized the GitHub Actions deploy pipeline; broken builds dropped from 4–6/week to 0–1/month.

The GOOD version mixes verb-led, system-led, outcome-led, scale-led. No two start with the same verb.

**Why this matters more than other rules.** Recruiters spot AI resumes in 5 seconds when every bullet has the same `<Verb-ed> <object>, <-ing-result>` shape. Even with quantified outcomes and no banned words, rhythmic uniformity signals LLM authorship.

**The "consecutive parallel" trap.** LLMs love to generate "Improved X by Y%" / "Improved A by B%" / "Improved C by D%". Even with real metrics, the parallel reads as machine-generated. Break by varying verbs and shapes.

## Concrete rule for SmartCV

When generating bullets for a single role, vary across the four shapes (verb-led, system-led, outcome-led, scale-led). For any 3 consecutive bullets, no two may start with the same verb, and at least one must NOT start with a verb. If the model produces 5 bullets all with the same shape, regenerate or rewrite the bullets in positions 2 and 4 in non-verb-led shapes.

---
sources:
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
