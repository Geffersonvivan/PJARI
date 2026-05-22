#!/bin/bash
set -e

# Release Command do Railway — roda UMA VEZ antes de qualquer réplica subir.

echo "Collecting static files..."
python3 manage.py collectstatic --noinput

echo "Running migrations..."
python3 manage.py migrate --noinput

echo "Ensuring superuser..."
python3 manage.py shell -c "
import os
from django.contrib.auth.models import User

username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

if username and email and password:
    user, created = User.objects.get_or_create(username=username, defaults={'email': email})
    user.email = email
    user.set_password(password)
    user.is_superuser = True
    user.is_staff = True
    user.save()
    print(f'Superuser {username} {\"created\" if created else \"updated\"}.')
else:
    print('Superuser env vars not set, skipping.')
"

echo "Release steps completed."
