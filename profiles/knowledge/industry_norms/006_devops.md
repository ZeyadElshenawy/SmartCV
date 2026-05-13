---
id: industry_norms_006_devops
type: industry_norm
title: DevOps and Site Reliability Engineering — Resume Conventions
roles: [devops]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# DevOps and Site Reliability Engineering — Resume Conventions

DevOps and SRE roles overlap heavily. Wikipedia's DevOps article (2026) emphasizes principles ("shared ownership, workflow automation, rapid feedback") and the DORA metrics (Deployment Frequency, Lead Time for Changes, Change Failure Rate, Failed Deployment Recovery Time). The Wikipedia SRE article describes SREs as responsible for "availability, latency, performance, efficiency, change management, monitoring, emergency response, and capacity planning", with reliability tracked via SLIs, SLOs, and error budgets.

**Tech-stack categories:**

- **Cloud:** AWS, GCP, Azure, DigitalOcean, Cloudflare, Fly.io. Name specific services.
- **IaC:** Terraform, Pulumi, AWS CDK, CloudFormation, Ansible, Chef, Puppet.
- **Containers:** Docker, Kubernetes (EKS / GKE / AKS), Nomad, ECS, Cloud Run, Fargate.
- **CI/CD:** GitHub Actions, GitLab CI, CircleCI, Jenkins, ArgoCD, Flux, Spinnaker.
- **Observability:** Prometheus, Grafana, Datadog, New Relic, Honeycomb, OpenTelemetry, Jaeger, Loki, ELK.
- **Secrets:** HashiCorp Vault, AWS Secrets Manager, SOPS, External Secrets Operator.
- **Networking:** VPC design, load balancers, service mesh (Istio, Linkerd, Consul), CDN, DNS, cert-manager.

**Metrics:**
- **DORA four keys:** Deployment Frequency, Lead Time for Changes, Change Failure Rate, Failed Deployment Recovery Time / MTTR.
- **SLI / SLO / Error budget:** SLO targets owned, error-budget burn rate.
- **Incident:** MTTR, MTBF, incident count per quarter, action-item completion rate.
- **Cost:** monthly infra spend, cost per request, instance utilization %.
- **Pipeline:** build duration, success rate, time-to-revert.

**Strong bullet examples for DevOps / SRE:**

- "Migrated 14 services from self-hosted K8s to EKS with Karpenter; deploy frequency rose from 8/week to 42/week; Change Failure Rate dropped from 11% to 2.3% over 6 months."
- "Owned the observability stack (Prometheus + Grafana + Loki + OpenTelemetry across 38 services); MTTR for 6 top-traffic services fell from 47 min to 12 min after standardizing golden-signal dashboards."
- "Cut monthly AWS spend from $84K to $52K over Q3 by right-sizing 47 EC2 instances, moving cold storage to S3 Glacier, and committing to 1-year reservations."
- "Built the GitHub Actions reusable-workflow library used by 22 repos (lint, test, Trivy + SAST, progressive-canary); CI flake rate fell from 9% to 1.2% across the org."
- "Implemented 99.95% availability SLO with 14-day error budget for checkout API; PagerDuty wakes on-call only when 50% budget would burn in 6h; noise alerts fell from ~15/week to under 2/week."

**DevOps anti-patterns:**

- Listing every cloud product flat. Pick 6–8 services with real depth.
- "Improved CI/CD pipeline" with no metric — use build time, success rate, deploy frequency.
- Claiming SRE without naming specific SLOs or the error-budget framework.
- "Deployed Kubernetes" — what was the cluster scale, the migration source, the operational outcome?

## Concrete rule for SmartCV

For DevOps / SRE roles, surface the stack across Cloud, IaC, Containers, CI/CD, Monitoring, Secrets, Networking sub-groups. Quantify bullets using DORA metrics (deploy frequency, lead time, change failure rate, MTTR) and SLI/SLO/error-budget framing. Always include at least one cost-optimization bullet (with dollar or percentage figures) for mid+ candidates. Cap cloud-service enumeration at 6–8 specific services in depth, not the full vendor catalog.

---
sources:
  - https://en.wikipedia.org/wiki/DevOps  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Site_reliability_engineering  (accessed 2026-05-12)
