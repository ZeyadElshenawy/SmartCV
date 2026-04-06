
from .models import GeneratedResume
from jobs.models import Job
from profiles.models import UserProfile
from analysis.models import GapAnalysis
from .services.resume_generator import generate_resume_content, calculate_ats_score
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
        
        resume_content = generate_resume_content(profile, job, gap_analysis)
        ats_score = calculate_ats_score(resume_content, job.extracted_skills)
        
        resume = GeneratedResume.objects.create(
            gap_analysis=gap_analysis,
            content=resume_content,
            ats_score=ats_score
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
