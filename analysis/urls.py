from django.urls import path
from . import views

urlpatterns = [
    path('gap/<uuid:job_id>/', views.gap_analysis_view, name='gap_analysis'),
    path('api/compute/<uuid:job_id>/', views.compute_gap_api, name='compute_gap_api'),
    path('learning-path/', views.generate_learning_path_view, name='learning_path'),
    path('learning-path/<uuid:job_id>/', views.generate_learning_path_view, name='learning_path'),
    path('negotiate/<uuid:job_id>/', views.negotiate_salary_view, name='negotiate_salary'),
]
