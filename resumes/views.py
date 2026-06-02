from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, FileResponse, Http404, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.conf import settings
from jobs.models import Job
from profiles.models import UserProfile
from analysis.models import GapAnalysis
from .models import GeneratedResume, CoverLetter
from .services.resume_generator import generate_resume_content, calculate_ats_score, regenerate_section


# The full set of body-section keys a resume can render. The user can
# reorder these (saved as resume.content['section_order']) but not add
# unknown keys — the endpoint validates against this whitelist so a
# typo or stale UI can't poison the saved order.
RESUME_SECTION_KEYS = (
    'summary', 'skills', 'experience', 'education',
    'projects', 'certifications', 'languages',
)
DEFAULT_SECTION_ORDER = list(RESUME_SECTION_KEYS)
from .services.pdf_exporter import generate_pdf
from .services.docx_exporter import generate_docx
from .services.cover_letter_generator import generate_cover_letter_content
from .services.pdf_generator import generate_optimized_pdf
import logging
import os
import tempfile
import json

logger = logging.getLogger(__name__)

@login_required
def generate_resume_view(request, job_id):
    """Generate tailored resume — enqueues a background task on POST."""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    # Needs a gap analysis first
    gap_analysis = get_object_or_404(GapAnalysis, job=job)
    
    # Needs a profile
    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return redirect('upload_master_profile')

    if request.method == 'POST':
        # Instantly render the "Generating..." state to provide immediate feedback.
        # The frontend JS will then trigger the actual sync computation via API.
        # The supervisor loop (generate -> review -> regenerate) roughly doubles
        # latency (observed ~90s, worse under Groq 429 backoff), so the client
        # abort timeout must scale with the round cap — otherwise the browser
        # gives up at 60s while the backend is still working and a resume that
        # WAS generated looks like a failure.
        if getattr(settings, 'SUPERVISOR_ENABLED', False):
            rounds = int(getattr(settings, 'SUPERVISOR_MAX_REVISION_ROUNDS', 1))
            gen_timeout_ms = (rounds + 1) * 90000
        else:
            gen_timeout_ms = 60000
        return render(request, 'resumes/generate.html', {
            'job': job,
            'generating': True,
            'gen_timeout_ms': gen_timeout_ms,
        })
    
    return render(request, 'resumes/generate.html', {'job': job})

@login_required
@require_POST
def trigger_resume_generation_api(request, job_id):
    """Sync API endpoint called by the frontend loader to perform the actual LLM work."""
    from .tasks import generate_resume_task
    try:
        # Perform the actual work synchronously
        generate_resume_task(str(job_id), request.user.id)
        
        # Find the newly created resume to return its ID
        from .models import GeneratedResume
        resume = GeneratedResume.objects.filter(gap_analysis__job_id=job_id).order_by('-created_at').first()
        
        return JsonResponse({
            'success': True, 
            'resume_id': str(resume.id) if resume else None
        })
    except Exception as e:
        logger.error(f"Sync resume generation failed: {e}")
        return JsonResponse({'success': False, 'error': 'Resume generation failed. Please try again.'}, status=500)


@login_required
@require_GET
def check_resume_status_api(request, job_id):
    """Legacy polling endpoint — the work is now done via the POST trigger API directly."""
    # Find latest resume for this job
    from .models import GeneratedResume
    resume = GeneratedResume.objects.filter(gap_analysis__job_id=job_id, gap_analysis__user=request.user).order_by('-created_at').first()
    if resume:
        return JsonResponse({'status': 'completed', 'resume_id': str(resume.id)})
    return JsonResponse({'status': 'waiting'})


@login_required
@require_GET
def regenerate_resume_view(request, resume_id):
    """Canonical entry point for in-place supervised resume regeneration.

    This view + trigger_resume_regeneration_api are the canonical
    implementation. The /resumes/edit/<id>/?refresh=1 branch in
    resume_edit_view is a back-compat redirect into HERE; no third regen
    entry point should be added — both should land in this loader.

    PR audit fix #3 (2026-05-29): the previous Path B behaviour was to
    regenerate inline on the resume_edit_view GET handler, which
    (a) blocked the browser for 15-90s on the supervised LLM call and
    (b) actually used the NON-supervised generator, silently bypassing
    the supervisor safety net.

    This view mirrors Path A's UX exactly — it reuses generate.html in
    its `generating=True` mode, scaled timeout, AbortController, and
    substep panel — but routes the loader's POST to
    trigger_resume_regen_api so the existing GeneratedResume row is
    updated in place instead of a new one being created. The
    `redirect_url` includes ?refresh=0 to prevent the destination
    edit page from looping back into another regen.
    """
    # Ownership scoped at the queryset, not via a post-fetch attribute
    # check: a foreign user's resume id resolves to 404 directly,
    # eliminating the (theoretical) window where we'd fetch a row we
    # have no business touching.
    resume = get_object_or_404(
        GeneratedResume,
        id=resume_id,
        gap_analysis__job__user=request.user,
    )
    job = resume.gap_analysis.job
    if getattr(settings, 'SUPERVISOR_ENABLED', False):
        rounds = int(getattr(settings, 'SUPERVISOR_MAX_REVISION_ROUNDS', 1))
        gen_timeout_ms = (rounds + 1) * 90000
    else:
        gen_timeout_ms = 60000
    return render(request, 'resumes/generate.html', {
        'job': job,
        'generating': True,
        'gen_timeout_ms': gen_timeout_ms,
        # Route the loader to the in-place regen endpoint instead of
        # Path A's job-keyed create-new endpoint. The template falls
        # back to {% url 'trigger_resume_api' job.id %} when this is
        # unset so Path A is untouched.
        'trigger_url': reverse('trigger_resume_regen_api', args=[resume.id]),
        # ?refresh=0 disables the destination view's stale-profile
        # check so we don't loop right back into another regen.
        'redirect_url': reverse('resume_edit', args=[resume.id]) + '?refresh=0',
    })


