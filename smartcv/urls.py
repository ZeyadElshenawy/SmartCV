from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('jobs/', include('jobs.urls')),
    path('profiles/', include('profiles.urls')),
    path('analysis/', include('analysis.urls')),
    path('resumes/', include('resumes.urls')),
    path('', include('core.urls')),  # Homepage and dashboard
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
