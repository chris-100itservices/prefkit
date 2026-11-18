"""Django settings for prefkit.

Every value can be overridden by an environment variable. For local debugging,
the defaults give you SQLite, in-process (eager) Celery and no Redis, so
`python manage.py runserver` works on a fresh clone.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Read the project's .env file, if it exists. Values already set in the operating
# system's environment win, so a variable set in the terminal still overrides the file.
load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    """Read an environment variable, falling back to a default."""
    return os.environ.get(name, default)


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable. Accepts 1/true/yes/on, case-insensitive."""
    value = env(name, "1" if default else "0") or ""
    return value.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = (env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1") or "").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "prefkit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Every page needs a signed-in user, unless the view is marked as open. Must come after
    # AuthenticationMiddleware, which works out who is signed in.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "prefkit.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Database: SQLite by default (zero setup). Set PREFKIT_DB=postgres for the real backend.
if env("PREFKIT_DB", "sqlite") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "prefkit"),
            "USER": env("POSTGRES_USER", "prefkit"),
            "PASSWORD": env("POSTGRES_PASSWORD", "prefkit"),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    (BASE_DIR / "var").mkdir(exist_ok=True)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "var" / "prefkit.sqlite3",
        }
    }

# Cache holds live job progress. Redis when configured, otherwise in-process memory.
REDIS_URL = env("REDIS_URL")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

# Static files: the page's JavaScript and styles. STATIC_URL is relative, so Django puts the
# site's URL prefix in front of it. `python manage.py collectstatic` copies every static file
# into STATIC_ROOT, where a web server such as nginx can serve them directly.
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "var" / "static"

# Where the sign-in page is. A URL name, not a path, so it follows the URL prefix below.
LOGIN_URL = "login"

# ---------------------------------------------------------------- running behind a web server
#
# These are only needed on a server where nginx (or another web server) receives the public
# connections and passes them to prefkit. See section 9.6 of the manual.

# The subfolder of the site where prefkit lives, for example /tests/prefkit. Django adds it
# to every link it makes. The web server must remove it before passing a request on.
PREFKIT_URL_PREFIX = (env("PREFKIT_URL_PREFIX", "") or "").rstrip("/")
if PREFKIT_URL_PREFIX:
    FORCE_SCRIPT_NAME = PREFKIT_URL_PREFIX

# Sites allowed to send forms to prefkit over HTTPS, for example https://example.com.
# Without it, Django refuses form posts that arrive through the web server.
CSRF_TRUSTED_ORIGINS = [o.strip() for o in (env("PREFKIT_CSRF_ORIGINS", "") or "").split(",") if o.strip()]

# When the web server handles HTTPS, it passes plain HTTP to prefkit and says so in the
# X-Forwarded-Proto header. Trust that header only when a web server is in front, because
# anyone can send it to an app that is reachable directly.
PREFKIT_BEHIND_PROXY = env_bool("PREFKIT_BEHIND_PROXY", False)
if PREFKIT_BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Send the sign-in and form cookies over HTTPS only, so they cannot be read in transit.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Keep prefkit's cookies inside its own subfolder, so they never mix with the cookies of
# another site on the same domain, such as WordPress.
SESSION_COOKIE_PATH = f"{PREFKIT_URL_PREFIX}/"
CSRF_COOKIE_PATH = f"{PREFKIT_URL_PREFIX}/"

# Celery. With PREFKIT_EAGER=1 (the default), tasks run inside the calling process,
# so a breakpoint in an ingest is hit in VS Code without starting a worker.
CELERY_BROKER_URL = REDIS_URL or "memory://localhost/"
CELERY_TASK_ALWAYS_EAGER = env_bool("PREFKIT_EAGER", True)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ROUTES = {"prefkit.ingest_dataset": {"queue": "ingest"}}

# Compute: auto | cuda | mps | cpu. Read by prefkit.compute.devices.
PREFKIT_DEVICE = env("PREFKIT_DEVICE", "auto")
PREFKIT_JUDGE_MODEL = env("PREFKIT_JUDGE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
PREFKIT_LOG_LEVEL = env("PREFKIT_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"prefkit": {"handlers": ["console"], "level": PREFKIT_LOG_LEVEL, "propagate": False}},
}