@login_required
@require_POST
def trigger_resume_regeneration_api(request, resume_id):
    """In-place supervised regeneration. Counterpart of trigger_resume_generation_api,
    but updates an existing GeneratedResume row rather than creating a new one.

    Mirrors generate_resume_task's orchestration:
      generate_resume_content_supervised → calculate_ats_score → save.
    Preserves the user's template_name choice across the regen, matching
    the contract of the previous inline Path B implementation.
    """
    from .services.resume_generator import generate_resume_content_supervised
    # Ownership scoped at the queryset (same pattern as regenerate_resume_view).
    # A foreign user's resume id resolves to 404 directly — no post-fetch
    # attribute check, no chance of leaking a row we shouldn't have read.
    resume = get_object_or_404(
        GeneratedResume,
        id=resume_id,
        gap_analysis__job__user=request.user,
    )
    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'No profile found'}, status=400)

    gap_analysis = resume.gap_analysis
    job = gap_analysis.job
    # Fix #1 — Path B updates in place, so the snapshot lives directly on
    # THIS row. Use it; fall back to the gap-analysis lookup so a Path B
    # regen still gets a snapshot if a SIBLING row was the one most
    # recently exported (rare but possible if the user has multiple rows
    # for the same JD).
    from .services.resume_generator import load_previous_best_for
    previous_best = resume.previous_best or None
    if not (previous_best and isinstance(previous_best, dict)
            and previous_best.get('content')):
        previous_best = load_previous_best_for(gap_analysis)
    try:
        # Atomicity: build the full new content + score FIRST. Only after
        # both succeed do we mutate the GeneratedResume instance — and we
        # do it in a contiguous block ending with a single .save() so any
        # exception from the LLM path leaves the existing row's content,
        # ats_score, and validation_report byte-identical. Do NOT mutate
        # resume.<field> until everything below has succeeded.
        new_content = generate_resume_content_supervised(
            profile, job, gap_analysis, previous_best=previous_best,
        )
        new_score = calculate_ats_score(new_content, job.extracted_skills)
        # Preserve the user's template choice across regeneration (same
        # contract as the previous inline Path B behaviour).
        if resume.content and resume.content.get('template_name'):
            new_content['template_name'] = resume.content['template_name']
        new_validation_report = new_content.get('validation_report', {})
        # All inputs ready — commit the row in one block. Once we start
        # assigning, every line below MUST NOT raise so the save fires.
        resume.content = new_content
        resume.ats_score = new_score
        resume.validation_report = new_validation_report
        resume.save()
        logger.info(
            "Supervised in-place regeneration completed for resume %s", resume.id,
        )
        return JsonResponse({'success': True, 'resume_id': str(resume.id)})
    except Exception as e:
        logger.exception("Supervised regen failed for resume %s", resume.id)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

def _description_text_to_list(raw):
    """Convert a textarea string (newline-separated bullets) into a List[str].

    Handles `\\r\\n` line endings, trims whitespace, drops empty lines, and
    treats None as an empty list.
    """
    if raw is None:
        return []
    text = str(raw).replace('\r\n', '\n').replace('\r', '\n')
    return [line.strip() for line in text.split('\n') if line.strip()]


def _description_list_to_text(value):
    """Convert a List[str] description into a newline-separated textarea string.

    Returns `''` for None or empty lists. A single string is returned as-is
    (lets legacy string-shaped descriptions round-trip without munging).
    """
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return '\n'.join(str(d) for d in value if d)


