---
id: seniority_norms_007_ml_senior
type: seniority_norm
title: Senior+ ML Engineer — Verb Deflation and Over-Abstraction Failure Modes
roles: [ml_engineer]
seniority: [senior]
industries: [all]
region: global
weight: high
last_updated: 2026-05-16
---

# Senior+ ML Engineer — Verb Deflation and Over-Abstraction Failure Modes

A senior ML engineer (7+ years, plus Staff / Principal / Architect bands) is no longer rewarded for shipping models. The resume's job is to demonstrate sustained scope (multi-model platform, multi-team initiative), architecture judgment (consequential decisions with named trade-offs), and influence beyond direct deliverables (hiring, mentorship at scale, the team practices that shape how others work). The two failure modes on senior ML resumes are mirror images: under-claim (verb deflation — writing junior-coded bullets that hide actual scope) and over-claim (over-abstraction — writing management-deck bullets that name nothing concrete). Both fail the same way: the recruiter cannot evaluate the candidate.

## Failure mode A — Verb deflation

The under-claim failure: a senior ML engineer who's spent 3 years owning an ML platform writes "Built classification models" and "Helped with deployment". The verbs are accurate but the scope is invisible.

**Senior-credible verbs (surface these when the source CV under-claims):**
Architected, Designed (the system / the eval framework), Drove (a multi-quarter initiative), Decided (build-vs-fine-tune-vs-API after named evaluation), Owned (a platform, a domain, a hiring loop), Led (a team, a workstream, an RFC), Mentored (multiple direct reports), Hired (named hire count), Established (a discipline, a practice, an artifact used cross-team), Authored (RFCs, eval frameworks, model cards).

**Note on "Decided".** This verb almost always requires the trade-off context to land. Bare "Decided to fine-tune Llama-3-8B" reads as inflation — it sounds like the candidate woke up and picked a model. "Decided to fine-tune Llama-3-8B after a 4-week comparison vs. GPT-4-turbo API and a domain-specific Ragas eval" reads as senior judgment — the credibility comes from naming the alternatives that were considered and rejected. Use "Decided" only when the bullet also names the evaluation that produced the decision; otherwise prefer "Chose" with an inline trade-off clause or "Owned the decision to" with the decision context.

**Junior-grade verbs (deflate senior work — reject in senior bullets unless context demands):**
Built, Implemented, Ran, Helped, Worked on, Used, Followed, Contributed to.

**Verb-substitution table (deflation direction — surface senior verbs where the candidate under-claims):**

| Under-claimed (deflation) | Senior-credible (prefer) |
|---|---|
| Built the ranking model | Architected the ranking platform (4 models, 3 teams contributing) |
| Implemented the eval pipeline | Designed the eval framework (now standard across 4 ML teams) |
| Ran A/B tests | Owned the experimentation review board (12 launches in 2025) |
| Helped hire engineers | Led the ML hiring loop (designed interview rubric, 6 hires in 2024) |
| Used MLflow for tracking | Standardized the team on MLflow + ONNX registry (rollout: 6 months, 12 models migrated) |
| Worked with cross-functional teams | Drove the platform / product / data-eng working group (quarterly cross-team RFC review) |
| Mentored a junior | Mentored 4 mid-levels to senior; designed the ML-engineer promotion rubric in use since 2025 |
| Followed the team's eval process | Established the team's eval discipline (defined Ragas-golden-set protocol, mandated baseline comparisons) |

## Failure mode B — Over-abstraction

The over-claim failure: a senior ML engineer writes "led the AI strategy" or "transformed how the team thinks about ML." These are management-deck phrases. They tell the recruiter nothing about what the candidate actually did. A recruiter cannot probe "transformed how the team thinks" in an interview, so they discount the bullet.

**Required components in every senior ML bullet:**

