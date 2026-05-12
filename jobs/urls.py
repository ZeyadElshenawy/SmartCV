from django.urls import path
from . import views

urlpatterns = [
    path('input/', views.job_input_view, name='job_input_view'),
    path('review/<uuid:job_id>/', views.review_extracted_job, name='review_extracted_job'),
    path('delete/<uuid:job_id>/', views.job_delete_view, name='job_delete'),
    path('api/v1/extension/save-job/', views.save_job_extension_view, name='save_job_extension'),
    path('api/v1/update-status/', views.update_job_status_api, name='update_job_status'),

    # Recommended jobs / discovery scan (declared BEFORE the catch-all <uuid:job_id>/
    # route so 'recommend/...' isn't swallowed by job_detail).
    path('recommend/scan/', views.scan_recommended_jobs, name='scan_recommended_jobs'),
    path('recommend/scrape/<uuid:scrape_job_id>/status/', views.scrape_status, name='scrape_status'),
    path('recommend/scrape/<uuid:scrape_job_id>/cancel/', views.scrape_cancel, name='scrape_cancel'),
    path('recommend/<uuid:rec_id>/save/', views.recommended_save, name='recommended_save'),
    path('recommend/<uuid:rec_id>/dismiss/', views.recommended_dismiss, name='recommended_dismiss'),
    path('recommend/<uuid:rec_id>/', views.recommended_detail, name='recommended_detail'),

    path('<uuid:job_id>/', views.job_detail_view, name='job_detail'),
]