def _capture_previous_best(resume) -> None:
    """Fix #1 — write the snapshot used as a 'preserve OR improve'
    reference on the NEXT regeneration for the same JD. Called from
    both export views just before the file is generated.

    Best-effort: this function MUST NOT raise. If anything fails (DB
    write timeout, JD-hash compute error, JSON-serialise edge case),
    we log and continue so the user's download still succeeds. The
    stickiness mechanism degrades gracefully — worst case the user
    sees no preservation on the next regen, same as today.
    """
    try:
        from datetime import datetime, timezone
        import copy as _copy
        from .services.resume_generator import _jd_identity_hash
        job = resume.gap_analysis.job
        snapshot = {
            'content': _copy.deepcopy(resume.content or {}),
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'ats_score_at_export': float(resume.ats_score or 0.0),
            'jd_identity_hash': _jd_identity_hash(job),
        }
        resume.previous_best = snapshot
        resume.save(update_fields=['previous_best'])
        logger.info("Captured previous_best snapshot for resume %s", resume.id)
    except Exception as exc:  # noqa: BLE001 — never block the download
        logger.warning(
            "Capture of previous_best failed for resume %s (%s); "
            "export proceeds without snapshot.",
            getattr(resume, 'id', '<unknown>'), exc,
        )


def _normalize_legacy_resume_content(resume):
    """Backwards compatibility check for older resumes generated before the schema upgraded descriptions to Lists"""
    modified = False
    content = resume.content

    for section in ['experience', 'projects']:
        if section in content:
            for item in content[section]:
                desc = item.get('description')
                if desc is None:
                    item['description'] = []
                    modified = True
                elif isinstance(desc, str):
                    item['description'] = _description_text_to_list(desc)
                    modified = True

    if modified:
        resume.content = content
        resume.save()

@login_required
def resume_preview_view(request, resume_id):
    """Preview generated resume with edit capabilities"""
    resume = get_object_or_404(GeneratedResume, id=resume_id)
    job = resume.gap_analysis.job
    
    # Check ownership
    if job.user != request.user:
        return redirect('dashboard')
        
    _normalize_legacy_resume_content(resume)
    
    context = {
        'resume': resume,
        'job': job,
        'content': resume.content
    }
    
    return render(request, 'resumes/preview.html', context)

