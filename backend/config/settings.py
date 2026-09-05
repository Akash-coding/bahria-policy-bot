"""
Django settings for the Bahria University Policy Bot.

All secrets and runtime configuration come from environment variables.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name, "true" if default else "false").lower()
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def _env_list(name: str, default: str) -> list[str]:
    raw = _env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = _env("DJANGO_SECRET_KEY", "insecure-dev-key-change-in-production")
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "accounts",
    "documents",
    "chat",
    "rag",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

_db_engine = _env("DATABASE_ENGINE", "sqlite").lower()
if _db_engine in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _env("POSTGRES_DB", "bahria_policy_bot"),
            "USER": _env("POSTGRES_USER", "bahria"),
            "PASSWORD": _env("POSTGRES_PASSWORD", ""),
            "HOST": _env("POSTGRES_HOST", "localhost"),
            "PORT": _env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

_media_root = _env("MEDIA_ROOT", str(PROJECT_ROOT / "data" / "documents"))
MEDIA_ROOT = Path(_media_root)
if not MEDIA_ROOT.is_absolute():
    MEDIA_ROOT = (PROJECT_ROOT / MEDIA_ROOT).resolve()
MEDIA_URL = "/media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

if DEBUG:
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append(
        "rest_framework.renderers.BrowsableAPIRenderer"
    )

CORS_ALLOWED_ORIGINS = _env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = _env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
)
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_HTTPONLY = True
# HTTP LAN (e.g. http://10.1.2.216) needs this false. Set true only behind HTTPS.
CSRF_COOKIE_SECURE = _env_bool("DJANGO_COOKIE_SECURE", False)
SESSION_COOKIE_SECURE = _env_bool("DJANGO_COOKIE_SECURE", False)

if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

# --- Policy Bot / RAG ---
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_TIMEOUT = _env_int("OLLAMA_TIMEOUT", 600)
OLLAMA_TEMPERATURE = _env_float("OLLAMA_TEMPERATURE", 0.3)
OLLAMA_NUM_CTX = _env_int("OLLAMA_NUM_CTX", 4096)
OLLAMA_NUM_PREDICT = _env_int("OLLAMA_NUM_PREDICT", 400)

EMBEDDING_PROVIDER = _env("EMBEDDING_PROVIDER", "ollama").lower()
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "nomic-embed-text")

_vector_path = _env("VECTOR_DB_PATH", str(PROJECT_ROOT / "data" / "chroma"))
VECTOR_DB_PATH = Path(_vector_path)
if not VECTOR_DB_PATH.is_absolute():
    VECTOR_DB_PATH = (PROJECT_ROOT / VECTOR_DB_PATH).resolve()
VECTOR_COLLECTION = _env("VECTOR_COLLECTION", "bahria_policies")

CHUNK_SIZE = _env_int("CHUNK_SIZE", 900)
CHUNK_OVERLAP = _env_int("CHUNK_OVERLAP", 150)
RAG_TOP_K = _env_int("RAG_TOP_K", 4)
SIMILARITY_THRESHOLD = _env_float("SIMILARITY_THRESHOLD", 0.28)
MAX_CONTEXT_CHARS = _env_int("MAX_CONTEXT_CHARS", 3800)
MAX_CONTEXT_CHARS = MAX_CONTEXT_CHARS
GRAPH_RAG_ENABLED = _env_bool("GRAPH_RAG_ENABLED", True)
GRAPH_MIN_VECTOR_HITS = _env_int("GRAPH_MIN_VECTOR_HITS", 2)
GRAPH_STRONG_SCORE = _env_float("GRAPH_STRONG_SCORE", 0.45)
GRAPH_EXPAND_LIMIT = _env_int("GRAPH_EXPAND_LIMIT", 6)

MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 20)
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt"}
PROCESS_DOCUMENTS_ASYNC = _env_bool("PROCESS_DOCUMENTS_ASYNC", True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        }
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "documents": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "rag": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "chat": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "accounts": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}
