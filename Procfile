web: cd backend && python manage.py migrate && gunicorn friends_turf.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 90