@login_required
def resume_edit_view(request, resume_id):
    """Edit resume content"""
    resume = get_object_or_404(GeneratedResume, id=resume_id)

    # Authorization check
    if resume.gap_analysis.job.user != request.user:
        raise Http404

    if request.method == 'POST':
        # Update resume content from form
        updated_content = resume.content.copy()
        
        # Simple fields
        updated_content['professional_title'] = request.POST.get('professional_title', '')
        updated_content['professional_summary'] = request.POST.get('professional_summary', '')
        updated_content['objective'] = request.POST.get('objective', '')
        updated_content['template_name'] = request.POST.get('template_name', 'ats_clean')

        # Skills (comma separated)
        skills_raw = request.POST.get('skills', '')
        updated_content['skills'] = [s.strip() for s in skills_raw.split(',') if s.strip()]

        # Experience (arrays — incl. location/industry passthrough from master)
        exp_titles = request.POST.getlist('exp_title[]')
        exp_companies = request.POST.getlist('exp_company[]')
        exp_durations = request.POST.getlist('exp_duration[]')
        exp_locations = request.POST.getlist('exp_location[]')
        exp_industries = request.POST.getlist('exp_industry[]')
        exp_descriptions = request.POST.getlist('exp_description[]')

        experience_list = []
        for i in range(len(exp_titles)):
            if exp_titles[i].strip() or exp_companies[i].strip():
                raw_desc = exp_descriptions[i] if i < len(exp_descriptions) else ''
                experience_list.append({
                    'title': exp_titles[i],
                    'company': exp_companies[i],
                    'duration': exp_durations[i] if i < len(exp_durations) else '',
                    'location': exp_locations[i] if i < len(exp_locations) else '',
                    'industry': exp_industries[i] if i < len(exp_industries) else '',
                    'description': _description_text_to_list(raw_desc)
                })
        updated_content['experience'] = experience_list

        # Education (arrays — incl. field/gpa/location/honors)
        edu_degrees = request.POST.getlist('edu_degree[]')
        edu_fields = request.POST.getlist('edu_field[]')
        edu_institutions = request.POST.getlist('edu_institution[]')
        edu_years = request.POST.getlist('edu_year[]')
        edu_gpas = request.POST.getlist('edu_gpa[]')
        edu_locations = request.POST.getlist('edu_location[]')
        edu_honors_raw = request.POST.getlist('edu_honors[]')

        education_list = []
        for i in range(len(edu_degrees)):
            if edu_degrees[i].strip() or edu_institutions[i].strip():
                honors_text = edu_honors_raw[i] if i < len(edu_honors_raw) else ''
                education_list.append({
                    'degree': edu_degrees[i],
                    'field': edu_fields[i] if i < len(edu_fields) else '',
                    'institution': edu_institutions[i],
                    'year': edu_years[i] if i < len(edu_years) else '',
                    'gpa': edu_gpas[i] if i < len(edu_gpas) else '',
                    'location': edu_locations[i] if i < len(edu_locations) else '',
                    'honors': _description_text_to_list(honors_text),
                })
        updated_content['education'] = education_list

        # Projects (arrays — incl. technologies as comma-separated string)
        proj_names = request.POST.getlist('proj_name[]')
        proj_desc = request.POST.getlist('proj_description[]')
        proj_urls = request.POST.getlist('proj_url[]')
        proj_techs_raw = request.POST.getlist('proj_technologies[]')
        projects_list = []
        for i in range(len(proj_names)):
            if proj_names[i].strip():
                raw_desc = proj_desc[i] if i < len(proj_desc) else ''
                tech_str = proj_techs_raw[i] if i < len(proj_techs_raw) else ''
                technologies = [t.strip() for t in tech_str.split(',') if t.strip()]
                projects_list.append({
                    'name': proj_names[i],
                    'description': _description_text_to_list(raw_desc),
                    'url': proj_urls[i] if i < len(proj_urls) else '',
                    'technologies': technologies,
                })
        updated_content['projects'] = projects_list

        # Certifications (arrays — incl. duration)
        cert_names = request.POST.getlist('cert_name[]')
        cert_issuers = request.POST.getlist('cert_issuer[]')
        cert_dates = request.POST.getlist('cert_date[]')
        cert_durations = request.POST.getlist('cert_duration[]')
        cert_urls = request.POST.getlist('cert_url[]')
        cert_list = []
        for i in range(len(cert_names)):
            if cert_names[i].strip():
                cert_list.append({
                    'name': cert_names[i],
                    'issuer': cert_issuers[i] if i < len(cert_issuers) else '',
                    'date': cert_dates[i] if i < len(cert_dates) else '',
                    'duration': cert_durations[i] if i < len(cert_durations) else '',
                    'url': cert_urls[i] if i < len(cert_urls) else '',
                })
        updated_content['certifications'] = cert_list
        
        # Extended Items Helper
        def extract_extended(prefix: str):
            titles = request.POST.getlist(f'{prefix}_title[]')
            orgs = request.POST.getlist(f'{prefix}_organization[]')
            dates = request.POST.getlist(f'{prefix}_date[]')
            descs = request.POST.getlist(f'{prefix}_description[]')
            items = []
            for i in range(len(titles)):
                if titles[i].strip():
                    raw_desc = descs[i] if i < len(descs) else ''
                    items.append({
                        'title': titles[i],
                        'organization': orgs[i] if i < len(orgs) else '',
                        'date': dates[i] if i < len(dates) else '',
                        'description': _description_text_to_list(raw_desc)
                    })
            return items

        # Extended Lists
        updated_content['volunteer_experience'] = extract_extended('vol')
        updated_content['awards'] = extract_extended('awd')
        updated_content['publications'] = extract_extended('pub')
        updated_content['patents'] = extract_extended('pat')
        
        # Languages (comma separated simple list, or array of strings)
        lang_raw = request.POST.get('languages', '')
        if lang_raw:
            updated_content['languages'] = [l.strip() for l in lang_raw.split(',') if l.strip()]
        else:
            updated_content['languages'] = []

        # Save to DB
        resume.content = updated_content
        resume.save()
        
        return redirect(f"{request.path}?saved=true")
    
    # ---- GET REQUEST HANDLING ----
    _normalize_legacy_resume_content(resume)

    # Auto-regenerate content if the profile was updated after this resume was
    # created. Triggered when the user re-uploads their CV — the stored
    # content is a stale snapshot of the old profile and the edit page would
    # otherwise show outdated data.
    # Skip when ?refresh=0 so users can bypass regeneration if they want.
    try:
        profile = UserProfile.objects.get(user=request.user)
        should_refresh = (
            request.GET.get('refresh') != '0'
            and profile.updated_at
            and resume.created_at
            and profile.updated_at > resume.created_at
        )
        if should_refresh or request.GET.get('refresh') == '1':
            # Back-compat regen entry point — redirects into the canonical
            # implementation at regenerate_resume_view. Do not add a third
            # regen entry point: both this branch and /resumes/regenerate/
            # MUST land in regenerate_resume_view's loader.
            #
            # PR audit report §6.5 fix #3 (2026-05-29): the previous behavior
            # was an inline call to the NON-supervised generator on a plain
            # GET — 15-30s typical, up to ~88s under Groq 429 backoff. That
            # blocked the browser and silently bypassed the supervisor
            # safety net. Both problems are addressed by handing the
            # regeneration off to the same async-loader UX Path A uses
            # ("Generate Resume" button); the loader POSTs to a new
            # in-place trigger endpoint that runs through the SUPERVISED
            # generator. The redirect target renders generate.html in
            # `generating=True` mode pointed at trigger_resume_regen_api.
            return redirect('regenerate_resume', resume_id=resume.id)
        else:
            # Cheap, no-LLM auto-sync of new master-profile fields into this
            # resume's content. Patches blank/missing supplemental fields
            # (experience location/industry, education field/gpa/honors,
            # project technologies, certification duration, objective) by
            # positional index. Preserves typed bullets and LLM-rewritten
            # content. Idempotent: only saves if the merge actually changed
            # something, so revisiting the page is a no-op.
            from resumes.services.resume_generator import _ensure_profile_data_preserved
            profile_data = profile.data_content or {}
            if profile_data:
                before = json.dumps(resume.content or {}, sort_keys=True, default=str)
                merged = _ensure_profile_data_preserved(resume.content or {}, profile_data)
                after = json.dumps(merged, sort_keys=True, default=str)
                if before != after:
                    resume.content = merged
                    resume.save()
                    # Stash a one-shot flag in the session so the next render
                    # of this resume's edit page can surface a banner. This
                    # is the single signal the template uses to differentiate
                    # "you opened a freshly-synced resume" from "no change."
                    request.session[f'resume_synced_{resume.id}'] = True
                    logger.info(f"Auto-synced master fields into resume {resume.id}")
    except UserProfile.DoesNotExist:
        pass
    except Exception as e:
        logger.exception(f"Failed to auto-regenerate resume {resume.id}: {e}")
        # Non-fatal — fall through and render with existing content

    # Create a deep copy for the form so we can convert lists to newline-separated strings
    import copy
    form_content = copy.deepcopy(resume.content)

    for section in ['experience', 'projects', 'volunteer_experience', 'awards', 'publications', 'patents']:
        if section in form_content:
            for item in form_content[section]:
                # Skip non-dict entries — `awards` is now a List[str]
                # (Round 1.5), and the LLM occasionally emits a bare
                # string inside a section that used to be all-dicts.
                # We don't want the textarea-conversion step to crash
                # the whole edit page over a typing variance.
                if not isinstance(item, dict):
                    continue
                item['description'] = _description_list_to_text(item.get('description'))

    # Overlay the modified content back onto the resume object specifically for the template
    resume.content = form_content

    # Single source of truth for the template picker. Values must match
    # PDF template file names: pdf_template_{value}.html. Old theme
    # names (standard / executive / etc.) are still accepted on POST and
    # migrate through pdf_exporter.THEME_MIGRATION at render time so an
    # in-flight legacy value never 500s the export.
    template_choices = [
        {'value': 'ats_clean',         'label': 'ATS Clean',  'subtitle': 'Single-column, black-only, recruiter-safe',  'tag': 'B&W'},
        {'value': 'ats_clean_accent',  'label': 'ATS Accent', 'subtitle': 'Same structure, one restrained accent color', 'tag': 'Accent'},
    ]

    # Pass the user's profile so the live preview's contact line can render
    # the same portfolio / Kaggle / Scholar / other links the PDF templates
    # render. Without this, the preview's contact info would diverge from
    # the downloaded PDF — confusing the user about what's actually there.
    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        profile = None

    # Resolve the section order: user's saved choice if present, else default.
    # Defensive: ignore any saved keys that aren't in the current whitelist
    # (e.g., an old key from a prior schema), and fill in any missing
    # whitelisted keys at the end so a partial saved order still works.
    saved_order = resume.content.get('section_order') or []
    valid_saved = [s for s in saved_order if s in RESUME_SECTION_KEYS]
    section_order = valid_saved + [s for s in DEFAULT_SECTION_ORDER if s not in valid_saved]

    section_labels = {
        'summary': 'Professional Summary',
        'skills': 'Skills',
        'experience': 'Experience',
        'education': 'Education',
        'projects': 'Projects',
        'certifications': 'Certifications',
        'languages': 'Languages',
    }
    # List of (key, label) tuples in the user's chosen order — easier for the
    # template to iterate than nesting a dict lookup inside a `{% for %}`.
    section_order_with_labels = [(k, section_labels.get(k, k.title())) for k in section_order]

    # One-shot "we synced master fields into this resume" banner flag. Set
    # by the auto-sync branch above; pop here so it shows exactly once per
    # sync event.
    sync_banner = request.session.pop(f'resume_synced_{resume.id}', False)

    # Audit fix #2 (2026-05-29): surface validation + supervisor findings
    # as a small severity-tiered banner. Read-only — the underlying
    # findings are computed elsewhere; this presenter only translates them
    # into a human-readable summary the template renders.
    from .services.findings_presenter import build_review_summary
    review_summary = build_review_summary(resume.content, resume.validation_report)

    return render(request, 'resumes/edit.html', {
        'resume': resume,
        'profile': profile,
        'template_choices': template_choices,
        'section_order': section_order,
        'section_order_with_labels': section_order_with_labels,
        'sync_banner': sync_banner,
        'review_summary': review_summary,
    })


