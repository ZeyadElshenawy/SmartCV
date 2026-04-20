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
    # Debug toolbar is only installed when DEBUG and not running tests
    # (see smartcv/settings.py). Guard the import against the test case.
    if 'debug_toolbar' in settings.INSTALLED_APPS:
        import debug_toolbar
        urlpatterns = [
            path('__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns

# Custom error handlers
handler404 = 'core.views.custom_404'
handler500 = 'core.views.custom_500'
