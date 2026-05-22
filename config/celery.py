import os
from celery import Celery
from celery.signals import task_postrun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("pjari")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@task_postrun.connect
def close_db_connections(**kwargs):
    """Fecha conexões idle do Postgres após cada task (evita pool exhaustion)."""
    from django import db
    db.close_old_connections()
