"""One-click deployment task for hosts without SSH.

In cPanel > Setup Python App > 'Execute python script', run:  cpanel_deploy.py
It is safe to run again after every update. It will:
  1. check the database connection
  2. create/upgrade the tables (migrate)
  3. collect static files
  4. create the first admin user, if DJANGO_SUPERUSER_USERNAME / _PASSWORD are in .env
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

from deploy_settings import detect_settings_module, load_env_file  # noqa: E402

load_env_file(os.path.join(BASE_DIR, ".env"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", detect_settings_module(BASE_DIR))

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402


def step(text):
    print(f"\n=== {text}", flush=True)


print(f"Python {sys.version.split()[0]} | Django {django.get_version()} | DEBUG={settings.DEBUG}")
if settings.DEBUG:
    print("WARNING: DEBUG is on. Check that .env contains DJANGO_ENV=production")

step("Database connection")
connection.ensure_connection()
with connection.cursor() as cur:
    cur.execute("SELECT VERSION()")
    print("Connected to", settings.DATABASES["default"]["NAME"], "- server version", cur.fetchone()[0])

step("Migrating database")
call_command("migrate", interactive=False)

step("Collecting static files")
call_command("collectstatic", interactive=False, verbosity=0)
print("Done")

username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
if username and password:
    step("Admin user")
    User = get_user_model()
    if User.objects.filter(**{User.USERNAME_FIELD: username}).exists():
        print(f"User '{username}' already exists - nothing changed")
    else:
        User.objects.create_superuser(username, os.environ.get("DJANGO_SUPERUSER_EMAIL", ""), password)
        print(f"Created admin user '{username}'. Now delete the DJANGO_SUPERUSER_* lines from .env")

print("\nALL DONE. Now click 'Restart' in Setup Python App.")
