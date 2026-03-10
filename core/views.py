from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from jobs.models import Job

def home_view(request):
    """Landing page - redirect to appropriate dashboard"""
    if request.user.is_authenticated:
        # Redirect to new profiles dashboard
        return redirect('/profiles/dashboard/')
    return render(request, 'core/home.html')

@login_required
def dashboard_view(request):
    """Legacy dashboard - redirect to new profiles dashboard"""
    return redirect('/profiles/dashboard/')