@login_required
@require_POST
def update_section_order_view(request, resume_id):
    """Persist a user-chosen section order on the resume.

    Body: `{"order": ["summary", "experience", "skills", ...]}`.
    Validates against RESUME_SECTION_KEYS so a stale UI or typo can't poison
    the saved order. Missing keys are filled in at the end (so the user can
    e.g. just promote 'projects' to the top and leave the rest implicit).
    """
    resume = get_object_or_404(GeneratedResume, id=resume_id)
    if resume.gap_analysis.job.user != request.user:
        raise Http404
    try:
        body = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid_json'}, status=400)

    raw = body.get('order')
    if not isinstance(raw, list):
        return JsonResponse({'error': 'order_must_be_list'}, status=400)
    valid = [s for s in raw if s in RESUME_SECTION_KEYS]
    # Append any whitelisted sections the client omitted, preserving the
    # user's chosen order for the ones they did send.
    seen = set(valid)
    final = valid + [s for s in DEFAULT_SECTION_ORDER if s not in seen]

    content = resume.content.copy() if resume.content else {}
    content['section_order'] = final
    resume.content = content
    resume.save(update_fields=['content'])
    return JsonResponse({'order': final})


@login_required
@require_POST
def regenerate_section_view(request, resume_id, section):
    """Regenerate one section of a resume in place. Returns JSON with the
    updated value. The edit page calls this from a per-section "Regenerate"
    button so the user can iterate on a weak section without losing edits
    elsewhere on the page.

    POST body is empty — context (CV, JD, signals, gap analysis, current
    in-progress edits) is fetched server-side. Optional `current_content`
    JSON body lets the client send the user's unsaved local edits so the
    LLM sees what they're actively working on, not just the saved snapshot.

    Allowed sections come from regenerate_section()'s whitelist:
      'professional_summary' | 'skills' | 'experience' | 'projects'
    """
    resume = get_object_or_404(GeneratedResume, id=resume_id)
    if resume.gap_analysis.job.user != request.user:
        raise Http404
    if section not in {'professional_summary', 'skills', 'experience', 'projects'}:
        return JsonResponse({'error': 'unsupported_section'}, status=400)

    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return JsonResponse({'error': 'no_profile'}, status=400)

    job = resume.gap_analysis.job
    gap_analysis = resume.gap_analysis

    # Use the client's in-flight edits when present so the LLM sees the
    # user's working draft, not a stale DB snapshot. Falls back to the
    # saved content if the body is empty or malformed.
    try:
        body = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        body = {}
    current_content = body.get('current_content')
    if not isinstance(current_content, dict):
        current_content = resume.content

    try:
        new_value = regenerate_section(profile, job, gap_analysis, current_content, section)
    except Exception:
        logger.exception("regenerate_section failed (resume=%s section=%s)", resume_id, section)
        return JsonResponse({'error': 'regen_failed'}, status=502)

    # Validate the LLM actually returned usable content. The most common
    # silent failure for experience/projects regen is the LLM returning
    # an empty list (rate limit recovered with junk, schema satisfied
    # but bullets dropped). Without this guard, we'd persist []  and the
    # subsequent reload would show an empty section — looking to the
    # user like "regenerate did nothing." Detect, refuse to save, and
    # surface a 422 so the UI can show a real error instead of silently
    # blowing away the section.
    def _empty(v):
        if v is None:
            return True
        if isinstance(v, str):
            return not v.strip()
        if isinstance(v, list):
            return len(v) == 0
        return False

    if _empty(new_value):
        logger.warning(
            "regenerate_section returned empty value (resume=%s section=%s) — refusing to overwrite",
            resume_id, section,
        )
        return JsonResponse({
            'error': 'empty_regeneration',
            'detail': "The model returned no usable content for this section. "
                      "Your existing content is unchanged. Try again — this is "
                      "usually transient (rate-limit, brief model hiccup).",
        }, status=422)

    # Per-list-element sanity for experience/projects: at least one entry
    # must have non-empty title/name AND at least one bullet. An array
    # of stub entries is just as bad as an empty array.
    if section == 'experience':
        usable = [e for e in (new_value or []) if isinstance(e, dict)
                  and (e.get('title') or e.get('company'))
                  and (e.get('description') or [])]
        if not usable:
            logger.warning("regenerate_section experience returned no usable entries — refusing")
            return JsonResponse({
                'error': 'empty_regeneration',
                'detail': "The model returned experience entries with no bullets. "
                          "Your existing content is unchanged. Try again.",
            }, status=422)
        # Fix-1b (2026-06-01) — HARD identity guard. The trace caught a
        # phantom "Banque Misr" role shipping because the prior code
        # whole-section-replaced regardless of identity. Now: filter the
        # returned entries to those whose company (or title fallback)
        # appears in current_content['experience']. If the LLM invented
        # a role, drop it. If the kept set doesn't cover every real
        # role, REJECT — partial returns are worse than a retry.
        from resumes.services.role_identity_guard import (
            filter_experiences_to_known,
            covers_known_identities,
            log_dropped,
        )
        current_exps = (current_content or {}).get('experience') or []
        kept, dropped = filter_experiences_to_known(new_value, current_exps)
        if dropped:
            log_dropped(dropped, kind='experience', surface='section-regen')
        if not covers_known_identities(kept, current_exps, kind='experience'):
            logger.warning(
                "regenerate_section experience identity mismatch — refusing "
                "(returned=%d kept=%d dropped=%d known=%d)",
                len(new_value or []), len(kept), len(dropped), len(current_exps),
            )
            return JsonResponse({
                'error': 'identity_mismatch',
                'detail': "The model returned a different set of roles than the "
                          "resume currently has (it invented or dropped a role). "
                          "Your existing content is unchanged. Try again.",
            }, status=422)
        new_value = kept
    elif section == 'projects':
        usable = [p for p in (new_value or []) if isinstance(p, dict)
                  and p.get('name')
                  and (p.get('description') or [])]
        if not usable:
            logger.warning("regenerate_section projects returned no usable entries — refusing")
            return JsonResponse({
                'error': 'empty_regeneration',
                'detail': "The model returned project entries with no bullets. "
                          "Your existing content is unchanged. Try again.",
            }, status=422)
        from resumes.services.role_identity_guard import (
            filter_projects_to_known,
            covers_known_identities,
            log_dropped,
        )
        current_projs = (current_content or {}).get('projects') or []
        kept, dropped = filter_projects_to_known(new_value, current_projs)
        if dropped:
            log_dropped(dropped, kind='projects', surface='section-regen')
        if not covers_known_identities(kept, current_projs, kind='projects'):
            logger.warning(
                "regenerate_section projects identity mismatch — refusing "
                "(returned=%d kept=%d dropped=%d known=%d)",
                len(new_value or []), len(kept), len(dropped), len(current_projs),
            )
            return JsonResponse({
                'error': 'identity_mismatch',
                'detail': "The model returned a different set of projects than the "
                          "resume currently has (it invented or dropped a project). "
                          "Your existing content is unchanged. Try again.",
            }, status=422)
        new_value = kept

    # Persist on the saved snapshot so a subsequent reload reflects the
    # regen. We don't auto-save the user's other in-flight edits here —
    # the form's save button does that. We're only writing the regenerated
    # field.
    saved = resume.content.copy()
    saved[section] = new_value
    resume.content = saved
    resume.save(update_fields=['content'])

    return JsonResponse({'section': section, 'value': new_value})


