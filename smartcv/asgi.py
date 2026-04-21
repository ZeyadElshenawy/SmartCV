"""
ASGI config for smartcv project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
import sys

# See manage.py for context. Same Python 3.13 Windows WMI hang workaround.
if sys.platform == 'win32':
    import platform as _platform
    def _wmi_query_disabled(*_a, **_kw):
        raise OSError('WMI disabled (Py3.13 Windows hang workaround)')
    _platform._wmi_query = _wmi_query_disabled

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')

application = get_asgi_application()
