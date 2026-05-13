---
id: banned_patterns_002_ai_generated_tells
type: banned_pattern
title: AI-Generation Tells — Patterns Recruiters Spot in 5 Seconds
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# AI-Generation Tells — Patterns Recruiters Spot in 5 Seconds

These are the structural and rhetorical patterns that flag a resume as AI-generated, even when individual word choices avoid the banned-buzzword list. They mostly come from the LLM training-data biases that produce predictable rhetorical shapes. The rules below extend `profiles/services/prompt_guards.py`'s `HUMAN_VOICE_RULE` (especially rules 2, 4, and 5).

**Tell #1: The `<action>, demonstrating <skill>` closer.**
The single strongest AI tell.

- BANNED: "Built a CI pipeline, demonstrating strong DevOps skills."
- BANNED: "Refactored the auth service, showcasing expertise in distributed systems."
- BANNED: "Migrated to microservices, leveraging modern architectural patterns."

Fix is never to swap "demonstrating" for "showcasing"/"leveraging" — all equally banned. Delete the closing participle, end on the metric:

- GOOD: "Built a CI pipeline that cut release time from 40 min to 6 min."

**Tell #2: Rhythmic uniformity.** Every bullet follows the same `<Verb-ed> <object>, <-ing-result>` shape. See `bullet_patterns/007`.

**Tell #3: The "comprehensive list of N items" formula.** LLMs love to enumerate.

- BANNED: "Implemented features including authentication, authorization, user management, RBAC, audit logging, password reset, 2FA, session management, and account recovery."
- GOOD: "Built the authentication subsystem (JWT + refresh-token rotation, RBAC over a 14-role policy graph, account-recovery with TOTP); used by 24K DAU."

**Tell #4: Empty intensifiers.** "Successfully delivered", "effectively managed", "efficiently optimized" — adverbs add no information.

**Tell #5: The tricolon obsession.** "designed, built, and shipped"; "fast, reliable, and scalable". Real writing uses tricolons sparingly.

**Tell #6: Hedge words.** "Helped to", "played a role in", "was involved in" with strong claim language reads as LLM hedging from missing source material. Be honest about scope ("Contributed 4 of 11 modules") instead.

**Tell #7: Mirror-speak from the JD.** Repeating JD language verbatim ("excellent communication and collaboration") reads as low-effort tailoring. Demonstrate through evidence, don't echo.

**Tell #8: Round-number metrics.** "Improved performance by 30%". Real metrics rarely land on multiples of 5.

**Tell #9: Aspiration closer in Summary.** "Looking to leverage my expertise to drive impactful results in a forward-thinking organization." Universally hated.

## Concrete rule for SmartCV

The bullet validator must flag any bullet ending in a participial phrase ("demonstrating X", "showcasing Y", "leveraging Z", "leveraging cutting-edge"), any bullet with empty adverb intensifiers ("successfully", "effectively", "efficiently", "strategically"), any bullet enumerating more than 4 features, and any 3-consecutive-bullet window with identical shape. Flag round-number metrics for review against the source CV. Reject any Summary ending in an aspiration sentence ("Looking to...", "Seeking to...").

---
sources:
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resume-mistakes-to-avoid/  (accessed 2026-05-12)
