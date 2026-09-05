#!/bin/sh
set -e
cd /srv/backend
python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --timeout 600 --workers 2 --access-logfile -
