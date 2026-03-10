from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.db import close_old_connections, transaction
from .models import UserProfile
from .services.cv_parser import parse_cv
from .services.chatbot import chat_with_user, extract_profile_from_conversation
from .services.llm_validator import validate_and_map_cv_data, get_missing_fields
from .services.interviewer import process_chat_turn
from .services.outreach_generator import generate_outreach_campaign
from analysis.services.gap_analyzer import compute_gap_analysis
from jobs.models import Job, RecommendedJob
import json
import logging

logger = logging.getLogger(__name__)


def _build_profile_form_context(profile):
    """Build the common context dict for manual-form and review-profile views."""
    standard_keys = {
        'full_name', 'email', 'phone', 'location', 'linkedin_url', 'github_url',
        'skills', 'experiences', 'education', 'projects', 'certifications',
        'normalized_summary', 'summary'
    }
    extra_sections = {}
    if profile.data_content:
        for key, value in profile.data_content.items():
            if key not in standard_keys and value and isinstance(value, list):
                extra_sections[key] = value

    return {
        'profile': profile,
        'skills_json': json.dumps(profile.skills or []),
        'experiences_json': json.dumps([
            {
                'title': e.get('position') or e.get('title', ''),
                'company': e.get('company', ''),
                'duration': e.get('duration') or f"{e.get('start_date', '')} - {e.get('end_date') or 'Present'}",
                'description': e.get('description') or '\n'.join(e.get('responsibilities', []))
            } for e in (profile.experiences or [])
        ]),
        'education_json': json.dumps([
            {
                'degree': e.get('degree', ''),
                'institution': e.get('institution', ''),
                'year': str(e.get('graduation_year') or e.get('graduation_date') or e.get('year', ''))
            } for e in (profile.education or [])
        ]),
        'projects_json': json.dumps(profile.projects or []),
        'certifications_json': json.dumps(profile.certifications or []),
        'extra_sections_json': json.dumps(extra_sections),
        'full_json': json.dumps(profile.data_content, indent=2, default=str),
    }

@login_required
def profile_input_choice(request, job_id):
    """Choose how to input profile data"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    return render(request, 'profiles/input_choice.html', {'job': job})

@login_required
@transaction.atomic
def profile_upload_cv(request, job_id):
    """Upload and parse CV with LLM validation"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    if request.method == 'POST' and request.FILES.get('cv_file'):
        cv_file = request.FILES['cv_file']
        
        # Save file temporarily
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.uploaded_cv = cv_file
        profile.input_method = 'upload'
        profile.save()
        
        # Parse CV
        try:
            # Step 1: Parse CV with existing parser
            parsed_data = parse_cv(profile.uploaded_cv.path)
            
            # Step 2: Get extracted text (Avoid reading PDF binary as text)
            raw_cv_text = parsed_data.get('raw_text', '')
            if not raw_cv_text and profile.uploaded_cv:
                 try:
                     # Fallback: if parser failed to return text, try reading if it's a txt file
                     if profile.uploaded_cv.name.lower().endswith('.txt'):
                         with open(profile.uploaded_cv.path, 'r', encoding='utf-8', errors='ignore') as f:
                             raw_cv_text = f.read()
                 except: 
                     raw_cv_text = ""
            
            # Step 3: LLM validation and enhancement (extracts ALL sections)
            logger.info("Running LLM validation - extracting ALL CV sections...")
            validated_data = validate_and_map_cv_data(parsed_data, raw_cv_text)
            
            # Step 4: Store COMPLETE CV data (no data loss!)
            profile.data_content = validated_data
            sections = list(validated_data.keys())
            logger.info(f"Stored complete CV with sections: {', '.join(sections)}")
            
            # Step 5: Extract core fields for job matching & chatbot
            profile.full_name = validated_data.get('full_name', '')
            profile.email = validated_data.get('email') or request.user.email
            profile.phone = validated_data.get('phone', '')
            profile.location = validated_data.get('location', '')
            profile.linkedin_url = validated_data.get('linkedin_url', '')
            profile.github_url = validated_data.get('github_url', '')
            
            # Core structured data for matching
            profile.skills = validated_data.get('skills', [])
            profile.experiences = validated_data.get('experiences', [])
            profile.education = validated_data.get('education', [])
            profile.projects = validated_data.get('projects', [])
            profile.certifications = validated_data.get('certifications', [])
            
            profile.save()
            logger.info(f"✓ Profile saved - Core fields + complete raw_cv_data")
            
            # Step 6: Always redirect to chatbot for job-aware conversation
            return redirect('profile_chatbot', job_id=job_id)
            
        except Exception as e:
            logger.error(f"CV parsing/validation failed: {e}")
            traceback.print_exc()
            # Fallback to manual entry on error
            return redirect('profile_manual_form', job_id=job_id)
    
    return render(request, 'profiles/upload_cv.html', {'job': job})

