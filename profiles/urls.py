from django.urls import path
from . import views, views_outreach_api as outreach_api

urlpatterns = [
    # Outreach automation API (token-authed for the Chrome extension; session-authed for the web UI)
    path('api/outreach/next', outreach_api.outreach_next, name='outreach_next'),
    path('api/outreach/result/<uuid:action_id>/', outreach_api.outreach_result, name='outreach_result'),
    path('api/outreach/discovery/push/', outreach_api.discovery_push, name='outreach_discovery_push'),
    path('api/outreach/discovery/<uuid:job_id>/', outreach_api.discovery_list, name='outreach_discovery_list'),
    path('api/outreach/campaigns/', outreach_api.create_campaign, name='outreach_create_campaign'),
    path('api/outreach/draft-target/', outreach_api.draft_manual_target, name='outreach_draft_manual_target'),
    path('api/outreach/campaigns/<uuid:campaign_id>/pause/', outreach_api.pause_campaign, name='outreach_pause_campaign'),
    path('api/outreach/campaigns/<uuid:campaign_id>/retry/', outreach_api.retry_failed_actions, name='outreach_retry_failed'),
    path('api/outreach/campaigns/<uuid:campaign_id>/status/', outreach_api.campaign_status, name='outreach_campaign_status'),
    path('extension/pair/', outreach_api.pairing_view, name='outreach_pairing'),


    # Job-Specific Flows
    path('upload/<uuid:job_id>/', views.profile_upload_cv, name='profile_upload_cv'),
    path('form/<uuid:job_id>/', views.profile_manual_form, name='profile_manual_form'),
    
    # Master Profile Flows (No Job ID)
    path('setup/upload/', views.upload_master_profile, name='upload_master_profile'),
    path('setup/review/', views.review_master_profile, name='review_master_profile'),
    path('setup/connect/', views.connect_accounts_view, name='connect_accounts'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('api/onboarding/dismiss/', views.dismiss_onboarding_banner_view, name='dismiss_onboarding_banner'),
    path('api/tour/dismiss/', views.dismiss_tour_view, name='dismiss_tour'),

    path('chatbot/<uuid:job_id>/', views.profile_chatbot, name='profile_chatbot'),
    path('chatbot/api/', views.chatbot_api, name='chatbot_api'),
    path('chatbot/scope/<uuid:job_id>/', views.chatbot_scope_decision, name='chatbot_scope_decision'),
    
    # API Endpoints
    path('api/current/', views.get_current_profile, name='get_current_profile'),
    
    # Advanced AI Features
    path('outreach/<uuid:job_id>/', views.generate_outreach_view, name='generate_outreach'),
    path('outreach/<uuid:job_id>/campaign/', views.outreach_campaign_view, name='outreach_campaign'),

    # External signal aggregation
    path('refresh-github/',   views.refresh_github_signals,   name='refresh_github_signals'),
    path('refresh-linkedin/', views.refresh_linkedin_signals, name='refresh_linkedin_signals'),
    path('refresh-scholar/',  views.refresh_scholar_signals,  name='refresh_scholar_signals'),
    path('refresh-kaggle/',   views.refresh_kaggle_signals,   name='refresh_kaggle_signals'),
    path('disconnect-github/',   views.disconnect_github_signals,   name='disconnect_github_signals'),
    path('disconnect-linkedin/', views.disconnect_linkedin_signals, name='disconnect_linkedin_signals'),
    path('disconnect-scholar/',  views.disconnect_scholar_signals,  name='disconnect_scholar_signals'),
    path('disconnect-kaggle/',   views.disconnect_kaggle_signals,   name='disconnect_kaggle_signals'),

    # Project enrichment + dedupe
    path('api/projects/enrich-from-signals/', views.enrich_from_signals_view, name='enrich_from_signals'),
    path('projects/review/', views.projects_review_view, name='projects_review'),

    # Job discovery preferences + source-login status
    path('preferences/jobs/', views.job_preferences_view, name='job_preferences'),
    path('preferences/jobs/suggest/', views.suggest_job_preferences_view, name='suggest_job_preferences'),
    path('preferences/job-sources/', views.job_sources_setup_view, name='job_sources_setup'),
]
