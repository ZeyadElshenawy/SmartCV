#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

# Python 3.13 on Windows: platform.machine() / platform.uname() spawn a WMI
# subprocess that can hang indefinitely when the host's WMI service is slow or
# broken. torch.__init__ calls platform.machine() at import time, and
# langchain_groq transitively imports torch, so every `manage.py` invocation
# hangs before any user code runs. Stubbing _wmi_query forces _win32_ver down
# its fallback path; platform.machine() then reads $PROCESSOR_ARCHITECTURE,
# which is what torch actually needs. Remove once the venv drops torch or
# Python ships a fix for https://github.com/python/cpython/issues/118518.
if sys.platform == 'win32':
    import platform as _platform
    def _wmi_query_disabled(*_a, **_kw):
        raise OSError('WMI disabled (Py3.13 Windows hang workaround)')
    _platform._wmi_query = _wmi_query_disabled


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
