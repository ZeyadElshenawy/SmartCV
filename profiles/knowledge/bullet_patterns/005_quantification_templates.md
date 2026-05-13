---
id: bullet_patterns_005_quantification_templates
type: bullet_pattern
title: Quantification Templates — How to Find a Number for Every Bullet
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Quantification Templates — How to Find a Number for Every Bullet

The MIT, Harvard, and Berkeley career guides all converge on the same rule: every accomplishment bullet should have at least one quantified outcome. MIT's resume guide states: "Quantify if you can. If you gave a presentation, include how many people attended." The challenge for a generation tool is finding *which* number to use when the source CV is sparse.

**Six quantification categories, priority order:**

1. **Direct outcome metrics** — latency (`from 280ms to 90ms`), conversion (`+11 pp`), revenue (`$N MRR`), error rate, throughput.
2. **Scale of work** — LOC touched, services migrated, tables in scope, customers onboarded, endpoints owned.
3. **Time** — `cut deploy from 40 min to 6 min`, `shipped in 8 weeks vs. 12 estimated`.
4. **Adoption / reach** — `used by 4 teams`, `consumed by 12 engineers`, `referenced in 14 docs`.
5. **Frequency / cadence** — `release shifted from monthly to bi-weekly`, `incidents 3/month to 0.5/month`.
6. **Cost** — `cut AWS spend by $14K/month`, `owned $180K budget`.

**Fallback: artifact-named outcome.** When no number exists, name a concrete artifact whose existence is the outcome:
- `became the team's standard onboarding playbook`.
- `the design doc was used in every subsequent service launch in 2025`.
- `eliminated the manual on-call escalation runbook in favor of auto-paging`.

**Quantification levels by role.**

- **Engineering:** latency, error rate, throughput, build time, test coverage %.
- **Data:** dataset size + model metric (accuracy/F1/AUC/NDCG/RMSE) + business impact.
- **Product:** retention/conversion delta, adoption %, NPS shift, revenue.
- **Design:** usability success rate, A/B outcome, surface count, adoption count.
- **Leadership:** team size, budget, timeline, hire count, promotion count.

**HUMAN_VOICE_RULE constraint.** `prompt_guards.py` rule 3 requires every bullet to name at least one concrete thing — tool/framework/system/dataset/model OR measurable outcome OR time-scoped result. Rule 3 also bans fabrication.

**Anti-patterns:**

- Round numbers ("Improved performance by 30%") — real metrics rarely land on round 5s.
- Percentages without baselines: "40% of what?"
- Unverifiable compounds: "Increased team productivity by 3x".
- Stacking two metrics for one accomplishment — the second usually has no real source.

## Concrete rule for SmartCV

Every accomplishment bullet must include either: (a) a direct-outcome metric (latency, error rate, throughput, conversion, revenue, count, etc.) sourced from the input CV, OR (b) a concrete-artifact outcome (named system, named playbook, named adoption count). Use the source CV's exact numbers — never round, infer, or fabricate. When numerical metrics are absent from the source, fall back to artifact-named outcomes; never invent percentages, dollar figures, or time deltas.

---
sources:
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resumes-cover-letters/star-method-resume  (accessed 2026-05-12)
  - https://www.themuse.com/advice/185-powerful-verbs-that-will-make-your-resume-awesome  (accessed 2026-05-12)
