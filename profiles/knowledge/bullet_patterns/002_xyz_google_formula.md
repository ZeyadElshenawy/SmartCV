---
id: bullet_patterns_002_xyz_google_formula
type: bullet_pattern
title: The X-Y-Z Formula (Google / Laszlo Bock)
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# The X-Y-Z Formula (Google / Laszlo Bock)

Laszlo Bock, Google's former SVP of People Operations, published the X-Y-Z resume formula in his 2015 book "Work Rules!" and in subsequent Google career-recruiting interviews. The formula is the single most-cited modern resume bullet template and is what Google's own recruiters look for.

**The formula:**
`Accomplished [X] as measured by [Y] by doing [Z].`

- **X** = what you accomplished — the outcome.
- **Y** = the measurement — how the outcome is quantified.
- **Z** = the action — what you actually did.

The order in the template puts the outcome first, but in a resume bullet most writers reorder to lead with the verb (Action) and end with the metric (X-as-measured-by-Y). Both orderings are valid; what matters is that all three slots are filled.

**Examples:**

- BAD: "Improved the search ranking algorithm."
- GOOD (X-Y-Z): "Improved search result relevance by 22% (NDCG@10) by re-tuning the ranker's feature weights and adding a query-intent classifier."

- BAD: "Reduced infrastructure costs."
- GOOD (X-Y-Z): "Cut AWS spend by 31% ($14K/month) by right-sizing 47 EC2 instances and migrating cold-storage data from S3 Standard to Glacier."

- BAD: "Made the API faster."
- GOOD (X-Y-Z): "Reduced p99 API latency from 940ms to 210ms by replacing the N+1 query with a batched JOIN and adding a Redis cache for the hottest 5% of endpoints."

**Why the formula works.** It enforces that every bullet contains both *what you did* (Z) and *what changed* (X measured by Y). A bullet missing X is duty language. A bullet missing Y is unmeasurable claim. A bullet missing Z is bragging without evidence.

**Comparison to STAR.** X-Y-Z is the compressed Action-Result core of STAR with stronger measurement emphasis. STAR includes the Situation/Task context; X-Y-Z assumes context from the role header. For most engineering bullets, X-Y-Z is cleaner; for non-obvious situations, STAR's explicit Situation slot is valuable.

**When you can't quantify Y.** Some work resists numerical quantification — security, code quality, design-doc authorship. Replace numerical Y with concrete-artifact Y:

- "Eliminated SQL injection risk in 14 query sites via a parameterized-query helper that became the codebase standard."
- "Authored the API design guide; referenced in every new-service review (12 in Q4)."

The Y slot is filled by adoption, scope, or named artifact.

**Anti-pattern: fake X-Y-Z.** Inventing metrics is the most damaging mistake. "Improved code quality by 35%" is meaningless and exposed in an interview. SmartCV's `prompt_guards.py` rule 3: "Do NOT invent metrics that aren't in the source."

## Concrete rule for SmartCV

Generate every accomplishment bullet to satisfy all three X-Y-Z slots: action (Z), what changed (X), and how it was measured (Y). When the source CV provides numerical metrics, use them verbatim. When no numerical metric is available, satisfy Y with a concrete-artifact outcome (adoption count, scope, named-artifact). NEVER fabricate a numerical Y — that risks the candidate failing the interview verification step.

---
sources:
  - https://www.indeed.com/career-advice/resumes-cover-letters/star-method-resume  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resumes-cover-letters/195-action-verbs-to-make-your-resume-stand-out  (accessed 2026-05-12)
