
from .models import GeneratedResume
from jobs.models import Job
from profiles.models import UserProfile
from analysis.models import GapAnalysis
from .services.resume_generator import (
    calculate_ats_score,
    load_previous_best_for,
)
from .services.pipeline_dispatch import generate_resume_content_dispatched
import logging

logger = logging.getLogger(__name__)

def generate_resume_task(job_id, user_id):
    """
    Background task to generate a tailored resume without blocking the web request.
    Handles the slower LLM structured output parsing locally.
    """
    try:
        job = Job.objects.get(id=job_id, user_id=user_id)
        profile = UserProfile.objects.get(user_id=user_id)
        gap_analysis = GapAnalysis.objects.get(job=job)
        
        # Fix #1 — look up the most recent previous_best snapshot for
        # this (profile, job). Path A creates a NEW GeneratedResume row
        # each call, so the snapshot lives on the PRIOR row; load_…
        # walks rows for this gap_analysis and returns the latest. None
        # when no prior export exists.
        previous_best = load_previous_best_for(gap_analysis)
        resume_content = generate_resume_content_dispatched(
            profile, job, gap_analysis, previous_best=previous_best,
        )
        # Fix (b): pass the must/nice tiers so the score is tier-weighted.
        # Falsy/missing tiers → None → flat (pre-(b)) scoring (same read
        # pattern as pipeline_dispatch._generate_via_v2).
        job_tiers = getattr(job, "extracted_skills_tiers", None) or None
        ats_score = calculate_ats_score(resume_content, job.extracted_skills, job_tiers)

        resume = GeneratedResume.objects.create(
            gap_analysis=gap_analysis,
            content=resume_content,
            ats_score=ats_score,
            validation_report=resume_content.get('validation_report', {}),
        )
        
        return {
            'status': 'completed',
            'resume_id': str(resume.id)
        }
    except Exception as e:
        logger.exception("Failed to generate resume in background")
        return {
            'status': 'failed',
            'error': str(e)
        }
