"""The settings for running behind a web server. Each case loads the settings in a fresh
Python process with the environment variables set, as a server would."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRINT = (
    "import json, django; from django.conf import settings as s; django.setup(); "
    "print(json.dumps({k: (str(getattr(s, k)) if hasattr(s, k) else None) for k in ("
    "'FORCE_SCRIPT_NAME', 'CSRF_TRUSTED_ORIGINS', 'SECURE_PROXY_SSL_HEADER', 'SESSION_COOKIE_SECURE', "
    "'CSRF_COOKIE_SECURE', 'SESSION_COOKIE_PATH', 'CSRF_COOKIE_PATH', 'STATIC_ROOT', 'LOGIN_URL')}))"
)


def settings_with(**env) -> dict:
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("PREFKIT_", "DJANGO_"))}
    environment.update(env, DJANGO_SETTINGS_MODULE="prefkit.settings")
    done = subprocess.run(
        [sys.executable, "-c", PRINT], cwd=ROOT, env=environment, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_defaults_need_nothing_set():
    s = settings_with()
    assert s["FORCE_SCRIPT_NAME"] in (None, "None")
    assert s["CSRF_TRUSTED_ORIGINS"] == "[]"
    assert s["SECURE_PROXY_SSL_HEADER"] in (None, "None")
    assert s["SESSION_COOKIE_SECURE"] == "False"
    assert s["SESSION_COOKIE_PATH"] == "/"
    assert s["STATIC_ROOT"].replace("\\", "/").endswith("var/static")
    assert s["LOGIN_URL"] == "login"


def test_server_settings_take_effect():
    s = settings_with(
        PREFKIT_URL_PREFIX="/tests/prefkit/",
        PREFKIT_CSRF_ORIGINS="https://example.com, https://www.example.com",
        PREFKIT_BEHIND_PROXY="1",
    )
    assert s["FORCE_SCRIPT_NAME"] == "/tests/prefkit"  # the trailing slash is removed
    assert s["CSRF_TRUSTED_ORIGINS"] == "['https://example.com', 'https://www.example.com']"
    assert s["SECURE_PROXY_SSL_HEADER"] == "('HTTP_X_FORWARDED_PROTO', 'https')"
    assert s["SESSION_COOKIE_SECURE"] == "True" and s["CSRF_COOKIE_SECURE"] == "True"
    assert s["SESSION_COOKIE_PATH"] == "/tests/prefkit/" == s["CSRF_COOKIE_PATH"]


def test_server_requirements_list_gunicorn_and_psycopg():
    text = (ROOT / "requirements-server.txt").read_text(encoding="utf-8")
    assert "gunicorn" in text and "psycopg" in text


def test_the_wsgi_entry_point_loads():
    """gunicorn prefkit.wsgi needs this module; without it the server cannot start."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("PREFKIT_", "DJANGO_"))}
    done = subprocess.run(
        [sys.executable, "-c", "import prefkit.wsgi as w; print(callable(w.application))"],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=True,
    )
    assert done.stdout.strip().endswith("True")
