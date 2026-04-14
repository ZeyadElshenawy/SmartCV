from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from .services.agent_chat import chat

def home_view(request):
    """Landing page - redirect to appropriate dashboard"""
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'core/home.html')

@login_required
def dashboard_view(request):
    """Legacy dashboard route — redirects to profiles dashboard."""
    return redirect('dashboard')

def custom_404(request, exception):
    """Custom 404 error page"""
    return render(request, '404.html', status=404)

def custom_500(request):
    """Custom 500 error page"""
    return render(request, '500.html', status=500)


def design_system_view(request):
    """Internal styleguide — renders every component primitive under
    templates/components/ in every tone/size so visual regressions show
    up at a glance. Route: /design/"""
    return render(request, 'core/design_system.html')


@login_required
def agent_chat_view(request):
    """General agent chat — not tied to a specific job by default.

    When reached via ``/agent/?job=<id>``, the agent is scoped to that job
    and receives a rich dossier (gap analysis, snapshot, artifacts) in the
    system prompt. Foreign or malformed job ids redirect back to the
    general chat with a user-facing warning.
    """
    import uuid as _uuid
    from django.contrib import messages
    from jobs.models import Job

    job = None
    raw = request.GET.get('job')
    if raw:
        try:
            _uuid.UUID(str(raw))
        except (ValueError, TypeError):
            messages.warning(request, "That job couldn't be found.")
            return redirect('agent_chat')
        job = Job.objects.filter(id=raw, user=request.user).first()
        if job is None:
            messages.warning(request, "That job couldn't be found.")
            return redirect('agent_chat')

    return render(request, 'core/agent_chat.html', {
        'job': job,
        'job_id': str(job.id) if job else None,
    })


@login_required
def agent_chat_api(request):
    """POST API used by the agent-chat page.

    Body: JSON { history: [{role, content}, ...], message: "...", job_id?: "<uuid>" }
    Returns { reply } on success, { error } on failure.

    When ``job_id`` is present, it must belong to the authenticated user
    (otherwise 403) and the matching Job is forwarded to the chat service
    so the agent's system prompt includes the job's dossier.
    """
    if request.method != 'POST':
        from django.http import JsonResponse
        return JsonResponse({'error': 'POST only'}, status=405)

    import json
    import uuid as _uuid
    from django.http import JsonResponse
    from jobs.models import Job

    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    history = payload.get('history') or []
    if not isinstance(history, list):
        history = []
    message = (payload.get('message') or '').strip()
    if not message:
        return JsonResponse({'error': 'Empty message.'}, status=400)

    job = None
    raw_job_id = payload.get('job_id')
    if raw_job_id:
        try:
            _uuid.UUID(str(raw_job_id))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Job not found.'}, status=403)
        job = Job.objects.filter(id=raw_job_id, user=request.user).first()
        if job is None:
            return JsonResponse({'error': 'Job not found.'}, status=403)

    result = chat(request.user, history, message, job=job)
    if result.get('error'):
        return JsonResponse({'error': result['error']}, status=502)
    return JsonResponse({'reply': result['reply']})


@login_required
def welcome_view(request):
    """First-run orientation screen shown to brand-new signups.

    Presents three ways into the product (upload CV / build by form / just
    tour the dashboard). Records has_seen_welcome on the profile so the
    page short-circuits to the dashboard on repeat visits — prevents users
    from getting stuck on a "welcome" screen they've already seen.

    Users who explicitly click "Just show me around" also get the flag set
    so the agent's stage-aware hero takes over from here on.
    """
    from profiles.models import UserProfile
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    data = profile.data_content or {}

    # POST: user clicked "skip to dashboard" — mark seen and go.
    if request.method == 'POST' and request.POST.get('action') == 'skip':
        data['has_seen_welcome'] = True
        profile.data_content = data
        profile.save(update_fields=['data_content', 'updated_at'])
        return redirect('dashboard')

    # Repeat visit — short-circuit.
    if data.get('has_seen_welcome'):
        return redirect('dashboard')

    # First visit — mark seen on the way in (whichever route they pick next,
    # they won't see /welcome/ again). Profiles that already have content
    # (e.g., user manually typed /welcome/) bypass the screen entirely.
    if profile.full_name or profile.data_content.get('skills'):
        data['has_seen_welcome'] = True
        profile.data_content = data
        profile.save(update_fields=['data_content', 'updated_at'])
        return redirect('dashboard')

    data['has_seen_welcome'] = True
    profile.data_content = data
    profile.save(update_fields=['data_content', 'updated_at'])
    return render(request, 'core/welcome.html', {'user_email': request.user.email})


@login_required
def applications_view(request):
    """Full-screen kanban board — pulled out of the dashboard so the
    pipeline gets its own nav entry and first-class real estate."""
    from jobs.models import Job
    jobs = list(Job.objects.filter(user=request.user).order_by('-created_at'))
    kanban_boards = {
        'Saved':        [j for j in jobs if j.application_status == 'saved'],
        'Applied':      [j for j in jobs if j.application_status == 'applied'],
        'Interviewing': [j for j in jobs if j.application_status == 'interviewing'],
        'Offer':        [j for j in jobs if j.application_status == 'offer'],
        'Rejected':     [j for j in jobs if j.application_status == 'rejected'],
    }
    return render(request, 'core/applications.html', {
        'kanban_boards': kanban_boards,
        'total_applications': len(jobs),
    })


@login_required
def insights_view(request):
    """Career insights hub — external signal tiles, top skills across
    applications, evidence confidence, and links to learning paths /
    scoring tools. Positions the agent as more than a CV maker."""
    from profiles.models import UserProfile
    from jobs.models import Job
    from resumes.services.scoring import compute_evidence_confidence
    from analysis.models import GapAnalysis
    from resumes.models import GeneratedResume

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    jobs = list(Job.objects.filter(user=request.user).order_by('-created_at'))

    # Top skills across applications
    skill_counts = {}
    for j in jobs:
        for s in (j.extracted_skills or []):
            skill_counts[s] = skill_counts.get(s, 0) + 1
    top_skills = sorted(skill_counts.items(), key=lambda x: -x[1])[:10]

    # Recent gap analyses
    recent_gaps = list(GapAnalysis.objects.filter(
        user=request.user
    ).select_related('job').order_by('-created_at')[:5])

    # Recent tailored résumés
    recent_resumes = list(GeneratedResume.objects.filter(
        gap_analysis__user=request.user
    ).select_related('gap_analysis__job').order_by('-created_at')[:5])

    evidence = compute_evidence_confidence(profile)

    return render(request, 'core/insights.html', {
        'profile': profile,
        'top_skills': top_skills,
        'recent_gaps': recent_gaps,
        'recent_resumes': recent_resumes,
        'evidence': evidence,
    })
