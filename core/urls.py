from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path('', views.home_view, name='home'),
    # Legacy route — redirect to the canonical profiles dashboard
    path('dashboard/', RedirectView.as_view(pattern_name='dashboard', permanent=False), name='core_dashboard'),
    # First-run welcome (shown once after signup, then skips to dashboard)
    path('welcome/',      views.welcome_view,      name='welcome'),
    # Exit the onboarding flow early from any step after /welcome/
    path('skip-onboarding/', views.skip_onboarding_view, name='skip_onboarding'),
    # Career-agent surface: full pipeline + cross-cutting insights hub + general chat
    path('applications/', views.applications_view, name='applications'),
    path('insights/',     views.insights_view,     name='insights'),
    path('agent/',        views.agent_chat_view,   name='agent_chat'),
    path('agent/api/',    views.agent_chat_api,    name='agent_chat_api'),
    # Internal design-system styleguide
    path('design/', views.design_system_view, name='design_system'),
]