def _render_export_error(request, resume_id, *, format: str, alt_format: str, error: Exception):
    """Render the friendly export-error page with retry / alt-format / back links.

    The previous behavior was a `HttpResponse('… failed. Please try again.', 500)` —
    a blank tab with plaintext. Real export failures (xhtml2pdf rendering edge
    cases, DOCX runtime issues) are usually transient, so giving the user a
    one-click retry plus a fallback-format link recovers them without a
    support ticket.

    Backend-unavailable errors (e.g. missing libcairo-2.dll for PDF
    rendering on Windows) get a more specific user-facing message that
    points to the alt format as the primary CTA — retrying won't help
    when the underlying system library is missing.
    """
    error_text = str(error).lower()
    # Detect the cases where "retry" is hopeless because something at
    # the system / dependency layer is broken.
    backend_unavailable = (
        isinstance(error, OSError)
        and any(tok in error_text for tok in ('cairo', 'libcairo', 'dll', 'shared library'))
    )
    return render(
        request,
        'resumes/export_error.html',
        {
            'format': format,
            'alt_format': alt_format,
            'retry_url': reverse(f'export_{format}', args=[resume_id]),
            'alt_url': reverse(f'export_{alt_format}', args=[resume_id]),
            'back_url': reverse('resume_preview', args=[resume_id]),
            'error_detail': f"{error.__class__.__name__}: {error}" if request.user.is_staff else '',
            'backend_unavailable': backend_unavailable,
        },
        status=500,
    )


