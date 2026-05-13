---
id: industry_norms_008_data_engineering
type: industry_norm
title: Data Engineering — Resume Conventions
roles: [data_engineer]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Data Engineering — Resume Conventions

Data engineers focus on the production-readiness of data systems. The Wikipedia data-engineering article (2026) describes the role as building "big data ETL pipelines to manage the flow of data through the organization", spanning infrastructure, warehousing, security, modeling, processing, and metadata management — distinct from data scientists, who focus on extracting insights from already-clean data.

**Tech-stack categories:**

- **Languages:** Python primary, SQL (specify dialect — Postgres / Snowflake / BigQuery), Java or Scala for Spark / Flink.
- **Orchestration:** Airflow, Prefect, Dagster, Argo Workflows, Step Functions.
- **Processing:** Spark, Flink, Beam, Polars, Dask, dbt for SQL transforms.
- **Streaming:** Kafka, Pulsar, Kinesis, Pub/Sub, Confluent Cloud.
- **Warehouses:** Snowflake, BigQuery, Redshift, Databricks SQL.
- **Lakehouses:** Iceberg, Hudi, Delta Lake on S3 / GCS / ADLS.
- **Quality:** Great Expectations, dbt tests, Monte Carlo, Soda, Datafold.
- **Catalog:** DataHub, Unity Catalog, Glue Data Catalog, Amundsen.

**Metrics:**
- **Scale:** rows/day, peak throughput (events/sec), volume (GB/TB).
- **Reliability:** SLA hit rate, job failure %, MTTR, freshness lag.
- **Cost:** monthly warehouse spend, $/TB, query-cost reduction.
- **Performance:** runtime before/after, query p95.
- **Quality:** test coverage, anomaly-detection precision/recall, downstream incident rate.

**Strong bullet examples for data engineering:**

- "Migrated 38 nightly Airflow DAGs from Hive Spark to dbt + Snowflake; batch runtime dropped from 6.4h to 1.1h; warehouse spend fell 28% ($14K/month)."
- "Built the realtime event pipeline (Kafka → Flink → Iceberg on S3) processing 4.2B events/day at 18K events/sec peak; freshness lag dropped from 22 min batch to under 90s end-to-end."
- "Owned the data-quality framework: 480 Great Expectations tests across 62 tables wired into dbt CI; downstream incidents from upstream data issues fell from 11/quarter to 2/quarter."
- "Designed the dimensional model (star schema, 4 fact + 18 dim tables) for the revenue-attribution warehouse; supports 3 BI tools with consistent dbt-metrics-layer definitions."
- "Cut the 6 most-expensive Snowflake queries by 64% over Q3 via partitioning on (event_date, tenant_id) + clustering keys + a materialized view; dashboard p95 fell from 4.2s to 0.8s."

**Data-engineering anti-patterns:**

- Listing every tool. Pick the 6–8 you've shipped production work on.
- Bullets describing pipelines without throughput, latency, or cost.
- Conflating data engineering and analytics. "Built Tableau dashboards" is analytics; "Built the dbt models powering the dashboards" is data engineering.
- Claiming Spark expertise without cluster scale or job-tuning specifics.

**Modern (2024–2026) trends:**
- Lakehouse (Iceberg, Hudi, Delta) replacing warehouse-only.
- dbt as standard SQL-transform layer.
- Streaming-first ELT replacing batch ETL.
- Data contracts; shift-left quality.
- FinOps for data (query-cost monitoring).

## Concrete rule for SmartCV

For data engineering roles, surface the stack across Languages, Orchestration, Processing, Streaming, Warehouse, Lakehouse, and Quality sub-groups. Every pipeline bullet must include throughput (events/sec or rows/day) AND either runtime, freshness lag, or cost. Distinguish between batch and streaming explicitly. Cap tool enumeration at 8 in-depth tools, not the entire modern data stack.

---
sources:
  - https://en.wikipedia.org/wiki/Data_engineering  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/MLOps  (accessed 2026-05-12)
