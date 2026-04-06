from django.urls import path
from . import views

urlpatterns = [
    path('generate/<uuid:job_id>/', views.generate_resume_view, name='generate_resume'),
    path('api/resume-status/<uuid:job_id>/', views.check_resume_status_api, name='check_resume_status'),
    path('preview/<uuid:resume_id>/', views.resume_preview_view, name='resume_preview'),
    path('edit/<uuid:resume_id>/', views.resume_edit_view, name='resume_edit'),
    path('export/<uuid:resume_id>/', views.export_pdf_view, name='export_pdf'),
    
    # NEW: Optimized PDF Generation
    path('download/<uuid:job_id>/', views.generate_optimized_pdf_view, name='generate_optimized_pdf'),
    
    # NEW: Cover Letters
    path('cover-letter/generate/<uuid:job_id>/', views.generate_cover_letter_view, name='generate_cover_letter'),
    path('cover-letter/preview/<uuid:letter_id>/', views.cover_letter_preview_view, name='cover_letter_preview'),
    
    # NEW: Resume Management
    path('history/', views.resume_list_view, name='resume_list'),
    path('delete/<uuid:resume_id>/', views.resume_delete_view, name='resume_delete'),
]
