"""Drobné pomocné funkce: logování, čas, retry, práce se soubory."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import random
import re
import time
import unicodedata
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"


def setup_logging(level="INFO", logfile=None):
    handlers = [logging.StreamHandler()]
    if logfile:
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, str(level).upper(), logging.INFO),
                        format=LOG_FORMAT, handlers=handlers, force=True)
    # requests/urllib3 jsou ukecané
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name):
    return logging.getLogger(name)


# ---------------------------------------------------------------- čas

def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse_iso(value):
    """Parsuje ISO8601 i formát, který vrací Graph API (+0000)."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    text = str(value).strip()
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    text = text.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def to_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat()


def local_tz(name):
    """Vrátí tzinfo podle IANA jména; při neúspěchu UTC."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # pragma: no cover - závisí na systémové tzdata
        return dt.timezone.utc


# ---------------------------------------------------------------- retry

def retry(times=4, base_delay=2.0, exceptions=(Exception,), should_retry=None, logger=None):
    """Dekorátor s exponenciálním backoffem (2s, 4s, 8s, 16s) a jitterem."""

    def decorator(fn):
        def wrapper(*args, **kwargs):
            last = None
            for attempt in range(times):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203 - retry smyčka
                    last = exc
                    if should_retry is not None and not should_retry(exc):
                        raise
                    if attempt == times - 1:
                        raise
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    (logger or logging.getLogger(fn.__module__)).warning(
                        "%s selhalo (%s), zkouším znovu za %.1fs [%d/%d]",
                        fn.__name__, exc, delay, attempt + 1, times - 1)
                    time.sleep(delay)
            raise last  # pragma: no cover

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


# ---------------------------------------------------------------- text / soubory

def slugify(text, max_len=48):
    norm = unicodedata.normalize("NFKD", str(text))
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return (ascii_text or "item")[:max_len]


def short_hash(*parts):
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8"))
    return digest.hexdigest()[:10]


def ensure_dir(path):
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return p


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def human_size(num_bytes):
    step = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if step < 1024 or unit == "GB":
            return f"{step:.1f}{unit}"
        step /= 1024
    return f"{step:.1f}GB"  # pragma: no cover


def env_flag(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "ano")


def clamp(value, low, high):
    return max(low, min(high, value))
