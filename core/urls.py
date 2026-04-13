from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path('', views.home_view, name='home'),
    # Legacy route — redirect to the canonical profiles dashboard
    path('dashboard/', RedirectView.as_view(pattern_name='dashboard', permanent=False), name='core_dashboard'),
    # Internal design-system styleguide
    path('design/', views.design_system_view, name='design_system'),
]
