import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from jobs.models import Job
from profiles.models import UserProfile
from django.db import transaction
from .models import GapAnalysis
from .services.gap_analyzer import compute_gap_analysis
from .services.learning_path_generator import generate_learning_path
from .services.salary_negotiator import generate_negotiation_script

@login_required
@transaction.atomic
def gap_analysis_view(request, job_id):
    """Compute and display gap analysis - 'The Hook'"""
    job = get_object_or_404(Job, id=job_id, user=request.user)

    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return redirect('upload_master_profile')

    # Try to load cached analysis first to avoid redundant LLM calls on every visit
    force_recompute = request.GET.get('refresh') == '1'
    existing = GapAnalysis.objects.filter(job=job, user=request.user).first()

    if existing and not force_recompute:
        # Use cached result — no LLM call needed
        gap_analysis = existing
        analysis_results = {
            'matched_skills': existing.matched_skills,
            'missing_skills': existing.missing_skills,
            'partial_skills': existing.partial_skills,
            'similarity_score': existing.similarity_score,
            'critical_missing_skills': existing.missing_skills[:5],
            'soft_skill_gaps': existing.partial_skills,  # drag-drop saves soft gaps to partial_skills
        }
        
        # Prepare "The Hook" context
        match_percentage = int(gap_analysis.similarity_score * 100)
    
        # Identify Red Flags (Critical Missing Skills)
        red_flags = analysis_results.get('critical_missing_skills', gap_analysis.missing_skills[:5])
    
        # Identify Soft Gaps (from LLM analysis if available)
        soft_gaps = analysis_results.get('soft_skill_gaps', [])
    
        # Score-based primary action routing
        if match_percentage > 80:
            primary_action = 'generate_resume'
        elif match_percentage >= 50:
            primary_action = 'chat_fill_gaps'
        else:
            primary_action = 'learning_path'

        # Compute frontend layout percentages
        score = gap_analysis.similarity_score
        circumference = 364.4
        
        # Pydantic schema mappings
        critical_missing_list = analysis_results.get('critical_missing_skills', getattr(gap_analysis, 'missing_skills', []))
        soft_skill_list = analysis_results.get('soft_skill_gaps', getattr(gap_analysis, 'soft_skill_gaps', []))

        # Normalize skills to plain strings for JSON serialization
        def to_str_list(lst):
            return [s['name'] if isinstance(s, dict) and 'name' in s else str(s) for s in lst]

        matched_str = to_str_list(gap_analysis.matched_skills)
        missing_str = to_str_list(gap_analysis.missing_skills)
        soft_str = to_str_list(soft_skill_list)

        total_required = max(len(job.extracted_skills), 1)
        
        context = {
            'job': job,
            'profile': profile,
            'gap': gap_analysis,
            'match_percentage': match_percentage,
            'red_flags': red_flags,
            'soft_gaps': soft_gaps,
            'primary_action': primary_action,
            'can_refresh': True,
            'is_computing': False,
            
            # New Gauge Layout Logic
            'gauge_fill': round(score * circumference, 1),
            'gauge_color': "#639922" if score >= 0.8 else "#BA7517" if score >= 0.5 else "#E24B4A",
            'matched_pct': round(len(gap_analysis.matched_skills) / total_required * 100),
            'missing_pct': round(len(critical_missing_list) / total_required * 100),
            'soft_pct': round(len(soft_skill_list) / total_required * 100),

            # JSON data for drag-and-drop Alpine component
            'matched_skills_json': json.dumps(matched_str),
            'missing_skills_json': json.dumps(missing_str),
            'soft_skills_json': json.dumps(soft_str),
        }
    
        return render(request, 'analysis/gap_analysis.html', context)
    else:
        # Validate preconditions up front so users aren't stuck in a
        # "spin → error → retry" loop when their profile or job is empty.
        if not job.extracted_skills:
            from django.contrib import messages
            messages.error(
                request,
                "We couldn't extract any technical skills from this job description. "
                "Edit the description to include specific requirements, then retry."
            )
            return redirect('review_extracted_job', job_id=job.id)

        if not profile.skills:
            from django.contrib import messages
            messages.warning(
                request,
                "Add some skills to your profile (upload a CV or fill it in manually) "
                "before running gap analysis."
            )
            return redirect('review_master_profile')

        context = {
            'job': job,
            'profile': profile,
            'is_computing': True,
        }
        return render(request, 'analysis/gap_analysis.html', context)

