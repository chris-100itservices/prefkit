"""The entry point a production web server such as Gunicorn loads: `gunicorn prefkit.wsgi`.

WSGI is the standard way a Python web server hands requests to a web application.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "prefkit.settings")

application = get_wsgi_application()
