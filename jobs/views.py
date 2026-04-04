from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from .models import Job
from .services.linkedin_scraper import scrape_linkedin_job
from .services.skill_extractor import extract_skills
import json
import logging

logger = logging.getLogger(__name__)

@login_required
def job_input_view(request):
    """Unified Job Input: URL Scraping OR Manual Text Paste"""
    if request.method == 'POST':
        input_method = request.POST.get('input_method')  # 'url' or 'text'
        
        try:
            if input_method == 'url':
                url = request.POST.get('job_url')
                logger.info("Starting job extraction for URL: %s", url)

                # Scrape job from LinkedIn
                job_data = scrape_linkedin_job(url)
                logger.info("Job scraped: %s", job_data.get('title', 'Unknown'))

                # Extract skills
                skills = extract_skills(job_data['description'])
                logger.info("Extracted %d skills", len(skills))

                # Save to database
                job = Job.objects.create(
                    user=request.user,
                    url=url,
                    title=job_data['title'],
                    company=job_data['company'],
                    description=job_data['description'],
                    raw_html=job_data['raw_html'],
                    extracted_skills=list(skills)
                )
                logger.info("Job saved with ID: %s", job.id)
                
            elif input_method == 'text':
                # Manual text paste
                title = request.POST.get('job_title', 'Untitled Position')
                company = request.POST.get('company', 'Unknown Company')
                description = request.POST.get('job_description', '')

                logger.info("Manual job input: %s at %s", title, company)

                # Extract skills from pasted description
                skills = extract_skills(description)
                logger.info("Extracted %d skills", len(skills))

                # Save to database
                job = Job.objects.create(
                    user=request.user,
                    url=None,       # No URL for manual input
                    title=title,
                    company=company,
                    description=description,
                    raw_html=None,
                    extracted_skills=list(skills)
                )
                logger.info("Job saved with ID: %s", job.id)
            
            else:
                raise ValueError("Invalid input method")

            # Redirect to Review step first (user confirms extracted data)
            return redirect('review_extracted_job', job_id=job.id)

        except Exception as e:
            logger.exception("Job extraction failed: %s", e)
            return render(request, 'jobs/input.html', {
                'error': f"Failed to process job: {str(e)}"
            })
    
    return render(request, 'jobs/input.html')

@login_required
def review_extracted_job(request, job_id):
    """Review extracted job data before gap analysis — prevents bad scraper data poisoning the flow."""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    if request.method == 'POST':
        # Update job with user-confirmed/edited data
        new_title = request.POST.get('title', job.title).strip()
        new_company = request.POST.get('company', job.company).strip()
        new_description = request.POST.get('description', job.description).strip()
        
        # Check if description was changed — re-extract skills if so
        description_changed = new_description != job.description
        
        job.title = new_title
        job.company = new_company
        job.description = new_description
        
        if description_changed:
            try:
                skills = extract_skills(new_description)
                job.extracted_skills = list(skills)
                logger.info("Re-extracted %d skills after description edit", len(job.extracted_skills))
            except Exception as e:
                logger.warning("Skill re-extraction failed: %s", e)
        
        job.save()
        logger.info("Job %s confirmed by user: %s at %s", job.id, job.title, job.company)
        
        # Now proceed to Gap Analysis
        return redirect('gap_analysis', job_id=job.id)
    
    return render(request, 'jobs/review_job.html', {'job': job})


@login_required
def job_detail_view(request, job_id):
    """Display job details and extracted skills, allow status updates"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    if request.method == 'POST':
        new_status = request.POST.get('application_status')
        valid_statuses = {choice[0] for choice in Job.STATUS_CHOICES}
        if new_status in valid_statuses and new_status != job.application_status:
            job.application_status = new_status
            job.save()
        return redirect('dashboard')
    
    return render(request, 'jobs/detail.html', {

        'job': job,
        'status_choices': Job.STATUS_CHOICES,
    })


from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_job_extension_view(request):
    """API endpoint for Chrome Extension to save jobs"""
    try:
        user = request.user
        data = request.data
        
        url = data.get('url', '')
        title = data.get('title', '')
        company = data.get('company', '')
        description = data.get('description', '')
        
        # Extract skills via LLM
        # For production this should be moved to a Celery task since it's blocking
        skills = extract_skills(description)
        
        # Save job
        job = Job.objects.create(
            user=user,
            url=url,
            title=title,
            company=company,
            description=description,
            extracted_skills=list(skills),
            application_status='saved' # Straight to Kanban board
        )
        
        return JsonResponse({
            'success': True,
            'job_id': str(job.id),
            'message': 'Job saved to SmartCV Kanban board'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def update_job_status_api(request):
    """
    Lightweight endpoint for Kanban drag-and-drop status updates.
    Expects POST with job_id and new_status.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)

    job_id = request.POST.get('job_id')
    new_status = request.POST.get('new_status')

    if not job_id or not new_status:
        return JsonResponse({'error': 'Missing job_id or new_status'}, status=400)

    valid_statuses = {choice[0] for choice in Job.STATUS_CHOICES}
    if new_status not in valid_statuses:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    try:
        job = Job.objects.get(id=job_id, user=request.user)
    except Job.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    job.application_status = new_status
    job.save(update_fields=['application_status'])

    return JsonResponse({'success': True})
