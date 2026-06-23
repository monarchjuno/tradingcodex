from __future__ import annotations

import os
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
BASE_DIR = SERVICE_DIR.parent


def default_db_name() -> str:
    configured = os.environ.get("TRADINGCODEX_DB_NAME")
    if configured:
        path = Path(configured).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)
    home = Path(os.environ.get("TRADINGCODEX_HOME", "~/.tradingcodex")).expanduser().resolve()
    path = home / "state" / "tradingcodex.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)

SECRET_KEY = os.environ.get("TRADINGCODEX_SECRET_KEY", "tradingcodex-local-dev-key")
DEBUG = os.environ.get("TRADINGCODEX_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("TRADINGCODEX_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")
ROOT_URLCONF = "tradingcodex_service.urls"
ASGI_APPLICATION = "tradingcodex_service.asgi.application"
WSGI_APPLICATION = "tradingcodex_service.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ninja",
    "apps.audit",
    "apps.harness",
    "apps.integrations",
    "apps.mcp",
    "apps.orders",
    "apps.policy",
    "apps.portfolio",
    "apps.research",
    "apps.workflows",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": default_db_name(),
        "OPTIONS": {"timeout": int(os.environ.get("TRADINGCODEX_SQLITE_TIMEOUT", "30"))},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [SERVICE_DIR / "static"]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates", BASE_DIR / "tradingcodex_service" / "templates"],
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

TRADINGCODEX = {
    "max_single_order_krw": int(os.environ.get("TRADINGCODEX_MAX_SINGLE_ORDER_KRW", "100000000")),
    "allowed_adapters": ["stub-execution", "paper-trading"],
    "enabled_live_execution": False,
}
