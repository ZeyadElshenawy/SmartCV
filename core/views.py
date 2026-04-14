from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

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
