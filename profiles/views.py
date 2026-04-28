from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.db import close_old_connections, transaction
from django.views.decorators.http import require_POST
from .models import UserProfile, JobProfileSnapshot
from .services.cv_parser import parse_cv
from .services.llm_validator import validate_and_map_cv_data, get_missing_fields
from .services.interviewer import process_chat_turn
from .services.outreach_generator import generate_outreach_campaign
from .services.github_aggregator import fetch_github_snapshot, parse_github_username
from .services.linkedin_aggregator import make_linkedin_snapshot
from .services.scholar_aggregator import fetch_scholar_snapshot
from .services.kaggle_aggregator import fetch_kaggle_snapshot

from jobs.models import Job, RecommendedJob
from core.services.action_planner import get_recommended_actions
from core.services.career_stage import detect_stage_for_dashboard
from django.contrib import messages
import json
import logging
import traceback

MAX_CV_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_CV_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt'}

logger = logging.getLogger(__name__)


def _build_profile_form_context(profile):
    """Build the common context dict for manual-form and review-profile views."""
    standard_keys = {
        'full_name', 'email', 'phone', 'location', 'linkedin_url', 'github_url',
        'skills', 'experiences', 'education', 'projects', 'certifications',
        'normalized_summary', 'summary'
    }
    
    extra_sections = {}
    
    # Build dynamic contact links from existing fields
    contact_links = []
    if profile.linkedin_url:
        contact_links.append({"platform": "LinkedIn", "url": profile.linkedin_url})
    if profile.github_url:
        contact_links.append({"platform": "GitHub", "url": profile.github_url})
        
    # See if they have other links in data_content (like portfolio, twitter, etc.)
    if profile.data_content:
        for key, value in profile.data_content.items():
            if key in ['portfolio', 'website', 'twitter', 'blog'] and value and isinstance(value, str) and value.startswith('http'):
                # Capitalize platform name slightly
                platform_name = key.title()
                contact_links.append({"platform": platform_name, "url": value})
            elif key not in standard_keys and value and isinstance(value, list) and not isinstance(value, str):
                extra_sections[key] = value

    return {
        'profile': profile,
        'skills_json': json.dumps(profile.skills or []),
        'experiences_json': json.dumps(profile.experiences or []),
        'education_json': json.dumps(profile.education or []),
        'projects_json': json.dumps(profile.projects or []),
        'certifications_json': json.dumps(profile.certifications or []),
        'extra_sections_json': json.dumps(extra_sections),
        'contact_links_json': json.dumps(contact_links),
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

        # Validate file
        ext = cv_file.name.rsplit('.', 1)[-1].lower() if '.' in cv_file.name else ''
        if ext not in ALLOWED_CV_EXTENSIONS:
            messages.error(request, "Unsupported file type. Please upload a PDF, DOCX, or TXT file.")
            return render(request, 'profiles/upload_cv.html', {'job': job})
        if cv_file.size > MAX_CV_SIZE:
            messages.error(request, "File too large. Maximum size is 10 MB.")
            return render(request, 'profiles/upload_cv.html', {'job': job})

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
            logger.exception(f"CV parsing/validation failed: {e}")
            messages.error(request, "We couldn't parse your CV automatically. Please enter your details manually.")
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

        # JSON fields
        try:
            if request.POST.get('contact_links_json'):
                contact_links = json.loads(request.POST.get('contact_links_json'))
                # Map standard ones back
                profile.linkedin_url = ""
                profile.github_url = ""
                
                for link in contact_links:
                    platform = link.get('platform', '').strip().lower()
                    url = link.get('url', '').strip()
                    
                    if platform == 'linkedin':
                        profile.linkedin_url = url
                    elif platform == 'github':
                        profile.github_url = url
                    else:
                        # Stash unknown links in data_content so they aren't lost
                        profile.data_content[platform] = url

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
    """Chatbot interface — stores pre-chatbot profile snapshot for scope control"""
    job = get_object_or_404(Job, id=job_id, user=request.user)
    
    # Save pre-chatbot profile snapshot to session for potential rollback
    try:
        profile = UserProfile.objects.get(user=request.user)
        request.session[f'pre_chatbot_data_{job_id}'] = json.dumps(profile.data_content, default=str)
    except UserProfile.DoesNotExist:
        pass
    
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
            
            # Process turn using single unified function
            
            result = process_chat_turn(
                user_id=request.user.id,
                job_id=job_id,
                user_reply=user_message,
                conversation_history=conversation_history
            )
            
            if result.get('error'):
                 return JsonResponse({
                     'error': result['error'],
                     'recoverable': result.get('recoverable', False),
                 }, status=400)
                 
            if result.get('needs_clarification'):
                 return JsonResponse({
                     'message': result.get('clarification_prompt', 'Could you provide more detail?'),
                     'topic': 'clarification',
                     'complete': False
                 })
                 
            gap_score = None
            if result.get('profile_updated'):
                try:
                    # Direct synchronous computation
                    from analysis.tasks import compute_gap_analysis_task
                    compute_gap_analysis_task(job_id, request.user.id)
                    gap_score = None  # UI will refresh profile and score normally
                except Exception as e:
                    logger.error(f"Failed to update gap analysis: {e}")
                    
            if result.get('is_complete'):
                return JsonResponse({
                    'message': result.get('next_question', 'Excellent! You have all the key skills for this role.'),
                    'topic': 'completion',
                    'complete': True,
                    'profile_updated': result.get('profile_updated', False) if user_message else False,
                    'gap_score': gap_score,
                    # Send the user to generate a tailored resume — the natural
                    # next step after filling skill gaps. Previously this routed
                    # to the manual form, which was confusing.
                    'redirect_url': reverse('generate_resume', kwargs={'job_id': job_id})
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
            return JsonResponse({'error': 'Something went wrong. Please try again.'}, status=500)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)

@login_required
def upload_master_profile(request):
    """Phase 1: Master Profile Upload (Step 1)"""
    if request.method == 'POST' and request.FILES.get('cv_file'):
        cv_file = request.FILES['cv_file']

        # Validate file
        ext = cv_file.name.rsplit('.', 1)[-1].lower() if '.' in cv_file.name else ''
        if ext not in ALLOWED_CV_EXTENSIONS:
            messages.error(request, "Unsupported file type. Please upload a PDF, DOCX, or TXT file.")
            return render(request, 'profiles/upload_cv.html', {'is_master': True})
        if cv_file.size > MAX_CV_SIZE:
            messages.error(request, "File too large. Maximum size is 10 MB.")
            return render(request, 'profiles/upload_cv.html', {'is_master': True})

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
            # Onboarding order: parsed CV → Connect accounts → Project review
            # → Master review (the user reviews ONCE, after external evidence
            # has been pulled and reconciled). Out-of-onboarding visits jump
            # straight to review since there's no signals step in their flow.
            if request.session.get('in_onboarding'):
                return redirect('connect_accounts')
            return redirect('review_master_profile')

        except Exception as e:
            logger.error("Master Profile Parsing Failed: %s", e)
            messages.warning(request, "We saved your CV but couldn't auto-fill everything. Please review and complete the fields below.")
            # Parse-failure still respects the onboarding order — the user
            # can connect accounts first and review the (sparse) parsed
            # output once at the end.
            if request.session.get('in_onboarding'):
                return redirect('connect_accounts')
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

        # Update JSON Fields
        try:
            if request.POST.get('contact_links_json'):
                contact_links = json.loads(request.POST.get('contact_links_json'))
                profile.linkedin_url = ""
                profile.github_url = ""
                
                for link in contact_links:
                    platform = link.get('platform', '').strip().lower()
                    url = link.get('url', '').strip()
                    
                    if platform == 'linkedin':
                        profile.linkedin_url = url
                    elif platform == 'github':
                        profile.github_url = url
                    else:
                        profile.data_content[platform] = url
                        
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
        messages.success(request, "Profile saved successfully.")
        # Master review is now the FINAL step of onboarding (post-reorder).
        # The connect-accounts + project-review steps already ran before
        # the user landed here, so nothing's left except routing to the
        # natural next action. Drop the onboarding session flag so
        # downstream pages stop showing the "Skip onboarding" affordance.
        if request.session.get('in_onboarding'):
            request.session.pop('in_onboarding', None)
        has_jobs = Job.objects.filter(user=request.user).exists()
        if not has_jobs:
            return redirect('job_input_view')
        return redirect('dashboard')

    context = _build_profile_form_context(profile)
    context['is_master'] = True
    
    # Career Snapshot stats. YoE uses month-precision parsing + overlap
    # merging; see profiles/services/experience_math.py for the design.
    from profiles.services.experience_math import compute_years_of_experience
    total_yoe = compute_years_of_experience(profile.experiences)

    context['summary_stats'] = {
        'total_yoe': total_yoe,
        'skills_count': len(profile.skills or []),
        'projects_count': len(profile.projects or []),
        'education_count': len(profile.education or []),
    }
    
    return render(request, 'profiles/manual_form.html', context)


@login_required
def connect_accounts_view(request):
    """Onboarding step 2 of 3: let a fresh signup paste their GitHub /
    LinkedIn / Scholar / Kaggle handles so the first gap analysis has
    real external signals to lean on.

    Reached automatically from review_master_profile when the session flag
    `in_onboarding` is set. Non-onboarding users can still visit this
    page directly (e.g., from Settings -> Connect accounts), but nothing
    redirects them through it automatically.

    POST (Continue) routes based on whether the user has any jobs yet —
    same logic as review_master_profile's success redirect so either flow
    (CV upload then review, or build-by-form then review) ends in the
    same place.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        # When external signals are connected, silently auto-merge the
        # enriched projects into the master profile and drop the user
        # straight at the master-review page. We deliberately do NOT
        # detour through /profiles/projects/review/ — the dedupe step
        # is hands-off by design (per user direction): the LLM's verdict
        # is authoritative and overrides aren't worth a manual review
        # screen. The page itself stays reachable for inspection but
        # is no longer a step in the flow.
        data = profile.data_content or {}

        def _has_signal(key):
            blob = data.get(key) or {}
            return bool(blob) and not blob.get('error')

        has_signals = (
            _has_signal('github_signals')
            or _has_signal('scholar_signals')
            or _has_signal('kaggle_signals')
        )
        if has_signals:
            try:
                from profiles.services.project_dedupe import auto_apply_enriched_projects
                summary = auto_apply_enriched_projects(profile)
                logger.info(
                    "connect_accounts: auto-merged enriched projects (added=%d, "
                    "merged=%d, kept_existing=%d, kept_new=%d, final=%d)",
                    summary['added_new'], summary['merged'],
                    summary['kept_existing'], summary['kept_new'],
                    summary['final_count'],
                )
                # One-shot session flag so the master-review page can
                # surface a small "we added X projects from your signals"
                # banner without requiring a separate confirm step.
                if summary['added_new'] or summary['merged']:
                    request.session['projects_auto_merged'] = summary
            except Exception:
                logger.exception("Auto-apply of enriched projects failed; continuing without merge.")

        if request.session.get('in_onboarding'):
            return redirect('review_master_profile')

        # Out-of-onboarding (settings-style visit): keep going to the
        # natural next step (job input, or dashboard if a job already exists).
        has_jobs = Job.objects.filter(user=request.user).exists()
        if not has_jobs:
            return redirect('job_input_view')
        return redirect('dashboard')

    return render(request, 'profiles/connect_accounts.html', {'profile': profile})


@login_required
def dashboard(request):
    """Phase 2: The Command Center with Analytics & Kanban"""
    # Reaching the dashboard naturally ends the onboarding journey — drop
    # the session flag so subsequent pages (job input etc.) stop showing
    # the "Skip onboarding" button.
    if request.session.get('in_onboarding'):
        request.session.pop('in_onboarding', None)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Fetch all jobs for the user ONCE — annotated with resume count so the
    # kanban tile can show "N résumés" per job without a query-per-card.
    # The chain is Job → GapAnalysis → GeneratedResume, so we count through.
    from django.db.models import Count
    jobs = list(
        Job.objects.filter(user=request.user)
        .annotate(resume_count=Count('gap_analyses__resumes', distinct=True))
        .order_by('-created_at')
    )
    
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
    
    # Onboarding logic. The "two steps" banner gates on profile-complete +
    # has-jobs (objective need) AND on the user not having explicitly
    # dismissed it (subjective preference). The dismiss flag is set by the
    # welcome page's "Just show me around" action and by the X button on
    # the banner itself (POST to dismiss_onboarding_banner_view).
    profile_complete = bool(profile.full_name and profile.skills)
    has_jobs = total_applications > 0
    banner_dismissed = bool((profile.data_content or {}).get('onboarding_banner_dismissed'))
    show_onboarding = not (profile_complete and has_jobs) and not banner_dismissed
    
    # AI-recommended next steps
    next_actions = get_recommended_actions(request.user)

    # Career stage — drives the stage-aware dashboard hero.
    # Reframes the dashboard from "here's an artifact you can make" to
    # "here's what this moment in your career needs next."
    career_stage = detect_stage_for_dashboard(profile, kanban_boards)
    from profiles.services.profile_strength import compute_profile_strength
    profile_strength = compute_profile_strength(profile, request.user)

    # Shepherd tour gating (Tier 4 / S7). Auto-trigger once per user. The
    # Help "?" button on every page can re-run the tour any time via
    # startTour('dashboard', {force: true}), which doesn't reset this flag.
    should_run_tour = not bool((profile.data_content or {}).get('has_seen_tour'))

    context = {
        'profile': profile,
        'kanban_boards': kanban_boards,
        'total_applications': total_applications,
        'top_skills': top_skills,
        'recommended_jobs': recommended_jobs,
        'profile_complete': profile_complete,
        'has_jobs': has_jobs,
        'show_onboarding': show_onboarding,
        'next_actions': next_actions,
        'career_stage': career_stage,
        'profile_strength': profile_strength,
        'should_run_tour': should_run_tour,
    }
    return render(request, 'profiles/dashboard.html', context)


@login_required
def get_current_profile(request):
    """API endpoint to fetch current user profile for live updates.

    Includes a `profile_strength` object from the same
    `compute_profile_strength` service that drives /profiles/dashboard/
    and /insights/, so every place in the UI sees one canonical score
    (instead of the chatbot's old 9-field checklist that maxed at 100
    as soon as the basics were filled in).
    """
    from profiles.services.profile_strength import compute_profile_strength
    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return JsonResponse({
            'full_name': '',
            'email': request.user.email,
            'skills': [],
            'experiences': [],
            'education': [],
            'projects': [],
            'profile_strength': {'score': 0, 'tier': 'Weak'},
        })

    strength = compute_profile_strength(profile, request.user)
    return JsonResponse({
        'full_name': profile.full_name or '',
        'email': profile.email or '',
        'phone': profile.phone or '',
        'location': profile.location or '',
        'skills': profile.skills or [],
        'experiences': profile.experiences or [],
        'education': profile.education or [],
        'projects': profile.projects or [],
        'profile_strength': {
            'score': strength['score'],
            'tier': strength['tier'],
        },
    })


@login_required
def chatbot_scope_decision(request, job_id):
    """Handle user's decision about chatbot profile scope: master vs. job-only"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        scope = data.get('scope')
        
        if scope != 'job_only':
            return JsonResponse({'error': 'Invalid scope value'}, status=400)
        
        profile = get_object_or_404(UserProfile, user=request.user)
        job = get_object_or_404(Job, id=job_id, user=request.user)
        
        # Get pre-chatbot snapshot from session
        session_key = f'pre_chatbot_data_{job_id}'
        pre_chatbot_json = request.session.get(session_key)
        
        if not pre_chatbot_json:
            logger.warning("No pre-chatbot snapshot found in session for job %s", job_id)
            return JsonResponse({'error': 'No pre-chatbot snapshot available'}, status=400)
        
        pre_chatbot_data = json.loads(pre_chatbot_json)
        
        # Save job-specific snapshot (current profile state = what chatbot created)
        JobProfileSnapshot.objects.update_or_create(
            profile=profile,
            job=job,
            defaults={
                'data_content': profile.data_content,  # Current (post-chatbot) state
                'pre_chatbot_data': pre_chatbot_data,   # Original state before chatbot
            }
        )
        
        # Revert master profile to pre-chatbot state
        profile.data_content = pre_chatbot_data
        profile.save()

        # Invalidate stale gap analysis (was computed with chatbot-enhanced profile)
        from analysis.models import GapAnalysis
        GapAnalysis.objects.filter(job=job, user=request.user).delete()

        # Clean session
        del request.session[session_key]
        
        logger.info("Profile scope: job_only. Master profile reverted, snapshot saved for job %s", job_id)
        return JsonResponse({'success': True})
        
    except Exception as e:
        logger.exception("Scope decision failed: %s", e)
        return JsonResponse({'error': 'Something went wrong. Please try again.'}, status=500)

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


_TITLE_STOPWORDS = {
    'senior', 'sr', 'junior', 'jr', 'lead', 'principal', 'staff',
    'engineer', 'developer', 'manager', 'analyst', 'specialist', 'associate',
    'and', '&', 'the', 'of', 'for', 'with', 'a', 'an',
    'i', 'ii', 'iii', 'iv',
}


def _role_keywords(title: str, max_kw: int = 2) -> list:
    """Pull the meaningful nouns out of a job title for SERP filtering.

    'AI & Tooling Engineer, LIGHT' -> ['AI', 'Tooling']  (drops Engineer, &, LIGHT)
    Using too many keywords narrows Google results to zero on small companies,
    so cap at 2.
    """
    if not title:
        return []
    cleaned = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in title)
    out = []
    for word in cleaned.split():
        lw = word.lower()
        if lw in _TITLE_STOPWORDS or len(word) <= 1 or word.isdigit():
            continue
        out.append(word)
        if len(out) >= max_kw:
            break
    return out


@login_required
def outreach_campaign_view(request, job_id):
    """Render the outreach automation campaign builder for a single job."""
    from jobs.services.people_finder import (
        find_hiring_team,
        find_peers_via_google,
        google_search_url,
    )
    from profiles.services.outreach_generator import generate_outreach_for_target
    from profiles.models import DiscoveredTarget, OutreachCampaign

    job = get_object_or_404(Job, id=job_id, user=request.user)
    profile = get_object_or_404(UserProfile, user=request.user)

    # v2: targets the paired Chrome extension scraped from a logged-in
    # LinkedIn job page. Independent of the (broken) server-side discovery —
    # they're a separate "candidate" pool the user can promote to drafts via
    # the existing "Add manually" path with one click.
    extension_targets = list(
        DiscoveredTarget.objects.filter(user=request.user, job=job)
        .values('handle', 'name', 'role', 'source', 'discovered_at')
    )
    for t in extension_targets:
        t['discovered_at'] = t['discovered_at'].isoformat() if t['discovered_at'] else None

    # POST = "discover + draft" round trip (sync). Campaign creation itself
    # goes through the JSON endpoint /api/outreach/campaigns/.
    discovered = []
    drafts = {}
    diagnostics = None  # {'ran': bool, 'hiring_team_count': N, 'google_count': N}
    if request.method == 'POST':
        hiring_team = find_hiring_team(job.url) if job.url else []
        role_keywords = _role_keywords(job.title or '')
        google_results = find_peers_via_google(job.company or '', role_keywords, n=8)
        # If specific keywords return nothing, retry once with company-only —
        # narrow searches kill recall on small companies more often than helps
        # signal at SmartCV scale.
        if not google_results and role_keywords:
            google_results = find_peers_via_google(job.company or '', [], n=8)

        diagnostics = {
            'ran': True,
            'job_url_present': bool(job.url),
            'hiring_team_count': len(hiring_team),
            'google_count': len(google_results),
            'role_keywords': role_keywords,
        }

        # de-dupe on handle, hiring_team first so it wins on ties
        combined = list(hiring_team) + list(google_results)
        seen = set()
        unique = []
        for target in combined:
            if target.handle in seen:
                continue
            seen.add(target.handle)
            unique.append(target)
        discovered = unique[:10]
        for target in discovered:
            drafts[target.handle] = generate_outreach_for_target(profile, job, target)

    fallback_search_url = google_search_url(
        job.company or '', _role_keywords(job.title or '') or [job.title or ''],
    )
    active_campaign = OutreachCampaign.objects.filter(
        user=request.user, job=job, status__in=['running', 'paused']
    ).order_by('-created_at').first()

    return render(request, 'profiles/outreach_campaign.html', {
        'job': job,
        'profile': profile,
        'discovered': [t.to_dict() for t in discovered],
        'drafts': drafts,
        'fallback_search_url': fallback_search_url,
        'active_campaign': active_campaign,
        'diagnostics': diagnostics,
        'extension_targets': extension_targets,
    })


@login_required
def refresh_github_signals(request):
    """Fetch a fresh GitHub snapshot for the user's profile and cache it.

    POST { github_input?: "username | URL" } — if omitted, falls back to the
    profile.github_url already on file. Stores the snapshot in
    profile.data_content['github_signals'] and returns it as JSON.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    raw = (request.POST.get('github_input') or '').strip()
    if not raw and profile.github_url:
        raw = profile.github_url

    username = parse_github_username(raw)
    if not username:
        return JsonResponse({
            'error': 'Could not parse a GitHub username from that input.',
        }, status=400)

    snapshot = fetch_github_snapshot(username)

    # Persist the URL so subsequent refreshes don't need it re-pasted, and
    # cache the snapshot in JSONB so the dashboard can render without a fetch.
    profile.github_url = snapshot.get('profile_url') or profile.github_url
    data = profile.data_content or {}
    data['github_signals'] = snapshot
    profile.data_content = data
    profile.save(update_fields=['github_url', 'data_content', 'updated_at'])

    return JsonResponse({'success': not snapshot.get('error'), 'snapshot': snapshot})


def _refresh_signal(request, *, signal_key: str, input_field: str, fetcher,
                    fallback_url_attr: str = None):
    """Shared helper for signal-aggregation refresh endpoints.

    `fetcher(value)` must return a snapshot dict. The result is cached on
    profile.data_content[signal_key] and returned as JSON.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    raw = (request.POST.get(input_field) or '').strip()
    if not raw and fallback_url_attr:
        raw = getattr(profile, fallback_url_attr, '') or ''

    snapshot = fetcher(raw)

    data = profile.data_content or {}
    data[signal_key] = snapshot
    profile.data_content = data
    update_fields = ['data_content', 'updated_at']

    # Persist canonical URL on the model too, when available.
    if fallback_url_attr and snapshot.get('profile_url'):
        setattr(profile, fallback_url_attr, snapshot['profile_url'])
        update_fields.insert(0, fallback_url_attr)

    profile.save(update_fields=update_fields)
    return JsonResponse({'success': not snapshot.get('error'), 'snapshot': snapshot})


@login_required
def refresh_linkedin_signals(request):
    """Validate a LinkedIn URL/handle and store it. No scraping (LinkedIn
    blocks public profile data behind auth). See linkedin_aggregator docstring."""
    return _refresh_signal(
        request,
        signal_key='linkedin_signals',
        input_field='linkedin_input',
        fetcher=make_linkedin_snapshot,
        fallback_url_attr='linkedin_url',
    )


@login_required
def refresh_scholar_signals(request):
    """Scrape a Google Scholar profile (citations, h-index, top publications).
    May fail if Scholar serves a CAPTCHA — snapshot.error indicates this."""
    return _refresh_signal(
        request,
        signal_key='scholar_signals',
        input_field='scholar_input',
        fetcher=fetch_scholar_snapshot,
    )


@login_required
def refresh_kaggle_signals(request):
    """Scrape a Kaggle profile (tier, competitions, datasets, notebooks, medals)
    by parsing the embedded __NEXT_DATA__ JSON blob."""
    return _refresh_signal(
        request,
        signal_key='kaggle_signals',
        input_field='kaggle_input',
        fetcher=fetch_kaggle_snapshot,
    )


@login_required
@require_POST
def dismiss_tour_view(request):
    """Persist that the user has seen (or dismissed) the Shepherd tour.

    Set on cancel + complete by the client-side tour wrapper. Force-replays
    triggered from the "?" Help button do NOT clear this flag — those are
    replays, not first-time runs. Keeps the auto-trigger from firing twice.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    data = profile.data_content or {}
    data['has_seen_tour'] = True
    profile.data_content = data
    profile.save(update_fields=['data_content', 'updated_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def dismiss_onboarding_banner_view(request):
    """Persist the user's dismissal of the dashboard "two steps to unlock"
    banner so it doesn't reappear on every dashboard visit.

    Idempotent. Returns JSON for the client; the banner is hidden via JS
    immediately on click without waiting for the response.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    data = profile.data_content or {}
    data['onboarding_banner_dismissed'] = True
    profile.data_content = data
    profile.save(update_fields=['data_content', 'updated_at'])
    return JsonResponse({'ok': True})


@login_required
def projects_review_view(request):
    """Read-only summary of auto-merged enriched projects.

    Per user direction: the dedupe step is hands-off — there's no
    confirm form, no action dropdowns, no opt-in checkboxes. On every
    visit we silently re-run enrichment + dedupe + apply (idempotent;
    cached on the profile via enrich_profile's hash) and then render a
    summary of what got merged. The page is now informational, not
    transactional.

    Use cases retained: a user wants to see what GitHub / Scholar /
    Kaggle entries got pulled in as projects; a user wants to force a
    re-merge after refreshing signals (?force=1).
    """
    from profiles.services.project_enricher import enrich_profile
    from profiles.services.project_dedupe import auto_apply_enriched_projects, dedupe_projects

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    force = request.GET.get('force') == '1'

    has_signals = bool(
        (profile.data_content or {}).get('github_signals')
        or (profile.data_content or {}).get('scholar_signals')
        or (profile.data_content or {}).get('kaggle_signals')
    )
    if has_signals:
        # Force=1 bypasses the enrichment cache; without it, this is
        # essentially a free re-render after the first auto-merge.
        if force:
            enrich_profile(profile, force=True)
        try:
            summary = auto_apply_enriched_projects(profile)
        except Exception:
            logger.exception("projects_review: auto-apply failed")
            summary = None
    else:
        summary = None

    # Refresh local view of the now-merged state for rendering.
    enriched = (profile.data_content or {}).get('enriched_projects_cache') or []
    decisions = (profile.data_content or {}).get('dedupe_decisions') or []
    final_projects = (profile.data_content or {}).get('projects') or []

    # Build per-row view objects for the read-only summary.
    matched_rows = []
    new_rows = []
    for d in decisions:
        e_idx = d.get('enriched_index', -1)
        if e_idx < 0 or e_idx >= len(enriched):
            continue
        row = {'decision': d, 'enriched': enriched[e_idx]}
        if d.get('action') == 'add_new':
            new_rows.append(row)
        else:
            matched_rows.append(row)

    return render(request, 'profiles/projects_review.html', {
        'matched_rows': matched_rows,
        'new_rows': new_rows,
        'final_projects': final_projects,
        'summary': summary,
        'has_signals': has_signals,
        'enriched_count': len(enriched),
    })


# Note: the prior `projects_confirm_view` was removed when the dedupe
# step became fully automatic. Auto-apply now runs in
# `connect_accounts_view` POST and on every GET to `projects_review_view`.
# The URL pattern was dropped from `profiles/urls.py` in the same change.


@login_required
def enrich_from_signals_view(request):
    """Run project enrichment + dedupe over the user's stored signal blobs.

    POST: returns JSON with three lists:
      - `enriched`: every project derived from GitHub/Scholar/Kaggle
      - `decisions`: per-pair dedupe verdicts (action + confidence + reason)
      - `typed`: the user's existing typed projects (for the review UI)

    Phase 1 is JSON-only — no UI yet. The endpoint runs the LLM calls and
    caches enrichment results, but does NOT mutate `data_content['projects']`.
    Phase 2's review UI will let the user accept/override the verdicts and
    persist the final pool to `data_content['confirmed_projects']`.

    Bypasses the enrichment cache when the request body has `force=1`.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    from profiles.services.project_enricher import enrich_profile
    from profiles.services.project_dedupe import dedupe_projects

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    force = bool(request.POST.get('force')) or request.GET.get('force') == '1'

    # Run enrichment (cached unless force=True). Mutates profile.data_content
    # in-memory but doesn't save — we save once after dedupe so the cache and
    # decision history land in one transaction.
    enriched = enrich_profile(profile, force=force)

    typed_projects = (profile.data_content or {}).get('projects') or []
    decisions = dedupe_projects(typed_projects, enriched)

    # Persist the enrichment cache + the latest dedupe decisions so the
    # Phase 2 review UI can pick up where this left off without re-running.
    data = profile.data_content or {}
    data['dedupe_decisions'] = decisions
    profile.data_content = data
    profile.save(update_fields=['data_content', 'updated_at'])

    return JsonResponse({
        'enriched': enriched,
        'decisions': decisions,
        'typed': typed_projects,
    })
