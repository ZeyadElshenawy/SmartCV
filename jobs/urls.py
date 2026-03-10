from django.urls import path
from . import views

urlpatterns = [
    path('input/', views.job_input_view, name='job_input_view'),
    path('<uuid:job_id>/', views.job_detail_view, name='job_detail'),
    path('api/v1/extension/save-job/', views.save_job_extension_view, name='save_job_extension'),
    path('api/v1/update-status/', views.update_job_status_api, name='update_job_status'),
]
