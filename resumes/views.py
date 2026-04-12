from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, FileResponse, Http404, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from jobs.models import Job
from profiles.models import UserProfile
from analysis.models import GapAnalysis
from .models import GeneratedResume, CoverLetter
from .services.resume_generator import generate_resume_content, calculate_ats_score
from .services.pdf_exporter import generate_pdf
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
        return render(request, 'resumes/generate.html', {
            'job': job,
            'generating': True,
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
                    item['description'] = [d.strip() for d in desc.split('\n') if d.strip()]
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
        updated_content['template_name'] = request.POST.get('template_name', 'standard')
        
        # Skills (comma separated)
        skills_raw = request.POST.get('skills', '')
        updated_content['skills'] = [s.strip() for s in skills_raw.split(',') if s.strip()]
        
        # Experience (arrays)
        exp_titles = request.POST.getlist('exp_title[]')
        exp_companies = request.POST.getlist('exp_company[]')
        exp_durations = request.POST.getlist('exp_duration[]')
        exp_descriptions = request.POST.getlist('exp_description[]')
        
        experience_list = []
        for i in range(len(exp_titles)):
            if exp_titles[i].strip() or exp_companies[i].strip():
                raw_desc = exp_descriptions[i] if i < len(exp_descriptions) else ''
                experience_list.append({
                    'title': exp_titles[i],
                    'company': exp_companies[i],
                    'duration': exp_durations[i] if i < len(exp_durations) else '',
                    'description': [d.strip() for d in raw_desc.split('\n') if d.strip()]
                })
        updated_content['experience'] = experience_list
        
        # Education (arrays)
        edu_degrees = request.POST.getlist('edu_degree[]')
        edu_institutions = request.POST.getlist('edu_institution[]')
        edu_years = request.POST.getlist('edu_year[]')
        
        education_list = []
        for i in range(len(edu_degrees)):
            if edu_degrees[i].strip() or edu_institutions[i].strip():
                education_list.append({
                    'degree': edu_degrees[i],
                    'institution': edu_institutions[i],
                    'year': edu_years[i] if i < len(edu_years) else ''
                })
        updated_content['education'] = education_list
        
        # Projects (arrays)
        proj_names = request.POST.getlist('proj_name[]')
        proj_desc = request.POST.getlist('proj_description[]')
        proj_urls = request.POST.getlist('proj_url[]')
        projects_list = []
        for i in range(len(proj_names)):
            if proj_names[i].strip():
                raw_desc = proj_desc[i] if i < len(proj_desc) else ''
                projects_list.append({
                    'name': proj_names[i],
                    'description': [d.strip() for d in raw_desc.split('\n') if d.strip()],
                    'url': proj_urls[i] if i < len(proj_urls) else ''
                })
        updated_content['projects'] = projects_list

        # Certifications (arrays)
        cert_names = request.POST.getlist('cert_name[]')
        cert_issuers = request.POST.getlist('cert_issuer[]')
        cert_dates = request.POST.getlist('cert_date[]')
        cert_urls = request.POST.getlist('cert_url[]')
        cert_list = []
        for i in range(len(cert_names)):
            if cert_names[i].strip():
                cert_list.append({
                    'name': cert_names[i],
                    'issuer': cert_issuers[i] if i < len(cert_issuers) else '',
                    'date': cert_dates[i] if i < len(cert_dates) else '',
                    'url': cert_urls[i] if i < len(cert_urls) else ''
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
                        'description': [d.strip() for d in raw_desc.split('\n') if d.strip()]
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
            gap_analysis = resume.gap_analysis
            job = gap_analysis.job
            new_content = generate_resume_content(profile, job, gap_analysis)
            new_score = calculate_ats_score(new_content, job.extracted_skills)
            # Preserve user's template choice across regeneration
            if resume.content.get('template_name'):
                new_content['template_name'] = resume.content['template_name']
            resume.content = new_content
            resume.ats_score = new_score
            resume.save()
            logger.info(f"Auto-regenerated resume {resume.id} from updated profile")
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
                desc = item.get('description')
                if desc is None:
                    item['description'] = ''
                elif isinstance(desc, list):
                    item['description'] = '\n'.join(str(d) for d in desc if d)

    # Overlay the modified content back onto the resume object specifically for the template
    resume.content = form_content

    return render(request, 'resumes/edit.html', {'resume': resume})

@login_required
def export_pdf_view(request, resume_id):
    """Export resume as PDF"""
    resume = get_object_or_404(GeneratedResume, id=resume_id)

    # Authorization check
    if resume.gap_analysis.job.user != request.user:
        raise Http404
        
    _normalize_legacy_resume_content(resume)

    fd, output_path = tempfile.mkstemp(suffix='.pdf')
    os.close(fd)

    try:
        template_name = resume.content.get('template_name', 'standard')
        generate_pdf(resume, output_path, template_name)
        # Read into memory so we can delete the temp file safely
        with open(output_path, 'rb') as f:
            pdf_data = f.read()
    except Exception as e:
        logger.exception("PDF export failed for resume %s", resume_id)
        return HttpResponse("PDF generation failed. Please try again.", status=500)
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
    """View and manage all generated resumes"""
    resumes = GeneratedResume.objects.filter(
        gap_analysis__job__user=request.user
    ).select_related('gap_analysis__job').order_by('-created_at')
    
    return render(request, 'resumes/list.html', {'resumes': resumes})

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
