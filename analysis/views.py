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
from resumes.services.scoring import compute_evidence_confidence


def _compute_evidence_safe(profile):
    """Defensive wrapper — never let evidence-confidence break gap analysis."""
    try:
        return compute_evidence_confidence(profile)
    except Exception:
        return {'score': 0, 'label': 'Untested', 'sources': [],
                'detail': 'No external signals available.'}

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

        # Pull the tier-aware lists. For legacy rows that have only the flat
        # lists populated, synthesize tier objects from the flat data so the
        # template still renders. Legacy rows show no proximity bars and no
        # ★ on matched chips — that's the documented graceful fallback.
        matched_must = list(gap_analysis.matched_must_have or [])
        matched_nice = list(gap_analysis.matched_nice_to_have or [])
        missing_must = list(gap_analysis.missing_must_have or [])
        missing_nice = list(gap_analysis.missing_nice_to_have or [])

        if not (matched_must or matched_nice or missing_must or missing_nice):
            # Legacy row — all tier fields empty. Synthesize from flat lists.
            matched_must = [
                {'name': s, 'evidence_source': 'skills', 'evidence_quote': '',
                 'tier': 'must', '_legacy': True}
                for s in gap_analysis.matched_skills or []
            ]
            missing_must = [
                {'name': s, 'source_quote': '', 'proximity': None,
                 'proximity_reason': '', 'bridge_hint': None,
                 'tier': 'must', '_legacy': True}
                for s in gap_analysis.missing_skills or []
            ]
            missing_nice = [
                {'name': s, 'source_quote': '', 'proximity': None,
                 'proximity_reason': '', 'bridge_hint': None,
                 'tier': 'nice', '_legacy': True}
                for s in gap_analysis.partial_skills or []
            ]
        else:
            # Tag each entry with its tier and sort missing by proximity desc
            # so the "almost there" chips appear first.
            for m in matched_must: m['tier'] = 'must'
            for m in matched_nice: m['tier'] = 'nice'
            for m in missing_must: m['tier'] = 'must'
            for m in missing_nice: m['tier'] = 'nice'
            def _p(m): return float(m.get('proximity') or 0.0)
            missing_must.sort(key=_p, reverse=True)
            missing_nice.sort(key=_p, reverse=True)

        score = float(gap_analysis.similarity_score or 0.0)
        match_percentage = int(round(score * 100))
        circumference = 364.4

        # Drag-drop columns: MATCHED merges both tier lists; CRITICAL = missing_must;
        # SOFT = missing_nice.
        matched_chips_for_ui = list(matched_must) + list(matched_nice)
        missing_chips_for_ui = list(missing_must)
        soft_chips_for_ui    = list(missing_nice)

        if match_percentage >= 80:
            primary_action = 'generate_resume'
        elif match_percentage >= 50:
            primary_action = 'chat_fill_gaps'
        else:
            primary_action = 'learning_path'

        total_required = max(
            len(matched_chips_for_ui) + len(missing_chips_for_ui) + len(soft_chips_for_ui),
            1,
        )

        # Banner: "you're X% to closing your gaps" when proximity is high
        # AND the user has enough gaps for it to matter.
        avg_p = gap_analysis.avg_proximity
        n_missing = len(missing_chips_for_ui) + len(soft_chips_for_ui)
        show_proximity_banner = (
            avg_p is not None and avg_p > 0.5 and n_missing >= 3
        )

        # Legacy free-text "Seniority gap" / "Career transition" observations
        # were saved to partial_skills before v2. Surface them as strings only
        # when they look like sentences (contain a space + uppercase) rather
        # than tier objects.
        soft_gaps_text = [
            s for s in (gap_analysis.partial_skills or [])
            if isinstance(s, str) and ' ' in s
        ]

        context = {
            'job': job,
            'profile': profile,
            'gap': gap_analysis,
            'match_percentage': match_percentage,
            'match_band': gap_analysis.match_band or '',
            'red_flags': [m.get('name') if isinstance(m, dict) else str(m)
                          for m in missing_chips_for_ui[:5]],
            'soft_gaps': soft_gaps_text,
            'primary_action': primary_action,
            'can_refresh': True,
            'is_computing': False,
            'avg_proximity': avg_p,
            'avg_proximity_pct': int(round((avg_p or 0) * 100)) if avg_p is not None else None,
            'show_proximity_banner': show_proximity_banner,

            # Gauge
            'gauge_fill': round(score * circumference, 1),
            'gauge_color': "#639922" if score >= 0.8 else "#BA7517" if score >= 0.5 else "#E24B4A",
            'matched_pct': round(len(matched_chips_for_ui) / total_required * 100),
            'missing_pct': round(len(missing_chips_for_ui) / total_required * 100),
            'soft_pct':    round(len(soft_chips_for_ui)    / total_required * 100),

            # JSON data for drag-and-drop Alpine component — chips are objects
            # now (not strings). Each carries tier + proximity + reason + hint.
            'matched_skills_json': json.dumps(matched_chips_for_ui, default=str),
            'missing_skills_json': json.dumps(missing_chips_for_ui, default=str),
            'soft_skills_json':    json.dumps(soft_chips_for_ui,    default=str),

            'evidence': _compute_evidence_safe(profile),
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
    """API endpoint to persist user skill reclassifications from drag-and-drop.

    Payload (v2):
      {
        matched_skills:    [chip_object, ...],   # objects, not strings
        missing_skills:    [chip_object, ...],   # CRITICAL MISSING column
        soft_skill_gaps:   [chip_object, ...],   # SOFT GAPS column
      }

    Chip objects carry tier + proximity + proximity_reason + bridge_hint.
    The endpoint re-splits the three on-screen columns into the four
    tier-aware DB columns based on each chip's `tier` field. When a chip
    moves from MATCHED into a missing column, it loses its evidence_* fields
    and gains proximity=0.8 by default (see spec: "user is overriding an LLM
    match"). When a chip moves between the two missing columns, proximity
    is preserved as-is.
    """
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

    def _normalize_matched(chip, default_tier='must'):
        """Coerce a chip to the matched_* shape."""
        if not isinstance(chip, dict):
            return {'name': str(chip), 'evidence_source': 'user', 'evidence_quote': '',
                    'tier': default_tier}
        return {
            'name': str(chip.get('name', '')),
            'evidence_source': str(chip.get('evidence_source') or 'user'),
            'evidence_quote': str(chip.get('evidence_quote') or '')[:140],
            'tier': str(chip.get('tier') or default_tier),
        }

    def _normalize_missing(chip, default_tier='must'):
        """Coerce a chip to the missing_* shape.

        If chip came from MATCHED (no proximity field), default to 0.8 — the
        user is overriding the LLM, claiming it's missing despite a match.
        If chip already has proximity (moving between missing columns), keep it.
        """
        if not isinstance(chip, dict):
            return {'name': str(chip), 'source_quote': '', 'proximity': 0.0,
                    'proximity_reason': 'User reclassified manually',
                    'bridge_hint': None, 'tier': default_tier}
        raw_p = chip.get('proximity')
        was_matched = raw_p is None or chip.get('_legacy_was_matched')
        try:
            prox = float(raw_p) if raw_p is not None else 0.8
        except (TypeError, ValueError):
            prox = 0.8 if was_matched else 0.0
        # Clamp to [0, 1)
        if prox >= 1.0: prox = 0.99
        if prox < 0.0:  prox = 0.0
        reason = chip.get('proximity_reason') or (
            'User reclassified from matched' if was_matched else ''
        )
        return {
            'name': str(chip.get('name', '')),
            'source_quote': str(chip.get('source_quote') or '')[:140],
            'proximity': prox,
            'proximity_reason': str(reason)[:120],
            'bridge_hint': chip.get('bridge_hint'),
            'tier': str(chip.get('tier') or default_tier),
        }

    # Re-split into 4 tier-aware lists based on each chip's tier.
    matched_must, matched_nice = [], []
    for chip in matched:
        norm = _normalize_matched(chip)
        if norm['tier'] == 'nice':
            matched_nice.append(norm)
        else:
            matched_must.append(norm)

    # CRITICAL MISSING column → missing_must_have (tier overridden to 'must').
    missing_must = [_normalize_missing(c, default_tier='must') for c in missing]
    for m in missing_must:
        m['tier'] = 'must'

    # SOFT GAPS column → missing_nice_to_have (tier overridden to 'nice').
    missing_nice = [_normalize_missing(c, default_tier='nice') for c in soft]
    for m in missing_nice:
        m['tier'] = 'nice'

    # Recompute score / band / avg_proximity from the new buckets.
    from analysis.services.skill_score import (
        avg_proximity as _avg_proximity,
        compute_match_score,
        match_band,
    )
    new_score = compute_match_score(matched_must, missing_must, matched_nice, missing_nice)
    new_band  = match_band(new_score)
    new_avg_p = _avg_proximity(missing_must, missing_nice)

    # Persist all six fields (4 tier lists + score + band + avg_proximity),
    # plus mirror to the legacy flat lists so old consumers see consistent data.
    gap.matched_must_have    = matched_must
    gap.matched_nice_to_have = matched_nice
    gap.missing_must_have    = missing_must
    gap.missing_nice_to_have = missing_nice
    gap.matched_skills = [m['name'] for m in matched_must + matched_nice]
    gap.missing_skills = [m['name'] for m in missing_must + missing_nice]
    gap.partial_skills = [m['name'] for m in missing_nice]  # legacy "soft" column
    gap.similarity_score = new_score
    gap.match_band = new_band
    gap.avg_proximity = new_avg_p
    gap.save(update_fields=[
        'matched_must_have', 'matched_nice_to_have',
        'missing_must_have', 'missing_nice_to_have',
        'matched_skills', 'missing_skills', 'partial_skills',
        'similarity_score', 'match_band', 'avg_proximity',
    ])

    return JsonResponse({
        'success': True,
        'matched_count': len(matched_must) + len(matched_nice),
        'missing_count': len(missing_must),
        'soft_count':    len(missing_nice),
        'similarity_score': new_score,
        'match_band': new_band,
        'avg_proximity': new_avg_p,
    })

@login_required
def check_gap_status_api(request, job_id):
    """Legacy polling endpoint — now always returns completed because analysis is sync."""
    return JsonResponse({'status': 'completed'})

@login_required
def generate_learning_path_view(request, job_id=None):
    """Generate a personalized learning path based on missing skills across
    jobs or a specific job.

    Tier 5: persists the generated `learning_path` on
    `UserProfile.data_content['learning_path']` so the user doesn't lose it
    when they navigate away. Surfaces `completed_skills` so the per-skill
    "Mark as done" toggles render correctly. Adds a return-path CTA so the
    user can re-run gap analysis once they've finished some items.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

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

    data = profile.data_content or {}
    completed_skills = set((data.get('completed_skills') or []))

    if request.method == 'POST':
        try:
            learning_path = generate_learning_path(skills_to_learn)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("Learning path generation failed: %s", e)
            from django.contrib import messages as _messages
            _messages.error(request, "Could not generate learning path — please try again.")
            learning_path = []
        # Persist so the user can navigate away and come back without re-running
        # the LLM. Re-running on POST overwrites the cached path. Caller can
        # also use ?force=1 to regenerate explicitly.
        if learning_path:
            data['learning_path'] = learning_path
            data['learning_path_skills'] = skills_to_learn
            from django.utils import timezone
            data['learning_path_generated_at'] = timezone.now().isoformat()
            profile.data_content = data
            profile.save(update_fields=['data_content', 'updated_at'])
    else:
        # GET: prefer the persisted path. If missing-skills set has shifted
        # since generation (user added a job, ran a new gap analysis), the
        # template will surface a "Re-generate" affordance via the existing
        # form action.
        learning_path = data.get('learning_path') or []

    return render(request, 'analysis/learning_path.html', {
        'skills_to_learn': top_missing,
        'learning_path': learning_path,
        'context_job': context_job,
        'completed_skills': completed_skills,
    })


@login_required
@require_POST
def mark_skill_complete_view(request):
    """Toggle a skill's "completed" state on the user's profile.

    Body: `{skill: "Python"}`. Stored on `data_content['completed_skills']`
    as a sorted list (ordered for deterministic test assertions). Idempotent:
    second call with the same skill removes it (toggle semantics).
    """
    try:
        body = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid_json'}, status=400)
    skill = (body.get('skill') or '').strip().lower()
    if not skill:
        return JsonResponse({'error': 'skill_required'}, status=400)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    data = profile.data_content or {}
    completed = set((data.get('completed_skills') or []))
    if skill in completed:
        completed.remove(skill)
        action = 'unmarked'
    else:
        completed.add(skill)
        action = 'marked'
    data['completed_skills'] = sorted(completed)
    profile.data_content = data
    profile.save(update_fields=['data_content', 'updated_at'])
    return JsonResponse({'ok': True, 'action': action, 'skill': skill,
                         'completed_skills': sorted(completed)})

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
            try:
                script = generate_negotiation_script(profile, job, current_offer, target_salary)
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception("Salary negotiation generation failed: %s", e)
                from django.contrib import messages as _messages
                _messages.error(request, "Could not generate negotiation script — please try again.")
            
    return render(request, 'analysis/salary_negotiator.html', {
        'job': job,
        'profile': profile,
        'script': script
    })
