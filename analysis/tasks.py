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
    Background task to compute full gap analysis via LLM,
    saving the result to the GapAnalysis model asynchronously.
    """
    try:
        from analysis.services.gap_analyzer import compute_gap_analysis
        from analysis.models import GapAnalysis
        
        job = Job.objects.get(id=job_id, user_id=user_id)
        profile = UserProfile.objects.get(user_id=user_id)
        
        analysis_results = compute_gap_analysis(profile, job)
        
        GapAnalysis.objects.update_or_create(
            job=job,
            user_id=user_id,
            defaults={
                'matched_skills': analysis_results['matched_skills'],
                'missing_skills': analysis_results['missing_skills'],
                'partial_skills': analysis_results['partial_skills'],
                'similarity_score': analysis_results['similarity_score']
            }
        )
        logger.info(f"Successfully computed gap analysis for job {job_id}")
    except Exception as e:
        logger.error(f"Failed to compute gap analysis in background for job {job_id}: {e}")