1. **A specific named system OR a specific influence artifact** — not "the ML platform" but "the v2 ranking platform" or "the support-ticket triage stack" or "the multi-tenant model-serving layer". For influence bullets, the artifact is named instead: a specific RFC (the MLOps-standards RFC), a specific framework (the Ragas-golden-set eval protocol), a specific rubric (the ML-engineer promotion rubric), a specific runbook (the model-drift incident playbook).
2. **A consequential decision with named trade-offs OR a named adoption outcome** — for architecture bullets: build-vs-fine-tune-vs-API, prompt-vs-RAG-vs-fine-tune, model-size-vs-latency-vs-cost, dense-vs-sparse-vs-hybrid retrieval, full-FT-vs-LoRA-vs-QLoRA, in-house-eval-vs-third-party (Ragas, DeepEval, Helm). For influence bullets: a named adoption outcome (the rubric is in use across 4 hiring loops, the RFC was adopted Q2 2024, the protocol is now the team's default).
3. **A measurable result** — see `industry_norms/013_ml_metric_reporting` and `industry_norms/014_ml_reproducibility`. For architecture bullets the result is usually a model/system metric (latency, F1, cost); for influence bullets the result is usually an adoption metric (N teams adopted, M hires onboarded, P launches reviewed).

**Senior-architecture decision vocabulary (use these to make consequential decisions tangible):**

- **Build vs. fine-tune vs. API.** "Chose to fine-tune Llama-3-8B-Instruct after 6-week comparison vs. GPT-4-turbo API (cost: $11K/month vs. $84K/month projected at 800K req/day; quality: 0.91 vs. 0.93 macro-F1 on the internal golden set; retention: 12 internal team members trained on the FT stack)."
- **Prompt vs. RAG vs. fine-tune.** "After a 4-week eval, chose RAG over fine-tuning for the support-bot use case: retrieval grounding gave 0.91 Ragas faithfulness vs. fine-tuned-without-retrieval's 0.74, with no need to re-train on KB updates (weekly content delta)."
- **Model size vs. latency vs. cost.** "Chose distilled-Mistral-7B-int4 over Llama-3-70B-fp16 after the latency-vs-quality eval showed 0.86 macro-F1 at 80ms p95 (acceptable) vs. 0.91 at 720ms p95 (unacceptable for the real-time endpoint)."
- **Retrieval method.** "Chose hybrid retrieval (BM25 + BGE-M3 dense + Cohere Rerank) over dense-only after the comparison showed BM25 contributing 0.06 absolute hit-rate@10 on legal-clause exact-match queries that dense retrieval missed."
- **Eval framework.** "Adopted Ragas + an internal LLM-as-judge for end-to-end RAG eval; mandated 200-question golden-set construction per launch; rejected the 'vibes-check' eval approach formerly in use."

**BAD -> GOOD transformations (over-abstraction direction):**

- BAD (over-abstracted): "Led the AI strategy at the company."
- GOOD (senior, credible): "Led the 7-engineer ML platform team (2023-2025); chose RAG-over-direct-LLM-call for the customer-support stack after a 4-week eval; shipped to 12M users in Q3 2024; retired the legacy keyword-rule system across 14 surfaces over 6 months."

- BAD (over-abstracted): "Transformed how the team thinks about ML."
- GOOD (senior, credible): "Established the team's evaluation discipline: defined the Ragas golden-set construction protocol, mandated baseline-comparison in every model-launch review, ran the bi-weekly model-launch board; 4 of 5 v2 model launches in 2025 caught regressions pre-deploy under the new process."

- BAD (over-abstracted): "Drove organizational excellence in machine learning practices."
- GOOD (senior, credible): "Authored the team's MLOps standards RFC (adopted Q2 2024): mandates MLflow tracking, ONNX registry artifacts, and 2-week shadow before promotion; full compliance reached across 11 models by Q4."

- BAD (over-abstracted): "Owned the AI roadmap and direction."
- GOOD (senior, credible): "Chaired the ML steering group (3 teams, 14 engineers); authored the H1 2025 model-platform plan (consolidating 3 ad-hoc serving stacks into one Triton-based shared layer); migration completed Q3 2025, on-call burden fell from 4 incidents/week to 0.6."

**Architecture and influence bullets — every senior recent role needs at least one of each.**

- *Architecture bullet:* names the system, names the trade-off, names the chosen direction, names the outcome.
- *Influence bullet:* names the artifact (RFC, rubric, framework, runbook, mentorship cohort), names the adoption (cross-team, team-of-N, org-wide), names the durable result.

**Scale signals that reinforce senior credibility:**

- Multi-model platform scope (not one model — the platform that serves N models).
- Multi-team scope (your work shaped how 3+ teams operate, not just your team).
- Multi-quarter initiative scope (12+ month named projects with measurable outcomes).
- Hiring + mentorship at scale (N hires by name, M direct reports promoted).
- Cross-team artifacts in production use (RFC adopted, framework standardized, rubric adopted across hiring loops).

**Senior anti-patterns specific to ML:**

- Listing every framework / model / library worked with. Senior level is about judgment of *when* to choose what, not lists. Cut the Skills section to grouped categories with 3-5 anchors each.
- No hiring or mentorship bullet. At senior level, absence is read as either non-promotability or solo-IC without people influence.
- Older-than-12-year roles in full bullet detail. Compress to single lines (see generic senior chunk for the compression rule).
- Verb mix dominated by Built / Implemented (>30%). Senior verb mix should be Led / Designed / Owned / Drove / Decided / Mentored / Hired more than Built / Implemented (target: feature-build under 30%).

## Concrete rule for SmartCV

For senior+ ml_engineer resumes (7+ years), address two failure modes. (a) **Verb deflation:** under-claiming with junior verbs ("built X", "ran experiments", "helped with deployment") hides senior scope — surface Architected, Designed, Drove, Decided, Owned, Led, Established, Mentored, Hired. "Decided" requires the trade-off context — pair it with "after [named evaluation]" or use "Chose" with an inline trade-off clause. (b) **Over-abstraction:** "led the AI strategy", "transformed how the team thinks about ML", "drove organizational excellence" are management-deck claims, not resume claims. Every senior bullet must name a specific system OR a specific influence artifact (RFC, framework, rubric, runbook); a consequential decision with named trade-offs (build-vs-fine-tune-vs-API, prompt-vs-RAG-vs-fine-tune, model-size/cost trade-off, dense-vs-hybrid retrieval) OR a named adoption outcome; and a measurable result. Reject any vague-strategy bullet that names neither the system nor the decision.

---
sources:
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-16)
  - https://en.wikipedia.org/wiki/MLOps  (accessed 2026-05-16)
  - https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html  (Sculley et al., "Hidden Technical Debt in Machine Learning Systems", NeurIPS 2015, accessed 2026-05-16)
