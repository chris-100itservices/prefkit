"""Django settings for prefkit.

Every value can be overridden by an environment variable. For local debugging,
the defaults give you SQLite, in-process (eager) Celery and no Redis, so
`python manage.py runserver` works on a fresh clone.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


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

STATIC_URL = "static/"

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
PREFKIT_JUDGE_MODEL = env("PREFKIT_JUDGE_MODEL", "Qwen/Qwen2.5-3B-Instruct")
PREFKIT_LOG_LEVEL = env("PREFKIT_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"prefkit": {"handlers": ["console"], "level": PREFKIT_LOG_LEVEL, "propagate": False}},
}