@login_required
def export_docx_view(request, resume_id):
    """Export resume as a DOCX file.

    Same authorization + section_order resolution as the PDF export, but
    produces an ATS-friendly DOCX via python-docx instead of xhtml2pdf.
    """
    resume = get_object_or_404(GeneratedResume, id=resume_id)
    if resume.gap_analysis.job.user != request.user:
        raise Http404
    _normalize_legacy_resume_content(resume)
    # Fix #1 — capture the version being exported as previous_best.
    # Best-effort: must NEVER prevent the download from succeeding.
    _capture_previous_best(resume)
    try:
        buf = generate_docx(resume)
        data = buf.getvalue()
    except Exception as exc:
        logger.exception("DOCX export failed for resume %s", resume_id)
        return _render_export_error(request, resume_id, format='docx', alt_format='pdf', error=exc)
    response = HttpResponse(
        data,
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )
    safe_title = resume.gap_analysis.job.title.replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename="resume_{safe_title}.docx"'
    return response


@login_required
def export_pdf_view(request, resume_id):
    """Export resume as PDF"""
    resume = get_object_or_404(GeneratedResume, id=resume_id)

    # Authorization check
    if resume.gap_analysis.job.user != request.user:
        raise Http404

    _normalize_legacy_resume_content(resume)
    # Fix #1 — capture the version being exported as previous_best.
    # Best-effort: must NEVER prevent the download from succeeding.
    _capture_previous_best(resume)

    fd, output_path = tempfile.mkstemp(suffix='.pdf')
    os.close(fd)

    try:
        template_name = resume.content.get('template_name', 'ats_clean')
        generate_pdf(resume, output_path, template_name)
        # Read into memory so we can delete the temp file safely
        with open(output_path, 'rb') as f:
            pdf_data = f.read()
    except Exception as exc:
        logger.exception("PDF export failed for resume %s", resume_id)
        # Clean up the temp file before rendering the error page so we don't
        # leak disk on the failure path either.
        try:
            os.unlink(output_path)
        except OSError:
            pass
        return _render_export_error(request, resume_id, format='pdf', alt_format='docx', error=exc)
    finally:
        # Always clean up the temp file to avoid disk leaks
        try:
            os.unlink(output_path)
        except OSError:
            pass

    response = HttpResponse(pdf_data, content_type='application/pdf')
    safe_title = resume.gap_analysis.job.title.replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename="resume_{safe_title}.pdf"'
    return response


