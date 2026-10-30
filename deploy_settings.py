"""Production (cPanel) configuration, kept out of settings.py.

settings.py only needs these two lines at the very bottom:

    from deploy_settings import apply_production_settings
    apply_production_settings(globals())

On your own computer nothing changes. On the server, a file called `.env` in the
app folder holds DJANGO_ENV=production plus the secrets, and this module then
switches the project to production mode.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def load_env_file(path):
    """Read KEY=VALUE lines from a .env file (no quoting rules, no variable expansion,
    so passwords may contain characters like # $ & !)."""
    path = Path(path)
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def detect_settings_module(base_dir):
    """Find the project package (the folder holding both settings.py and wsgi.py)."""
    for name in sorted(os.listdir(base_dir)):
        folder = os.path.join(base_dir, name)
        if os.path.isfile(os.path.join(folder, "settings.py")) and os.path.isfile(os.path.join(folder, "wsgi.py")):
            return f"{name}.settings"
    raise ImproperlyConfigured("Could not find the Django project folder (the one with settings.py and wsgi.py).")


REQUIRED = ["DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "DB_NAME", "DB_USER", "DB_PASSWORD"]


def apply_production_settings(g):
    base_dir = Path(g["BASE_DIR"])
    load_env_file(base_dir / ".env")
    if os.environ.get("DJANGO_ENV") != "production":
        return  # local development: leave settings.py exactly as it is

    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        raise ImproperlyConfigured("Missing in the .env file: " + ", ".join(missing))

    hosts = [h.strip() for h in os.environ["DJANGO_ALLOWED_HOSTS"].split(",") if h.strip()]
    secure = os.environ.get("DJANGO_SECURE_COOKIES", "1") != "0"

    g["DEBUG"] = False
    g["SECRET_KEY"] = os.environ["DJANGO_SECRET_KEY"]
    g["ALLOWED_HOSTS"] = hosts
    g["CSRF_TRUSTED_ORIGINS"] = [f"https://{h.lstrip('.')}" for h in hosts]

    g["DATABASES"] = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ["DB_NAME"],
            "USER": os.environ["DB_USER"],
            "PASSWORD": os.environ["DB_PASSWORD"],
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }

    # Static files (admin look and feel) are served by WhiteNoise: no web-server setup needed
    middleware = list(g["MIDDLEWARE"])
    if "whitenoise.middleware.WhiteNoiseMiddleware" not in middleware:
        middleware.insert(middleware.index("django.middleware.security.SecurityMiddleware") + 1,
                          "whitenoise.middleware.WhiteNoiseMiddleware")
    g["MIDDLEWARE"] = middleware
    g["STATIC_ROOT"] = base_dir / "staticfiles"
    g["STORAGES"] = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
    }

    g["SESSION_COOKIE_SECURE"] = secure
    g["CSRF_COOKIE_SECURE"] = secure
    g["SECURE_CONTENT_TYPE_NOSNIFF"] = True

    # Errors go to a file you can open in cPanel's File Manager (no SSH needed)
    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    g["LOGGING"] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(log_dir / "django.log"),
                "maxBytes": 1_000_000,
                "backupCount": 3,
                "formatter": "std",
            }
        },
        "root": {"handlers": ["file"], "level": "WARNING"},
    }
