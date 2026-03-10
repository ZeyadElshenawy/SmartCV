from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import User

# TODO: Use custom forms for styled rendering if needed, 
# for now using standard Django forms or manual handling in templates for custom style as requested

def register_view(request):
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
            return redirect('dashboard')
        except Exception as e:
            messages.error(request, f"Error: {e}")
            
    return render(request, 'accounts/register.html')

def login_view(request):
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
def profile_view(request):
    return render(request, 'accounts/profile.html', {'user': request.user})
