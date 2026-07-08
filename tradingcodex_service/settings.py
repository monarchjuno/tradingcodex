from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

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


def sqlite_database_config(name: str) -> dict[str, Any]:
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": name,
        "OPTIONS": {"timeout": int(os.environ.get("TRADINGCODEX_SQLITE_TIMEOUT", "30"))},
    }


def database_config_from_url(database_url: str) -> dict[str, Any]:
    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()
    if scheme in {"sqlite", "sqlite3"}:
        if parsed.path in {"", "/:memory:"}:
            name = ":memory:"
        else:
            name = str(Path(unquote(parsed.path)).expanduser().resolve())
            Path(name).parent.mkdir(parents=True, exist_ok=True)
        return sqlite_database_config(name)
    if scheme in {"postgres", "postgresql"}:
        options = {key: value for key, value in parse_qsl(parsed.query) if key in {"sslmode", "connect_timeout", "application_name"}}
        config: dict[str, Any] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
        }
        if options:
            config["OPTIONS"] = options
        return config
    raise ValueError(f"unsupported TRADINGCODEX_DATABASE_URL scheme: {scheme or '<empty>'}")


def default_database_config() -> dict[str, Any]:
    database_url = os.environ.get("TRADINGCODEX_DATABASE_URL")
    if database_url:
        return database_config_from_url(database_url)
    return sqlite_database_config(default_db_name())


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

DATABASES = {"default": default_database_config()}

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
