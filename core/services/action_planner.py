import logging
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse

logger = logging.getLogger(__name__)


def get_recommended_actions(user):
    """
    Inspect user state and return prioritized next-step actions.
    Pure Python rules engine — no LLM calls.

    Returns list of dicts:
        {priority, action_type, title, description, url, icon}
    sorted by priority (P0 first).
    """
    from profiles.models import UserProfile
    from jobs.models import Job
    from analysis.models import GapAnalysis
    from resumes.models import GeneratedResume, CoverLetter

    actions = []

    # ── Profile state ──────────────────────────────────────────────
    try:
        profile = UserProfile.objects.get(user=user)
        has_profile = bool(profile.full_name and profile.skills)
    except UserProfile.DoesNotExist:
        has_profile = False
        profile = None

    if not has_profile:
        actions.append({
            'priority': 0,
            'action_type': 'profile_setup',
            'title': 'Upload your CV to get started',
            'description': 'Your AI career agent needs your profile to tailor resumes and find skill gaps.',
            'url': reverse('upload_master_profile'),
            'icon': 'upload',
        })
        return actions  # nothing else matters until profile exists

    # ── Job state ──────────────────────────────────────────────────
    jobs = list(Job.objects.filter(user=user).order_by('-created_at'))

    if not jobs:
        actions.append({
            'priority': 0,
            'action_type': 'add_job',
            'title': 'Add your first target job',
            'description': 'Paste a LinkedIn URL or job description to start matching.',
            'url': reverse('job_input_view'),
            'icon': 'briefcase',
        })
        return actions

    # ── Per-job actions ────────────────────────────────────────────
    # Prefetch gap analyses and resumes to avoid N+1
    gap_map = {}
    for ga in GapAnalysis.objects.filter(user=user, job__in=jobs):
        gap_map[ga.job_id] = ga

    resume_job_ids = set(
        GeneratedResume.objects
        .filter(gap_analysis__user=user, gap_analysis__job__in=jobs)
        .values_list('gap_analysis__job_id', flat=True)
    )

    cover_letter_job_ids = set(
        CoverLetter.objects
        .filter(profile__user=user, job__in=jobs)
        .values_list('job_id', flat=True)
    )

    now = timezone.now()
    three_days_ago = now - timedelta(days=3)

    for job in jobs:
        gap = gap_map.get(job.id)

        # No gap analysis yet
        if not gap:
            actions.append({
                'priority': 1,
                'action_type': 'run_analysis',
                'title': f'See how you match for {job.title}',
                'description': f'Run AI gap analysis against {job.company or "this role"}.',
                'url': reverse('gap_analysis', args=[job.id]),
                'icon': 'chart',
            })
            continue

        match_pct = int(gap.similarity_score * 100)

        # Low match → learning path
        if match_pct < 50:
            actions.append({
                'priority': 1,
                'action_type': 'learning_path',
                'title': f'Build missing skills for {job.title}',
                'description': f'{match_pct}% match — get a personalized learning plan.',
                'url': reverse('learning_path', args=[job.id]),
                'icon': 'book',
            })

        # Medium match → chatbot
        elif match_pct < 80:
            actions.append({
                'priority': 1,
                'action_type': 'chatbot',
                'title': f'Improve your profile for {job.title}',
                'description': f'{match_pct}% match — a quick chat can boost it.',
                'url': reverse('profile_chatbot', args=[job.id]),
                'icon': 'chat',
            })

        # High match, no resume → generate
        if match_pct >= 50 and job.id not in resume_job_ids:
            actions.append({
                'priority': 1,
                'action_type': 'generate_resume',
                'title': f'Generate tailored resume for {job.title}',
                'description': f'{match_pct}% match — create an ATS-optimized resume.',
                'url': reverse('generate_resume', args=[job.id]),
                'icon': 'document',
            })

        # Resume exists, no cover letter
        if job.id in resume_job_ids and job.id not in cover_letter_job_ids:
            actions.append({
                'priority': 2,
                'action_type': 'cover_letter',
                'title': f'Create cover letter for {job.title}',
                'description': f'Complete your application package for {job.company or "this role"}.',
                'url': reverse('generate_cover_letter', args=[job.id]),
                'icon': 'mail',
            })

        # Saved for 3+ days → nudge to apply
        if job.application_status == 'saved' and job.created_at < three_days_ago:
            actions.append({
                'priority': 2,
                'action_type': 'apply_nudge',
                'title': f'Ready to apply for {job.title}?',
                'description': f'Saved {(now - job.created_at).days} days ago — don\'t miss the window.',
                'url': reverse('job_detail', args=[job.id]),
                'icon': 'clock',
            })

        # Offer stage → negotiate
        if job.application_status == 'offer':
            actions.append({
                'priority': 1,
                'action_type': 'negotiate',
                'title': f'Negotiate your {job.title} offer',
                'description': 'Generate a data-backed salary negotiation script.',
                'url': reverse('negotiate_salary', args=[job.id]),
                'icon': 'dollar',
            })

        # No outreach generated
        if job.application_status in ('saved', 'applied'):
            actions.append({
                'priority': 3,
                'action_type': 'outreach',
                'title': f'Reach out about {job.title}',
                'description': 'Generate LinkedIn message and cold email templates.',
                'url': reverse('generate_outreach', args=[job.id]),
                'icon': 'send',
            })

    # Sort by priority, then limit to top 5
    actions.sort(key=lambda a: a['priority'])
    return actions[:5]
