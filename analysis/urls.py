from django.urls import path
from . import views

urlpatterns = [
    path('gap/<uuid:job_id>/', views.gap_analysis_view, name='gap_analysis'),
    path('learning-path/', views.generate_learning_path_view, name='learning_path'),
    path('negotiate/<uuid:job_id>/', views.negotiate_salary_view, name='negotiate_salary'),
]
