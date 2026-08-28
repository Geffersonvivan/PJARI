"""
Migra os uploads do volume local (/app/media) para o bucket Cloudflare R2 —
pré-requisito para separar o worker Celery (volume do Railway anexa a UM
serviço; R2 é compartilhável e S3-compatível).

Uso (dentro do container web, onde o volume está montado):
    python manage.py migrate_media_to_r2                # copia o que faltar
    python manage.py migrate_media_to_r2 --dry-run      # só lista, não envia
    python manage.py migrate_media_to_r2 --overwrite    # reenvia mesmo se existir

Lê o filesystem direto e escreve no R2 via boto3 (independe de USE_R2), então
pode rodar ANTES de flipar USE_R2=True — cópia não-destrutiva. Requer os envs:
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL, R2_BUCKET_NAME.
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Copia mídia do volume local para o bucket R2 (idempotente)."

    def add_arguments(self, parser):
        default_source = os.environ.get(
            "RAILWAY_VOLUME_MOUNT_PATH",
            getattr(settings, "MEDIA_ROOT", None) or str(settings.BASE_DIR / "media"),
        )
        parser.add_argument("--source", type=str, default=default_source,
                            help="Diretório de origem (default: volume/MEDIA_ROOT)")
        parser.add_argument("--bucket", type=str,
                            default=os.environ.get("R2_BUCKET_NAME", "pjari-midias"),
                            help="Nome do bucket R2 de destino")
        parser.add_argument("--dry-run", action="store_true", help="Não envia, só lista")
        parser.add_argument("--overwrite", action="store_true",
                            help="Reenvia mesmo se o objeto já existir")

    def handle(self, *args, **opts):
        source = opts["source"]
        bucket = opts["bucket"]
        dry_run = opts["dry_run"]
        overwrite = opts["overwrite"]

        if not os.path.isdir(source):
            raise CommandError(f"Diretório de origem não existe: {source}")

        endpoint = os.environ.get("R2_ENDPOINT_URL")
        access_key = os.environ.get("R2_ACCESS_KEY_ID")
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        if not (endpoint and access_key and secret_key):
            raise CommandError(
                "Faltam envs R2: defina R2_ENDPOINT_URL, R2_ACCESS_KEY_ID e "
                "R2_SECRET_ACCESS_KEY."
            )

        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import ClientError
        except ImportError as e:
            raise CommandError(f"boto3 não instalado (django-storages[s3]): {e}")

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
        )

        def existe(key):
            try:
                s3.head_object(Bucket=bucket, Key=key)
                return True
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

        self.stdout.write(f"Origem: {source}")
        self.stdout.write(f"Destino: r2://{bucket}/  (dry_run={dry_run}, overwrite={overwrite})")

        enviados = pulados = erros = total = 0
        for root, _dirs, files in os.walk(source):
            for fname in files:
                local_path = os.path.join(root, fname)
                key = os.path.relpath(local_path, source).replace(os.sep, "/")
                total += 1
                try:
                    if not overwrite and existe(key):
                        pulados += 1
                        continue
                    if dry_run:
                        self.stdout.write(f"  [dry-run] enviaria {key}")
                        enviados += 1
                        continue
                    s3.upload_file(local_path, bucket, key)
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
