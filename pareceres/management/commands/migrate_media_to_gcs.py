"""
Management command para migrar os uploads do volume local (/app/media) para o
bucket GCS — pré-requisito para separar o worker Celery em serviço próprio
(volume do Railway anexa a UM único serviço; GCS é compartilhável).

Uso (dentro do container web, onde o volume está montado):
    python manage.py migrate_media_to_gcs                # copia o que faltar
    python manage.py migrate_media_to_gcs --dry-run      # só lista, não envia
    python manage.py migrate_media_to_gcs --overwrite    # reenvia mesmo se existir

Idempotente: por padrão pula arquivos que já existem no bucket. Lê o filesystem
diretamente (independe de USE_GCS) e escreve no GCS via google-cloud-storage,
usando as credenciais de GOOGLE_APPLICATION_CREDENTIALS (ADC).
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Copia os arquivos de mídia do volume local para o bucket GCS (idempotente)."

    def add_arguments(self, parser):
        default_source = os.environ.get(
            "RAILWAY_VOLUME_MOUNT_PATH",
            getattr(settings, "MEDIA_ROOT", None) or str(settings.BASE_DIR / "media"),
        )
        parser.add_argument("--source", type=str, default=default_source,
                            help="Diretório de origem (default: volume/MEDIA_ROOT)")
        parser.add_argument("--bucket", type=str,
                            default=os.environ.get("GCS_BUCKET_NAME", "pjari-midias"),
                            help="Nome do bucket GCS de destino")
        parser.add_argument("--dry-run", action="store_true", help="Não envia, só lista")
        parser.add_argument("--overwrite", action="store_true",
                            help="Reenvia mesmo se o objeto já existir no bucket")

    def handle(self, *args, **opts):
        source = opts["source"]
        bucket_name = opts["bucket"]
        dry_run = opts["dry_run"]
        overwrite = opts["overwrite"]

        if not os.path.isdir(source):
            raise CommandError(f"Diretório de origem não existe: {source}")

        try:
            from google.cloud import storage
        except ImportError as e:
            raise CommandError(f"google-cloud-storage não instalado: {e}")

        client = storage.Client()
        bucket = client.bucket(bucket_name)

        self.stdout.write(f"Origem: {source}")
        self.stdout.write(f"Destino: gs://{bucket_name}/  (dry_run={dry_run}, overwrite={overwrite})")

        enviados = pulados = erros = total = 0
        for root, _dirs, files in os.walk(source):
            for fname in files:
                local_path = os.path.join(root, fname)
                # Chave no bucket = caminho relativo ao source (mantém uploads/...)
                key = os.path.relpath(local_path, source).replace(os.sep, "/")
                total += 1
                try:
                    blob = bucket.blob(key)
                    if not overwrite and blob.exists():
                        pulados += 1
                        continue
                    if dry_run:
                        self.stdout.write(f"  [dry-run] enviaria {key}")
                        enviados += 1
                        continue
                    blob.upload_from_filename(local_path)
                    enviados += 1
                    if enviados % 25 == 0:
                        self.stdout.write(f"  ... {enviados} enviados")
                except Exception as e:
                    erros += 1
                    self.stderr.write(f"  ERRO em {key}: {e}")

        self.stdout.write(self.style.SUCCESS(
            f"Concluído: {total} arquivos | {enviados} enviados | "
            f"{pulados} já existiam | {erros} erros"
        ))
        if erros:
            raise CommandError(f"{erros} arquivo(s) falharam — verifique os logs acima.")