@login_required
def profile_manual_form(request, job_id):
    """Manual form entry - Enhanced for full JSON support"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        profile.input_method = 'form'

        # Standard fields
        profile.full_name = request.POST.get('full_name')
        profile.email = request.POST.get('email')
        profile.phone = request.POST.get('phone')
        profile.location = request.POST.get('location')
        profile.linkedin_url = request.POST.get('linkedin_url')

        # JSON fields
        try:
            if request.POST.get('skills_json'):
                profile.skills = json.loads(request.POST.get('skills_json'))

            if request.POST.get('experiences_json'):
                profile.experiences = json.loads(request.POST.get('experiences_json'))

            if request.POST.get('education_json'):
                profile.education = json.loads(request.POST.get('education_json'))

            if request.POST.get('projects_json'):
                profile.projects = json.loads(request.POST.get('projects_json'))

            if request.POST.get('certifications_json'):
                profile.certifications = json.loads(request.POST.get('certifications_json'))

        except json.JSONDecodeError as e:
            logger.error("JSON Decode Error in form save: %s", e)

        profile.save()
        return redirect('gap_analysis', job_id=job_id)

    context = _build_profile_form_context(profile)
    context['job'] = job
    return render(request, 'profiles/manual_form.html', context)

@login_required
def profile_chatbot(request, job_id):
    """Chatbot interface"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    return render(request, 'profiles/chatbot.html', {'job': job})

