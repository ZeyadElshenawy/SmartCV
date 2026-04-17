from django.urls import path
from . import views

urlpatterns = [
    # Job-Specific Flows
    path('upload/<uuid:job_id>/', views.profile_upload_cv, name='profile_upload_cv'),
    path('form/<uuid:job_id>/', views.profile_manual_form, name='profile_manual_form'),
    
    # Master Profile Flows (No Job ID)
    path('setup/upload/', views.upload_master_profile, name='upload_master_profile'),
    path('setup/review/', views.review_master_profile, name='review_master_profile'),
    path('setup/connect/', views.connect_accounts_view, name='connect_accounts'),
    path('dashboard/', views.dashboard, name='dashboard'),

    path('chatbot/<uuid:job_id>/', views.profile_chatbot, name='profile_chatbot'),
    path('chatbot/api/', views.chatbot_api, name='chatbot_api'),
    path('chatbot/complete/<uuid:job_id>/', views.chatbot_complete, name='chatbot_complete'),
    path('chatbot/scope/<uuid:job_id>/', views.chatbot_scope_decision, name='chatbot_scope_decision'),
    
    # API Endpoints
    path('api/current/', views.get_current_profile, name='get_current_profile'),
    
    # Advanced AI Features
    path('outreach/<uuid:job_id>/', views.generate_outreach_view, name='generate_outreach'),

    # External signal aggregation
    path('refresh-github/',   views.refresh_github_signals,   name='refresh_github_signals'),
    path('refresh-linkedin/', views.refresh_linkedin_signals, name='refresh_linkedin_signals'),
    path('refresh-scholar/',  views.refresh_scholar_signals,  name='refresh_scholar_signals'),
    path('refresh-kaggle/',   views.refresh_kaggle_signals,   name='refresh_kaggle_signals'),
]