@login_required
def compute_gap_api(request, job_id):
    """API endpoint to trigger the gap analysis synchronously."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    job = get_object_or_404(Job, id=job_id, user=request.user)

    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return JsonResponse({
            'error': 'Please upload your CV first before running gap analysis.',
            'action': 'upload_profile',
        }, status=400)

    # Validate preconditions — the analysis is meaningless without inputs.
    if not job.extracted_skills:
        return JsonResponse({
            'error': "We couldn't extract any skills from this job description. Try editing the description to include technical requirements.",
            'action': 'edit_job',
        }, status=400)

    if not profile.skills:
        return JsonResponse({
            'error': 'Your profile has no skills yet. Upload your CV or add skills manually, then run gap analysis.',
            'action': 'upload_profile',
        }, status=400)

    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        from .tasks import compute_gap_analysis_task
        compute_gap_analysis_task(job.id, request.user.id)
        return JsonResponse({'success': True, 'message': 'Analysis complete'})
    except Exception as e:
        _log.exception(f"Gap analysis failed for job {job_id}: {e}")
        return JsonResponse({
            'error': 'Gap analysis failed. This can happen if the AI is temporarily unavailable — please try again in a moment.',
            'retryable': True,
        }, status=500)

@login_required
@require_POST
def update_gap_skills(request, job_id):
    """API endpoint to persist user skill reclassifications from drag-and-drop."""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    gap = GapAnalysis.objects.filter(job=job, user=request.user).first()
    if not gap:
        return JsonResponse({'error': 'No gap analysis found'}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    matched = data.get('matched_skills')
    missing = data.get('missing_skills')
    soft = data.get('soft_skill_gaps')

    if matched is None or missing is None or soft is None:
        return JsonResponse({'error': 'All three skill lists are required'}, status=400)

    gap.matched_skills = matched
    gap.missing_skills = missing
    # soft_skill_gaps not stored on model — we fold them into partial_skills for persistence
    gap.partial_skills = soft
    gap.save(update_fields=['matched_skills', 'missing_skills', 'partial_skills'])

    return JsonResponse({
        'success': True,
        'matched_count': len(matched),
        'missing_count': len(missing),
        'soft_count': len(soft),
    })

@login_required
def check_gap_status_api(request, job_id):
    """Legacy polling endpoint — now always returns completed because analysis is sync."""
    return JsonResponse({'status': 'completed'})

@login_required
def generate_learning_path_view(request, job_id=None):
    """Generate a personalized learning path based on missing skills across jobs or a specific job"""

    if job_id:
        gap_analyses = GapAnalysis.objects.filter(
            job__id=job_id, job__user=request.user
        ).only('missing_skills')
        context_job = get_object_or_404(Job, id=job_id, user=request.user)
    else:
        # Fetch all gap analyses for this user's jobs
        gap_analyses = GapAnalysis.objects.filter(
            job__user=request.user
        ).only('missing_skills')
        context_job = None

    missing_skills_pool = {}
    for gap in gap_analyses:
        for skill in gap.missing_skills:
            normalized = skill.lower().strip()
            missing_skills_pool[normalized] = missing_skills_pool.get(normalized, 0) + 1

    # Get top 5 most frequently missing skills
    top_missing = sorted(missing_skills_pool.items(), key=lambda x: x[1], reverse=True)[:5]
    skills_to_learn = [skill for skill, count in top_missing]

    learning_path = []
    if request.method == 'POST':
        # Generate the learning path using LLM
        learning_path = generate_learning_path(skills_to_learn)

    return render(request, 'analysis/learning_path.html', {
        'skills_to_learn': top_missing,
        'learning_path': learning_path,
        'context_job': context_job
    })

@login_required
def negotiate_salary_view(request, job_id):
    """Generate an AI-powered salary negotiation script"""
    
    job = get_object_or_404(Job, id=job_id, user=request.user)
    profile = get_object_or_404(UserProfile, user=request.user)
    
    script = None
    if request.method == 'POST':
        current_offer = request.POST.get('current_offer')
        target_salary = request.POST.get('target_salary')
        
        if current_offer and target_salary:
            script = generate_negotiation_script(profile, job, current_offer, target_salary)
            
    return render(request, 'analysis/salary_negotiator.html', {
        'job': job,
        'profile': profile,
        'script': script
    })
