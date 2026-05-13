---
id: action_verbs_001_engineering_verbs
type: action_verb
title: Engineering and Technical Action Verbs
roles: [software_engineer, frontend, backend, fullstack, mobile, devops, data_engineer, ml_engineer, qa]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Engineering and Technical Action Verbs

Engineering bullets need verbs that describe what was built, what was changed, and what was measured. The verb sets compiled by MIT CAPD and The Muse for technical/creative work are the most directly applicable.

**Build / create:**
Architected, Authored, Built, Coded, Created, Designed, Developed, Devised, Engineered, Implemented, Programmed, Prototyped, Wrote.

**Improve / change:**
Accelerated, Enhanced, Extended, Migrated, Modernized, Modified, Optimized, Overhauled, Rearchitected, Refactored, Rewrote, Simplified, Streamlined, Upgraded.

**Operate / run:**
Automated, Configured, Deployed, Maintained, Monitored, Operated, Orchestrated, Provisioned, Scaled, Scheduled, Tuned.

**Investigate / fix:**
Debugged, Diagnosed, Detected, Investigated, Isolated, Profiled, Resolved, Root-caused, Tested, Triaged, Troubleshooted.

**Measure / verify:**
Benchmarked, Instrumented, Measured, Profiled, Tested, Validated, Verified.

**Verbs to avoid in engineering bullets:**
- "Helped" — the bullet should describe what *you* did, not what you assisted with.
- "Worked on" — too vague; specify whether you built, debugged, refactored, or owned.
- "Was responsible for" — duty language, not accomplishment language. MIT explicitly recommends rewriting "responsible for delivering projects on time" to "ensured projects were delivered on or ahead of schedule".
- "Utilized" / "leveraged" — banned in SmartCV's `prompt_guards.py`. Replace with "used".
- "Spearheaded" — banned for the same reason (overused buzzword).

**Pairing verbs with metrics.**
Engineering verbs become strongest when paired with a measurable outcome. Examples:

- "Refactored the auth service from monolithic Django views into a 6-endpoint FastAPI module, cutting p95 latency from 280ms to 90ms."
- "Built a Storybook component library used by 4 product teams, replacing 12 duplicated snippets across the codebase."
- "Migrated 38 PostgreSQL queries from raw SQL to SQLAlchemy ORM, reducing query-related bugs in production from 4/month to 0 over 6 months."

Each example pairs a build/improve verb (Refactored, Built, Migrated) with a concrete artifact (auth service, component library, queries) and a measurable result (latency, count, bug rate).

**Variation.** Across any 3 consecutive bullets in a single role, no two should start with the same verb. Mix build/create verbs with improve/change verbs and operate/run verbs. The `prompt_guards.py` HUMAN_VOICE_RULE also requires that at least one of any 3 consecutive bullets does NOT start with a verb at all — lead with the system name, the outcome, or the scale instead.

## Concrete rule for SmartCV

When generating engineering bullets, prefer Build/Improve/Operate/Fix verbs from the lists above. Never start a bullet with "Helped", "Worked on", "Was responsible for", "Utilized", "Leveraged", or "Spearheaded". Vary the opening verb across consecutive bullets in the same role. Each engineering bullet should pair the verb with both a concrete artifact (system, service, library, dataset) and a measurable result (%, ms, count, rate, throughput).

---
sources:
  - https://capd.mit.edu/resources/resume-action-verbs/  (accessed 2026-05-12)
  - https://www.themuse.com/advice/185-powerful-verbs-that-will-make-your-resume-awesome  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resumes-cover-letters/195-action-verbs-to-make-your-resume-stand-out  (accessed 2026-05-12)
