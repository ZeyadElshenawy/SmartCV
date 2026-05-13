---
id: bullet_patterns_006_before_after_rewrites
type: bullet_pattern
title: Before-and-After Bullet Rewrites — Worked Examples
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Before-and-After Bullet Rewrites — Worked Examples

Paired before/after examples from MIT, Indeed, Jobscan failure-mode docs.

**1. Duty → accomplishment.**
- BEFORE: "Responsible for managing the deployment pipeline."
- AFTER: "Maintained the GitHub Actions deployment pipeline across 18 services; cut mean release time from 40 min to 6 min."

**2. Vague verb → specific.**
- BEFORE: "Worked on the search feature."
- AFTER: "Refactored the search ranker into a 3-stage retrieve-rerank-filter pipeline; lifted NDCG@10 from 0.34 to 0.49 on the 12K-query held-out set."

**3. No metric → quantified.**
- BEFORE: "Improved API performance."
- AFTER: "Reduced p99 API latency from 940ms to 210ms by replacing the N+1 query with a batched JOIN and adding a Redis cache."

**4. `, demonstrating <skill>` closer.**
- BEFORE: "Built a CI pipeline, demonstrating strong DevOps skills."
- AFTER: "Built a GitHub Actions CI pipeline replacing the manual Jenkins setup; broken main-branch builds dropped from 4–6/week to 0–1/month."

**5. Banned power-verb.**
- BEFORE: "Spearheaded the migration to leverage modern cloud infrastructure."
- AFTER: "Led the migration of 14 services from VMware to AWS ECS over 6 months; cut monthly infra spend by $9.4K."

**6. Generic claim → concrete artifact.**
- BEFORE: "Improved code quality across the team."
- AFTER: "Authored the API design guide; referenced in every new-service review (12 in Q4); adopted as platform-wide standard in Feb 2025."

**7. Inflated leadership at junior level.**
- BEFORE: "Spearheaded the rewrite of the customer portal."
- AFTER: "Contributed 4 of 11 modules in the customer-portal rewrite; my onboarding-flow module shipped 1 sprint ahead of estimate."

**8. Double-verb redundancy.**
- BEFORE: "Researched and analyzed churn data to identify and discover key drop-off points."
- AFTER: "Analyzed 18 months of churn data (2.4M events); identified 3 friction points driving 60% of cancellations."

**9. Inside-out summary opener.**
- BEFORE: "With 5 years of experience in backend engineering, I bring a unique ability to architect scalable systems."
- AFTER: "Backend engineer, 5 years; led billing-service migration to event-sourced architecture, dropping reconciliation errors from 3% to under 0.1%."

**10. Buzzword soup.**
- BEFORE: "Highly motivated, results-driven engineer passionate about leveraging cutting-edge technology."
- AFTER: "Backend engineer focused on payment-system reliability; most recent project cut chargeback on-call pages from 9/month to 1/month."

**11. Long Situation, no Action.**
- BEFORE: "In a fast-paced fintech environment with significant tech debt, played a key role on a team of 6 tasked with delivering critical infrastructure improvements."
- AFTER: "Rebuilt the payment-retry layer (Postgres advisory locks + idempotency keys); duplicate charges dropped from 0.4% to 0.01%."

## Concrete rule for SmartCV

When generating or rewriting a bullet, run through this checklist: (1) starts with a specific action verb (not Responsible-for, not Worked-on); (2) names a concrete artifact (system, dataset, tool, framework); (3) includes a quantified or artifact-named outcome; (4) contains no banned phrases (`, demonstrating X`, "leveraged", "spearheaded", inside-out openers, "passionate about"); (5) varies in shape from the bullets immediately above and below it. If any check fails, rewrite using the before/after patterns above as templates.

---
sources:
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resume-mistakes-to-avoid/  (accessed 2026-05-12)
