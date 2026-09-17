"""Hosting médií.

Instagram si soubor stahuje z veřejné URL — nelze ho nahrát přímo v requestu.
Proto potřebujeme místo, odkud je médium na pár minut veřejně dostupné.

Podporujeme:
  * LocalHost — soubor už leží ve složce, která je publikovaná přes web
    (nginx / Caddy / GitHub Pages / cokoliv). Stačí nastavit MEDIA_PUBLIC_BASE.
  * S3Host   — S3 nebo S3-kompatibilní úložiště (AWS S3, Cloudflare R2,
    Backblaze B2, MinIO). Vyžaduje `pip install boto3`.
"""

from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from urllib.parse import quote

from ..errors import ConfigError, MediaError
from ..util import get_logger, short_hash

log = get_logger(__name__)


class MediaHost:
    def publish(self, path, key=None) -> str:
        raise NotImplementedError

    def cleanup(self, key):  # pragma: no cover - volitelné
        return None


class LocalHost(MediaHost):
    """Zkopíruje soubor do veřejné složky a vrátí jeho URL."""

    def __init__(self, public_dir, public_base):
        if not public_base:
            raise ConfigError("LocalHost potřebuje MEDIA_PUBLIC_BASE.")
        self.public_dir = Path(public_dir)
        self.public_dir.mkdir(parents=True, exist_ok=True)
        self.public_base = public_base.rstrip("/")

    def publish(self, path, key=None):
        src = Path(path)
        if not src.exists():
            raise MediaError(f"Soubor k publikaci neexistuje: {src}")
        key = key or f"{short_hash(src.name, src.stat().st_mtime)}-{src.name}"
        dest = self.public_dir / key
        if dest.resolve() != src.resolve():
            shutil.copy2(src, dest)
        url = f"{self.public_base}/{quote(key)}"
        log.debug("Médium %s dostupné na %s", src.name, url)
        return url

    def cleanup(self, key):
        target = self.public_dir / key
        if target.exists():
            target.unlink()


class S3Host(MediaHost):
    """Nahraje soubor do S3/R2 a vrátí veřejnou (nebo předpodepsanou) URL."""

    def __init__(self, bucket, prefix="ig", region="auto", endpoint_url=None,
                 public_base=None, presign_seconds=3600):
        try:
            import boto3  # noqa: PLC0415 - volitelná závislost
        except ImportError as exc:  # pragma: no cover - závisí na prostředí
            raise ConfigError("MEDIA_HOST=s3 vyžaduje `pip install boto3`.") from exc
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.public_base = (public_base or "").rstrip("/")
        self.presign_seconds = presign_seconds
        self.client = boto3.client(
            "s3", region_name=None if region == "auto" else region,
            endpoint_url=endpoint_url or None)

    def _key(self, filename):
        return f"{self.prefix}/{filename}" if self.prefix else filename

    def publish(self, path, key=None):
        src = Path(path)
        if not src.exists():
            raise MediaError(f"Soubor k publikaci neexistuje: {src}")
        name = key or f"{short_hash(src.name, src.stat().st_mtime)}-{src.name}"
        s3_key = self._key(name)
        content_type = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        self.client.upload_file(str(src), self.bucket, s3_key,
                                ExtraArgs={"ContentType": content_type})
        if self.public_base:
            return f"{self.public_base}/{quote(s3_key)}"
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": s3_key},
            ExpiresIn=self.presign_seconds)

    def cleanup(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))


class NullHost(MediaHost):
    """Pro dry-run: vrací fiktivní URL, nikam nic nekopíruje."""

    def publish(self, path, key=None):
        return f"https://dry-run.local/{Path(path).name}"


def build_host(settings, dry_run=False):
    if dry_run:
        return NullHost()
    if settings.media_host == "s3":
        return S3Host(settings.s3_bucket, settings.s3_prefix, settings.s3_region,
                      settings.s3_endpoint or None, settings.media_public_base or None)
    return LocalHost(settings.out_dir, settings.media_public_base)