@login_required
def generate_optimized_pdf_view(request, job_id):
    """Generate optimized PDF from profile data_content (NEW APPROACH)"""
    
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return HttpResponse("Profile not found", status=404)
    
    try:
        # Generate PDF buffer
        pdf_buffer = generate_optimized_pdf(profile, job)
        
        # Return as download
        response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{profile.full_name}_Resume_{job.company}.pdf"'
        return response
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HttpResponse("PDF generation failed. Please try again.", status=500)

@login_required
def generate_cover_letter_view(request, job_id):
    """Generate tailored cover letter"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return redirect('upload_master_profile')

    if request.method == 'POST':
        try:
            content = generate_cover_letter_content(profile, job)
            letter = CoverLetter.objects.create(
                job=job,
                profile=profile,
                content=content
            )
            return redirect('cover_letter_preview', letter_id=letter.id)
        except Exception as e:
            return HttpResponse("Cover letter generation failed. Please try again.", status=500)
            
    return render(request, 'resumes/generate_cover_letter.html', {'job': job})

@login_required
def cover_letter_preview_view(request, letter_id):
    """Preview generated cover letter"""
    letter = get_object_or_404(CoverLetter, id=letter_id)
    
    if letter.job.user != request.user:
        return redirect('dashboard')
        
    return render(request, 'resumes/cover_letter_preview.html', {'letter': letter})

@login_required
def resume_list_view(request):
    """View and manage all generated resumes.

    Also fetches the user's profile so the per-card thumbnails (rendered
    HTML previews of each resume in the grid) can show the candidate's
    real name + contact line — same data the PDF template renders. Name
    falls back to the email local-part when full_name isn't set yet.
    """
    from profiles.models import UserProfile
    profile = UserProfile.objects.filter(user=request.user).first()
    if profile and profile.full_name:
        profile_name = profile.full_name
    else:
        profile_name = (request.user.email or '').split('@')[0] or 'Your Name'

    resumes = GeneratedResume.objects.filter(
        gap_analysis__job__user=request.user
    ).select_related('gap_analysis__job').order_by('-created_at')

    return render(request, 'resumes/list.html', {
        'resumes': resumes,
        'profile': profile,
        'profile_name': profile_name,
    })

@login_required
def resume_delete_view(request, resume_id):
    """Delete a tailored resume"""
    resume = get_object_or_404(GeneratedResume, id=resume_id)
    
    # Security: Ensure only the owner can delete it
    if resume.gap_analysis.job.user != request.user:
        raise Http404("Not authorized")
        
    if request.method == 'POST':
        resume.delete()
        return redirect('resume_list')
        
    return redirect('resume_list')
