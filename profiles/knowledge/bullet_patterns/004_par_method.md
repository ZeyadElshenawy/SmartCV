---
id: bullet_patterns_004_par_method
type: bullet_pattern
title: PAR Method (Problem–Action–Result)
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: medium
last_updated: 2026-05-12
---

# PAR Method (Problem–Action–Result)

PAR is a near-twin of CAR, with "Problem" replacing "Challenge". The structural difference is connotative: Problem implies something was broken, Challenge implies an opportunity or hard task. Use Problem framing for bug-fix, incident-response, technical-debt, and security work; use Challenge framing (CAR) for net-new builds and process improvements.

**Template:**
`[Problem clause], <Action verb> <object>, <Result with metric>.`

**When to use PAR (vs. CAR or STAR):**
- The Problem is unambiguous and concrete (an outage, a vulnerability, a slow query, a broken metric).
- The Action is direct cause-and-effect, not exploratory.
- The Result is the absence of the Problem, ideally measured.

**Examples:**

- "After a Q3 incident root-caused to a missing nullable check in the payment processor, added schema-level constraints across all 14 financial-data tables; payment-data null-related bugs dropped from 7 in Q3 to 0 in Q4."
- "When the search service began returning empty results for 3% of queries due to an Elasticsearch index drift, wrote a daily index-checksum job that auto-rebuilds on mismatch; empty-result rate dropped to 0.02%."
- "When the OAuth refresh-token endpoint started returning 500s under load, traced to a connection-pool exhaustion bug, increased the pool size and added connection-timeout monitoring; error rate dropped from 4.2% to 0.05% during peak hours."
- "Audit revealed 6 production services without any test coverage; added pytest suites with 78%–94% line coverage across all 6 services; production-incident rate for those services dropped from 2.1/month to 0.4/month."

**PAR for incident-response.** PAR fits incident bullets where you can name the incident and post-fix metric:

- "Triaged the Mar-2025 cache-stampede outage (12 services down 47 min); coordinated rollback, then implemented request-coalescing that prevented recurrence in the following 14 months."

**Avoid blame language.** "Inherited a broken deploy script no one maintained for 18 months..." reads as attitude flag. Frame neutrally: "Inherited a deploy script with intermittent failures..."

**PAR for security.** Vulnerabilities are explicit Problems with explicit fixes:

- "Identified an unauthenticated GraphQL introspection endpoint exposing the schema; restricted to admin tokens and added schema-fuzz tests; caught and prevented in 3 subsequent PRs over 6 months."

## Concrete rule for SmartCV

Use the PAR template for incident-response, bug-fix, security, technical-debt, and audit-driven work. Name the specific Problem (not "issues with the system"). Frame neutrally — never blame previous engineers or hide your own contribution to the original Problem. Close with a Result that names the post-fix metric or the duration over which the Problem did not recur.

---
sources:
  - https://www.indeed.com/career-advice/resumes-cover-letters/star-method-resume  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/interviewing/how-to-use-the-star-interview-response-technique  (accessed 2026-05-12)
