# SmartCV Gap Analyzer Fixes — Implementation Plan

## Phase 1 — Fix Similarity Score Formula
- **Problem:** `similarity_score = 1.0 - distance` is mathematically inaccurate because PGVector's `CosineDistance` returns between `0.0` and `2.0`. This produces negative similarity scores for low matches.
- **Files Touched:** `analysis/services/gap_analyzer.py`
- **Fix:** Replace the similarity calculation with `1.0 - (distance / 2.0)` and add a clamp `max(0.0, min(1.0, score))`. Apply the equivalent structural logic to the numpy fallback.
- **Test Criteria:**
  - Write a Django shell snippet that creates two dummy vectors, computes `CosineDistance`, and asserts the returned score is mathematically restricted between 0.0 and 1.0.
  - Manually verify a near-identical vector pair returns close to 1.0 and an inverse/opposite pair returns close to 0.0.
  - Only proceed to Phase 2 after confirming output is sane.

## Phase 2 — Fix GapAnalysis Model Cardinality
- **Problem:** `GapAnalysis.job` is a `OneToOneField`. This means the whole platform shares exactly one analysis result per job, and the second user analyzing the same job overwrites the first user.
- **Files Touched:** `analysis/models.py`
- **Fix:** Change `job` to a `ForeignKey` (on_delete=CASCADE). Add a `user` ForeignKey to `settings.AUTH_USER_MODEL`. Add `unique_together = ("job", "user")` in the model `Meta`. Generate and apply the migration.
- **Test Criteria:**
  - In the Django shell, simulate two different users running gap analysis on the same job and confirm two separate rows exist.
  - Run `python manage.py check` and confirm zero errors.
  - Only proceed to Phase 3 after both pass.

## Phase 3 — Fix Embedding Dimension Mismatch
- **Problem:** The embedding columns may have occasionally been bootstrapped as 1536 (OpenAI length), but the local runner (`sentence-transformers/all-MiniLM-L6-v2`) produces 384 dimensions. 
- **Files Touched:** `profiles/migrations/*`, `profiles/models.py`. 
- **Fix:** Validate the field dimensions. If legacy migrations are wrong, generate a new migration altering ALL vector columns in `profiles.models` and `jobs.models` to explicitly enforce `dimensions=384` to prevent crashes. 
- **Test Criteria:**
  - In Django shell, generate a real embedding using `all-MiniLM-L6-v2`. Attempt to assign and save it to all vector fields (`embedding`, `embedding_skills`, `embedding_experience`, `embedding_education`).
  - Confirm the DB driver accepts it without dimension mismatch errors.
  - Only proceed to Phase 4 after the shell insert succeeds cleanly.

## Phase 4 — Fix Pydantic Schema / Django Model Field Name Mismatch
- **Problem:** `GapAnalysisResult` Pydantic schema utilizes the field `critical_missing` but the Django model expects `missing_skills`. This inconsistency breaks mapping layers and creates silent loss of missing skills unless hacked dynamically.
- **Files Touched:** `profiles/services/schemas.py`, `analysis/services/gap_analyzer.py`, `analysis/models.py`, `analysis/views.py`.
- **Fix:** Rename the Pydantic field to `critical_missing_skills` for perfect unambiguous clarity. Audit every view and serializer location to rely globally on `critical_missing_skills` and map evenly to the DB.
- **Test Criteria:**
  - Trigger a real LLM structured output call and confirm the returned Pydantic object maps cleanly to the Django model update call.
  - Verify that the saved Django object populated `missing_skills` (or `critical_missing_skills`) perfectly.
  - Only proceed to Phase 5 after confirmed.

## Phase 5 — Fix Job Embedding Never Being Invalidated
- **Problem:** While `_bust_profile_embeddings` effectively clears stale vector caches on the user profile, `job.embedding` is never cleared when a job's description is organically altered.
- **Files Touched:** `jobs/views.py` (and forms/services handling job generation).
- **Fix:** Create a `_bust_job_embedding(job)` helper that assigns `job.embedding = None` (and other vector fields if applicable). Wrap this helper around any logic segment modifying a job description. 
- **Test Criteria:**
  - In Django shell or view simulation, patch a live job's description. Check if fetching that job natively yields `.embedding == None`.
  - Only proceed to Phase 6 after confirmed.

## Phase 6 — Deploy Django-Q2 for Background Embedding Pre-computation
- **Problem:** Generating deep-learning vectors synchronously blocks the HTTP request for 10-20 seconds. Standard reverse proxies (like Nginx) will drop the request entirely with a `504 Gateway Timeout`.
- **Files Touched:** `SmartCV/settings.py`, `analysis/tasks.py` (New), `profiles/views.py`, `jobs/views.py`.
- **Fix:**
  - Install `django-q2` and add `django_q` to `INSTALLED_APPS`.
  - Add the `Q_CLUSTER` definition in settings.
  - Move the slow embedding generation logic into async hooks inside `analysis.tasks.py` (`generate_profile_embeddings` and `generate_job_embeddings`).
  - Trigger `async_task()` directly after busting the embeddings in the views.
  - Run `python manage.py migrate` to provision Q2 tables.
- **Test Criteria:**
  - Start the distinct `python manage.py qcluster` terminal worker. 
  - Save a user profile. Observe the task entering the Q-Cluster in the background worker output logger.
  - Ensure the web endpoint instantly returns an HTTP 200/302, bypassing the 10s wait. Observe DB vector fields populate moments later.
  - Only proceed to Phase 7 after all confirmed.

## Phase 7 — Update Documentation
- **Problem:** Current docs lag behind structural implementations.
- **Files Touched:** `docs/gap_analysis_system.md`.
- **Fix:** 
  - Update Section 5 with the `1.0 - (D / 2.0)` formula logic.
  - Update Section 7 with updated matching parameters (cutoff 0.80). 
  - Overhaul dimensions logic if applicable in Section 11.
  - Add **Section 12: Background Task Architecture**, explicitly noting Q-cluster management for production deployments.
- **Test Criteria:** Read the final document and confirm all sections are accurate. 

## User Review Required
> [!IMPORTANT]
> - Please review the breakdown of the 7 phases above.
> - Respond with "approved" to allow me to write code and execute Phase 1.
