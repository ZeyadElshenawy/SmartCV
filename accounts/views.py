from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import User
import logging

logger = logging.getLogger(__name__)

# TODO: Use custom forms for styled rendering if needed, 
# for now using standard Django forms or manual handling in templates for custom style as requested

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        # Custom registration logic to handle email as username
        email = request.POST.get('email')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        
        if password != confirm_password:
            messages.error(request, "Passwords do not match")
            return render(request, 'accounts/register.html')
            
        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already registered")
            return render(request, 'accounts/register.html')
            
        try:
            user = User.objects.create_user(username=email, email=email, password=password)
            login(request, user)
            # First-ever entry point — a friendly welcome before the dashboard.
            # welcome_view itself short-circuits to dashboard on repeat visits.
            return redirect('welcome')
        except Exception as e:
            logger.exception("Registration failed: %s", e)
            messages.error(request, "Registration failed. Please try again.")
            
    return render(request, 'accounts/register.html')

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, "Invalid credentials")
            
    return render(request, 'accounts/login.html')

def logout_view(request):
    logout(request)
    return redirect('home')

def password_reset_request(request):
    # Simplified for dev
    if request.method == 'POST':
        messages.success(request, "Password reset link sent (console)")
        return redirect('login')
    return render(request, 'accounts/password_reset.html')

def password_reset_confirm(request, uidb64, token):
    # Simplified placeholder
    return render(request, 'accounts/password_reset_confirm.html')

@login_required
def account_settings_view(request):
    """Account settings page — change password, view account info."""
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'change_password':
            current_password = request.POST.get('current_password')
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_new_password')
            
            if not request.user.check_password(current_password):
                messages.error(request, "Current password is incorrect.")
            elif new_password != confirm_password:
                messages.error(request, "New passwords do not match.")
            elif len(new_password) < 8:
                messages.error(request, "Password must be at least 8 characters.")
            else:
                request.user.set_password(new_password)
                request.user.save()
                # Re-authenticate to prevent logout
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)
                messages.success(request, "Password updated successfully.")
            
            return redirect('account_settings')
    
    return render(request, 'accounts/settings.html', {'user': request.user})
