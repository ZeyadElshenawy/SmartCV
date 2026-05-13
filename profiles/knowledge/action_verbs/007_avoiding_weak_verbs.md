---
id: action_verbs_007_avoiding_weak_verbs
type: action_verb
title: Weak Verbs and Their Replacements
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Weak Verbs and Their Replacements

> **Cross-reference:** the canonical banned-buzzword list lives in `banned_patterns/001_overused_buzzwords.md`. This file groups the verb subset with replacement guidance for use during bullet rewriting; the retriever may surface both — treat that file as authoritative for the ban list itself.

Even with a strong verb library, certain weak verbs and duty-language openers slip into drafts and instantly downgrade the resume. MIT CAPD's resume guidance specifically calls these out: "Avoid the passive voice. Avoid the phrase 'responsible for' and use action verbs instead." The replacements below come from MIT, Harvard, and Muse compilations cross-checked against `prompt_guards.py`'s banned-phrase list.

**Duty language → accomplishment language.**

- "Responsible for managing the deployment pipeline" → "Maintained the deployment pipeline; cut mean release time from 40 min to 6 min."
- "Tasked with onboarding new engineers" → "Onboarded 8 new engineers across 3 cohorts; time-to-first-PR dropped from 9 to 3 days."
- "In charge of code reviews" → "Reviewed 200+ PRs across the platform team in Q3, catching 11 production-blocking bugs pre-merge."
- "Duties included writing documentation" → "Authored 14 architecture docs that became the team's canonical reference for the migration."

**Vague verbs → specific verbs.**

- "Worked on the auth service" → "Refactored the auth service" / "Debugged the auth service" / "Migrated the auth service" (pick the one that's true).
- "Helped with the launch" → "Coordinated the launch checklist" / "Wrote the rollout playbook" / "Owned the post-launch monitoring".
- "Dealt with the migration" → "Led the migration" / "Executed the migration" / "Unblocked the migration".
- "Was involved in the rewrite" → "Authored 4 modules of the rewrite" / "Reviewed and approved the rewrite architecture".

**Banned overused power-verbs (from `prompt_guards.py`):**
- Spearheaded → Led, Started, Founded.
- Leveraged / Utilized → Used.
- Embarked on → Started, Began.
- Delved into → Investigated, Studied.
- Unleashed → Released, Launched, Shipped.
- Empowered → Enabled, Mentored, Removed blockers for.
- Fostered (figurative) → name the actual mechanism (mentored, paired, coached).
- Navigated (figurative) → Resolved, Worked through.

**Passive → active.**
- "The pipeline was rebuilt by me" → "Rebuilt the pipeline".
- "Bugs were identified" → "Identified 11 production-blocking bugs in Q3 code review".

**"Demonstrating" closer — banned.** The `<action>, demonstrating <skill>` closer is one of the strongest AI tells. Don't swap "demonstrating" for "leveraging" or "showcasing" — equally banned. Delete the closer entirely and let the metric speak.

## Concrete rule for SmartCV

When generating or rewriting a bullet, first check whether it starts with "Responsible for", "Tasked with", "In charge of", "Duties included", or any passive construction — rewrite to start with a specific action verb. Then check for the verbs in the banned list above (Spearheaded, Leveraged, Utilized, etc.) and replace each with the plain-English equivalent. Finally, scan for the ", demonstrating <skill>" closer — delete it and end the bullet on the concrete outcome instead.

---
sources:
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
  - https://capd.mit.edu/resources/resume-action-verbs/  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resume-mistakes-to-avoid/  (accessed 2026-05-12)
