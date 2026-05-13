---
id: industry_norms_007_data_science
type: industry_norm
title: Data Science — Resume Conventions
roles: [data_scientist]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Data Science — Resume Conventions

Data science clusters into three sub-types: analytics / business-impact, experimentation / causal-inference, ML-product. Wikipedia (2026): the field combines "statistics, computing, mathematics, and domain-specific knowledge"; recent emphasis on "data quality and curation" plus ethical/fairness rather than pure algorithms. Resumes should signal sub-type.

**Tech-stack categories:**

- **Languages:** Python primary, R secondary, SQL always, occasionally Scala / Julia.
- **Python ecosystem:** pandas, NumPy, scikit-learn, statsmodels, matplotlib, plotly, polars.
- **ML frameworks:** PyTorch, TensorFlow, JAX, XGBoost, LightGBM, Hugging Face.
- **Notebook:** Jupyter, JupyterLab, Colab, Databricks notebooks.
- **Experimentation:** Optimizely / LaunchDarkly / Statsig; causal libraries (DoWhy, EconML).
- **BI:** Tableau, PowerBI, Looker, Metabase, Streamlit, Dash.
- **Warehouse:** Snowflake, BigQuery, Redshift, Databricks SQL, DuckDB, Postgres.
- **Orchestration:** Airflow, Prefect, dbt, Dagster.

**Metrics:**
- **Model:** accuracy, precision/recall, F1, AUC, NDCG, RMSE, MAPE — with dataset and task named.
- **Experiments:** lift in primary metric, sample size, duration, p-value / CI.
- **Business:** dollars, conversion-pp lift, retention delta, churn reduction.
- **Dataset:** rows, features, entities covered.
- **Decisions:** A/B tests run, recommendations adopted.

**Strong bullet examples for data science:**

- "Shipped the day-1 churn-risk model (XGBoost on 18 months behavior data, 1.4M users, 84 features); AUC=0.79 on held-out; retention-team campaigns on top-decile risk lifted 7-day retention by +4.2 pp on a 12-week A/B (n=24K, p<0.01)."
- "Designed and analyzed 14 A/B tests in 2024 on search ranking; 9 launched, generating ~$1.2M annual incremental revenue."
- "Productionized the LTV model (LightGBM, served via FastAPI behind 50ms p95 SLA); marketing cut paid-acquisition spend on low-LTV cohorts by 23% with no revenue change."
- "Migrated 38 dbt models from manual Snowflake to dbt Cloud + GitHub Actions CI; nightly failure rate fell from 12% to under 2%; lineage now auto-generated in dbt Docs."
- "Authored the team's experimentation handbook (sample-size calc, sequential testing, multiple-comparisons correction); used by 6 PMs to scope 28 experiments in 2024."

**Data-science anti-patterns:**

- Listing models without application and metric. What was predicted, on what data, with what accuracy?
- Vague experiment claims. "Ran A/B tests to improve conversion" needs sample size, duration, significance.
- Claiming production ML without naming the serving stack. Notebook-only work isn't "deployed to production".
- Listing every Python library. pandas / NumPy are assumed; specialized libraries (Hugging Face, DoWhy) are differentiators.

**Domain matters.** Same model in different domains has very different implications. Surface the domain so reviewers can interpret impact correctly.

## Concrete rule for SmartCV

For data science roles, surface the stack across Languages, ML frameworks, Data warehouse / SQL, Visualization, and Workflow sub-groups. Every model bullet must name (a) the algorithm, (b) the dataset scope, (c) the model metric, AND (d) the business outcome or decision it influenced. Every experimentation bullet must include sample size, duration, and significance. Distinguish between "trained / prototyped" and "shipped to production" — never claim production deployment without naming the serving pattern.

---
sources:
  - https://en.wikipedia.org/wiki/Data_science  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/MLOps  (accessed 2026-05-12)
