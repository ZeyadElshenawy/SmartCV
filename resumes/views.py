from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, FileResponse, Http404
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
    """Generate tailored resume"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    # Needs a gap analysis first
    gap_analysis = get_object_or_404(GapAnalysis, job=job)
    
    # Needs a profile
    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return redirect('upload_master_profile')

    if request.method == 'POST':
        # Generate resume content using AI
        try:
            resume_content = generate_resume_content(profile, job, gap_analysis)
            ats_score = calculate_ats_score(resume_content, job.extracted_skills)
            
            # Save to database
            resume = GeneratedResume.objects.create(
                gap_analysis=gap_analysis,
                content=resume_content,
                ats_score=ats_score
            )
            
            return redirect('resume_preview', resume_id=resume.id)
            
        except Exception as e:
            return render(request, 'resumes/generate.html', {
                'job': job,
                'error': f'Failed to generate resume: {str(e)}'
            })
    
    return render(request, 'resumes/generate.html', {'job': job})

@login_required
def resume_preview_view(request, resume_id):
    """Preview generated resume with edit capabilities"""
    resume = get_object_or_404(GeneratedResume, id=resume_id)
    job = resume.gap_analysis.job
    
    # Check ownership
    if job.user != request.user:
        return redirect('dashboard')
    
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
                experience_list.append({
                    'title': exp_titles[i],
                    'company': exp_companies[i],
                    'duration': exp_durations[i] if i < len(exp_durations) else '',
                    'description': exp_descriptions[i] if i < len(exp_descriptions) else ''
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
        
        # Save to DB
        resume.content = updated_content
        resume.save()
        
        return redirect('resume_preview', resume_id=resume.id)
    
    return render(request, 'resumes/edit.html', {'resume': resume})

@login_required
def export_pdf_view(request, resume_id):
    """Export resume as PDF"""
    resume = get_object_or_404(GeneratedResume, id=resume_id)

    # Authorization check
    if resume.gap_analysis.job.user != request.user:
        raise Http404

    fd, output_path = tempfile.mkstemp(suffix='.pdf')
    os.close(fd)

    try:
        generate_pdf(resume.content, output_path)
        # Read into memory so we can delete the temp file safely
        with open(output_path, 'rb') as f:
            pdf_data = f.read()
    except Exception as e:
        logger.exception("PDF export failed for resume %s", resume_id)
        return HttpResponse(f"Error generating PDF: {e}", status=500)
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
        return HttpResponse(f"Error generating PDF: {e}", status=500)

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
            return HttpResponse(f"Error generating cover letter: {e}", status=500)
            
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
