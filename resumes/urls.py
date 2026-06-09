from django.urls import path
from . import views

urlpatterns = [
    path('generate/<uuid:job_id>/', views.generate_resume_view, name='generate_resume'),
    path('api/trigger-resume/<uuid:job_id>/', views.trigger_resume_generation_api, name='trigger_resume_api'),
    # PR audit §6.5 fix #3 — supervised in-place regen for the stale-profile /
    # ?refresh=1 flow, behind the same async loader used by 'generate_resume'
    # so a GET on /edit/<id>/ never blocks on a 15-90s LLM call.
    path('regenerate/<uuid:resume_id>/', views.regenerate_resume_view, name='regenerate_resume'),
    path('api/regenerate-resume/<uuid:resume_id>/', views.trigger_resume_regeneration_api, name='trigger_resume_regen_api'),
    path('preview/<uuid:resume_id>/', views.resume_preview_view, name='resume_preview'),
    path('edit/<uuid:resume_id>/', views.resume_edit_view, name='resume_edit'),
    path('regen/<uuid:resume_id>/<str:section>/', views.regenerate_section_view, name='regenerate_section'),
    path('section-order/<uuid:resume_id>/', views.update_section_order_view, name='update_section_order'),
    path('export/<uuid:resume_id>/', views.export_pdf_view, name='export_pdf'),
    path('export-docx/<uuid:resume_id>/', views.export_docx_view, name='export_docx'),
    
    # NEW: Optimized PDF Generation
    path('download/<uuid:job_id>/', views.generate_optimized_pdf_view, name='generate_optimized_pdf'),
    
    # NEW: Cover Letters
    path('cover-letter/generate/<uuid:job_id>/', views.generate_cover_letter_view, name='generate_cover_letter'),
    path('cover-letter/preview/<uuid:letter_id>/', views.cover_letter_preview_view, name='cover_letter_preview'),
    
    # NEW: Resume Management
    path('history/', views.resume_list_view, name='resume_list'),
    path('delete/<uuid:resume_id>/', views.resume_delete_view, name='resume_delete'),

    # Findings UX — 3-bucket list + guarded "Fix it" proposal/accept.
    path('api/findings/<uuid:resume_id>/',
         views.resume_findings_api, name='resume_findings_api'),
    path('api/findings/<uuid:resume_id>/fix/<str:finding_id>/',
         views.resume_propose_fix_api, name='resume_propose_fix_api'),
    path('api/findings/<uuid:resume_id>/accept/',
         views.resume_accept_fix_api, name='resume_accept_fix_api'),

    # Read-only "why this score" ATS breakdown (Slice 1). Not under
    # api/findings/ — it isn't a finding; it recomputes the deterministic
    # scorer's structured breakdown for the editor panel.
    path('api/<uuid:resume_id>/ats-breakdown/',
         views.resume_ats_breakdown_api, name='resume_ats_breakdown_api'),
    # Apply one actionable Category-1 card (Slice 3) — card-id-only trust path.
    path('api/<uuid:resume_id>/ats-apply/',
         views.resume_ats_apply_api, name='resume_ats_apply_api'),
]
