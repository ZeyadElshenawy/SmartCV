---
id: bullet_patterns_001_star_method
type: bullet_pattern
title: STAR Method for Resume Bullets
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# STAR Method for Resume Bullets

The STAR method (Situation, Task, Action, Result) was originally a framework for behavioral interview answers but adapts directly to resume bullets. The method has four parts:

1. **Situation** — the context or challenge (1 short clause).
2. **Task** — your role / what you needed to accomplish (often merged with Situation in a resume bullet).
3. **Action** — what you specifically did (the verb-led core of the bullet).
4. **Result** — the measurable outcome (the metric or concrete consequence).

In an interview answer all four parts get separate sentences. In a resume bullet, Situation and Task collapse into a brief context phrase, Action is the verb-led main clause, and Result is the closing measurable outcome. The Wikipedia entry on STAR notes that some practitioners substitute "Target" for Task to emphasize self-directed motivation, but in a resume bullet either word works.

**Template (compressed):**
`<Action verb> <object/system> <[Situation/Task context]>, <Result with metric>.`

**Examples:**

- BAD (no Result): "Refactored the authentication service to use JWT tokens."
- GOOD (STAR): "Refactored the authentication service from session cookies to JWT tokens, cutting p95 login latency from 280ms to 90ms across 12K daily active users."

- BAD (no Action specificity): "Worked on the checkout flow to improve conversion."
- GOOD (STAR): "Redesigned the checkout flow from 5 screens to 2 after identifying drop-off in funnel analytics; A/B test on 8K users showed cart-abandonment fell from 38% to 24%."

- BAD (no Situation): "Built a CI pipeline."
- GOOD (STAR): "Built a GitHub Actions CI pipeline replacing the manual Jenkins setup that was producing 4–6 broken main-branch builds per week; broken builds dropped to 0–1 per month."

**Action leads.** Even though STAR lists Situation first, in a bullet the verb leads. Burying action behind situation weakens scannability. Indeed's STAR-for-resumes guide recommends starting with action verbs and condensing Situation/Task into a context phrase.

**When STAR is overkill.** Technical-scope bullets ("Languages used: Python, Go, Rust") don't need STAR. Reserve STAR for accomplishment bullets — the kind you'd talk about in an interview.

**Common STAR mistakes:**

- Too much Situation. Trim to one clause or omit.
- Vague Action ("Worked on improving the system"). Use specific verbs (Refactored, Migrated, Built).
- Soft Result ("Helped the team work better"). Use a metric or named-artifact outcome.
- Result without Action. Lead with what *you* did; let the Result close.

## Concrete rule for SmartCV

Generate accomplishment bullets following the compressed STAR template: `<specific Action verb> <concrete object> [<short Situation/context clause>], <measurable Result>`. The Action verb leads. The Situation is at most one short clause and may be omitted if obvious from the role. The Result must include at least one number, percentage, time delta, or named-artifact outcome. Never write a bullet that has Action without Result, or Result without Action.

---
sources:
  - https://www.indeed.com/career-advice/interviewing/how-to-use-the-star-interview-response-technique  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resumes-cover-letters/star-method-resume  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Situation,_task,_action,_result  (accessed 2026-05-12)