@login_required
@transaction.atomic
def chatbot_api(request):
    """API endpoint for job-aware conversational interviewer"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)

            job_id = data.get('job_id')
            user_message = data.get('message')
            conversation_history = data.get('conversation_history', [])
            
            if not job_id:
                return JsonResponse({'error': 'job_id is required'}, status=400)
            
            close_old_connections()
            
            # Process turn using single unified function
            
            result = process_chat_turn(
                user_id=request.user.id,
                job_id=job_id,
                user_reply=user_message,
                conversation_history=conversation_history
            )
            
            if result.get('error'):
                 return JsonResponse({'error': result['error']}, status=400)
                 
            if result.get('needs_clarification'):
                 return JsonResponse({
                     'message': result.get('clarification_prompt', 'Could you provide more detail?'),
                     'topic': 'clarification',
                     'complete': False
                 })
                 
            gap_score = None
            if result.get('profile_updated'):
                try:
                    profile = UserProfile.objects.get(user=request.user)
                    job = Job.objects.get(id=job_id)
                    
                    analysis_results = compute_gap_analysis(profile, job)
                    gap_score = int(analysis_results['similarity_score'] * 100)
                except Exception as e:
                    logger.error(f"Failed to recalculate gap score: {e}")
                    
            if result.get('is_complete'):
                return JsonResponse({
                    'message': result.get('next_question', 'Excellent! You have all the key skills for this role.'),
                    'topic': 'completion',
                    'complete': True,
                    'profile_updated': result.get('profile_updated', False) if user_message else False,
                    'gap_score': gap_score,
                    'redirect_url': f'/profiles/form/{job_id}/'
                })
            else:
                return JsonResponse({
                    'message': result.get('next_question'),
                    'topic': result.get('next_topic', 'general'),
                    'complete': False,
                    'profile_updated': result.get('profile_updated', False) if user_message else False,
                    'gap_score': gap_score
                })

        except Exception as e:
            logger.exception(f"Chatbot API error: {e}")
            traceback.print_exc()
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)

@login_required
def upload_master_profile(request):
    """Phase 1: Master Profile Upload (Step 1)"""
    if request.method == 'POST' and request.FILES.get('cv_file'):
        cv_file = request.FILES['cv_file']
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.uploaded_cv = cv_file
        profile.input_method = 'upload'
        profile.save()

        try:
            # 1. Parse
            parsed_data = parse_cv(profile.uploaded_cv.path)

            # 2. Use raw text returned by the parser (not a binary re-read)
            raw_cv_text = parsed_data.get('raw_text', '')

            # 3. Validated Extraction (Gemini)
            validated_data = validate_and_map_cv_data(parsed_data, raw_cv_text)
            profile.data_content = validated_data

            # 4. Map Fields
            profile.full_name = validated_data.get('full_name', '')
            profile.email = validated_data.get('email') or request.user.email
            profile.phone = validated_data.get('phone', '')
            profile.location = validated_data.get('location', '')
            profile.linkedin_url = validated_data.get('linkedin_url', '')
            profile.github_url = validated_data.get('github_url', '')
            profile.skills = validated_data.get('skills', [])
            profile.experiences = validated_data.get('experiences', [])
            profile.education = validated_data.get('education', [])
            profile.projects = validated_data.get('projects', [])
            profile.certifications = validated_data.get('certifications', [])

            profile.save()
            return redirect('review_master_profile')

        except Exception as e:
            logger.error("Master Profile Parsing Failed: %s", e)
            return redirect('review_master_profile')  # Fallback to manual edit

    return render(request, 'profiles/upload_cv.html', {'is_master': True})


@login_required
def review_master_profile(request):
    """Phase 1: Master Profile Review (Step 2)"""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        # Update Standard Fields
        profile.full_name = request.POST.get('full_name')
        profile.email = request.POST.get('email')
        profile.phone = request.POST.get('phone')
        profile.location = request.POST.get('location')
        profile.linkedin_url = request.POST.get('linkedin_url')

        # Update JSON Fields
        try:
            if request.POST.get('skills_json'):
                profile.skills = json.loads(request.POST.get('skills_json'))
            if request.POST.get('experiences_json'):
                profile.experiences = json.loads(request.POST.get('experiences_json'))
            if request.POST.get('education_json'):
                profile.education = json.loads(request.POST.get('education_json'))
            if request.POST.get('projects_json'):
                profile.projects = json.loads(request.POST.get('projects_json'))
            if request.POST.get('certifications_json'):
                profile.certifications = json.loads(request.POST.get('certifications_json'))
        except json.JSONDecodeError as e:
            logger.error("JSON Error: %s", e)

        profile.save()
        return redirect('dashboard')

    context = _build_profile_form_context(profile)
    context['is_master'] = True
    return render(request, 'profiles/manual_form.html', context)


@login_required
def dashboard(request):
    """Phase 2: The Command Center with Analytics & Kanban"""
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    # Fetch all jobs for the user ONCE
    jobs = list(Job.objects.filter(user=request.user).order_by('-created_at'))
    
    # Kanban Board Data - grouped in Python to save 5 DB queries
    kanban_boards = {
        'Saved': [j for j in jobs if j.application_status == 'saved'],
        'Applied': [j for j in jobs if j.application_status == 'applied'],
        'Interviewing': [j for j in jobs if j.application_status == 'interviewing'],
        'Offer': [j for j in jobs if j.application_status == 'offer'],
        'Rejected': [j for j in jobs if j.application_status == 'rejected'],
    }
    
    # Analytics Data
    total_applications = len(jobs)
    
    # Calculate most common required skills
    skill_counts = {}
    for job in jobs:
        for skill in job.extracted_skills:
            skill_counts[skill] = skill_counts.get(skill, 0) + 1
            
    # Sort by count
    top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Recommended Jobs Feed
    recommended_jobs = RecommendedJob.objects.filter(user=request.user, status='new').order_by('-match_score')[:5]
    
    context = {
        'profile': profile,
        'kanban_boards': kanban_boards,
        'total_applications': total_applications,
        'top_skills': top_skills,
        'recommended_jobs': recommended_jobs,
    }
    return render(request, 'profiles/dashboard.html', context)


@login_required
def get_current_profile(request):
    """API endpoint to fetch current user profile for live updates"""
    try:
        profile = UserProfile.objects.get(user=request.user)
        return JsonResponse({
            'full_name': profile.full_name or '',
            'email': profile.email or '',
            'phone': profile.phone or '',
            'location': profile.location or '',
            'skills': profile.skills or [],
            'experiences': profile.experiences or [],
            'education': profile.education or [],
            'projects': profile.projects or [],
        })
    except UserProfile.DoesNotExist:
        return JsonResponse({
            'full_name': '',
            'email': request.user.email,
            'skills': [],
            'experiences': [],
            'education': [],
            'projects': [],
        })


@login_required
def chatbot_complete(request, job_id):
    """Complete chatbot conversation and extract profile"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            conversation_history = data.get('conversation', [])
            
            # Extract profile from conversation
            profile_data = extract_profile_from_conversation(conversation_history)
            
            # Save to database
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            profile.input_method = 'chatbot'
            
            # Update fields
            profile.full_name = profile_data.get('full_name', '')
            profile.email = profile_data.get('email', request.user.email)
            profile.phone = profile_data.get('phone', '')
            profile.location = profile_data.get('location', '')
            profile.skills = profile_data.get('skills', [])
            profile.experiences = profile_data.get('experiences', [])
            profile.education = profile_data.get('education', [])
            
            profile.save()
            
            return JsonResponse({'success': True, 'redirect_url': f'/analysis/gap/{job_id}/'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid request'}, status=400)

@login_required
def generate_outreach_view(request, job_id):
    """Generate tailored cold outreach scripts for a specific job"""
    
    job = get_object_or_404(Job, id=job_id, user=request.user)
    profile = get_object_or_404(UserProfile, user=request.user)
    
    campaign = None
    if request.method == 'POST':
        campaign = generate_outreach_campaign(profile, job)
        
    return render(request, 'profiles/outreach.html', {
        'job': job,
        'profile': profile,
        'campaign': campaign
    })
