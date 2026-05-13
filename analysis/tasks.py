import logging
from profiles.models import UserProfile
from jobs.models import Job

logger = logging.getLogger(__name__)


def generate_profile_embeddings(profile_id):
    """
    No-op stub. Embeddings have been removed in favour of pure-LLM analysis.
    Kept as a function so any queued tasks don't crash.
    """
    logger.info("generate_profile_embeddings called for %s — skipped (embeddings removed)", profile_id)


def generate_job_embeddings(job_id):
    """
    No-op stub. Embeddings have been removed in favour of pure-LLM analysis.
    Kept as a function so any queued tasks don't crash.
    """
    logger.info("generate_job_embeddings called for %s — skipped (embeddings removed)", job_id)


def compute_gap_analysis_task(job_id, user_id):
    """
    Synchronously compute gap analysis via LLM and persist to GapAnalysis.
    Raises on failure so callers can surface errors to the user.

    Writes both the new tier-aware fields and the legacy flat lists so older
    consumers keep working without change.
    """
    from analysis.services.gap_analyzer import compute_gap_analysis
    from analysis.models import GapAnalysis

    job = Job.objects.get(id=job_id, user_id=user_id)
    profile = UserProfile.objects.get(user_id=user_id)

    analysis_results = compute_gap_analysis(profile, job)

    GapAnalysis.objects.update_or_create(
        job=job,
        user_id=user_id,
        defaults={
            # Legacy flat (back-compat)
            'matched_skills':  analysis_results['matched_skills'],
            'missing_skills':  analysis_results['missing_skills'],
            'partial_skills':  analysis_results['partial_skills'],
            'similarity_score': analysis_results['similarity_score'],
            # Tier-aware v2
            'matched_must_have':    analysis_results.get('matched_must_have', []),
            'matched_nice_to_have': analysis_results.get('matched_nice_to_have', []),
            'missing_must_have':    analysis_results.get('missing_must_have', []),
            'missing_nice_to_have': analysis_results.get('missing_nice_to_have', []),
            'match_band':           analysis_results.get('match_band', ''),
            'avg_proximity':        analysis_results.get('avg_proximity'),
        }
    )
    logger.info(
        "Gap analysis persisted for job %s: score=%s band=%s avg_prox=%s "
        "matched_must=%d matched_nice=%d missing_must=%d missing_nice=%d",
        job_id,
        analysis_results.get('similarity_score'),
        analysis_results.get('match_band'),
        analysis_results.get('avg_proximity'),
        len(analysis_results.get('matched_must_have') or []),
        len(analysis_results.get('matched_nice_to_have') or []),
        len(analysis_results.get('missing_must_have') or []),
        len(analysis_results.get('missing_nice_to_have') or []),
    )
    return analysis_results
