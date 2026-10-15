@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
python manage.py collectstatic --noinput >nul
echo Red Tag System running on port 8000. Close this window to stop it.
waitress-serve --listen=0.0.0.0:8000 --threads=8 redtag_project.wsgi:application
