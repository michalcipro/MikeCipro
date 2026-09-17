"""Obálka nad ffmpeg/ffprobe.

Binárku hledáme v tomto pořadí:
  1. proměnná prostředí FFMPEG_BINARY / FFPROBE_BINARY
  2. systémový ffmpeg v PATH
  3. statická binárka z balíčku `imageio-ffmpeg` (fallback, ať to jede všude)

ffprobe nemusí být k dispozici (imageio-ffmpeg ho nedodává), takže `probe()`
umí informace vytáhnout i z výstupu samotného ffmpeg.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from ..errors import MediaError
from ..util import get_logger

log = get_logger(__name__)


@lru_cache(maxsize=1)
def ffmpeg_path():
    explicit = os.getenv("FFMPEG_BINARY")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # noqa: PLC0415 - volitelná závislost

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise MediaError(
            "Nenašel jsem ffmpeg. Nainstaluj ho systémově (apt install ffmpeg / brew install "
            "ffmpeg) nebo doinstaluj `pip install imageio-ffmpeg`.") from exc


@lru_cache(maxsize=1)
def ffprobe_path():
    explicit = os.getenv("FFPROBE_BINARY")
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("ffprobe")


def run(args, capture=True, check=True, timeout=1800, quiet=True):
    """Spustí ffmpeg s danými argumenty (bez názvu binárky)."""
    cmd = [ffmpeg_path(), "-hide_banner"]
    if quiet:
        cmd += ["-loglevel", "error", "-nostdin", "-y"]
    else:
        cmd += ["-nostdin", "-y"]
    cmd += [str(a) for a in args]
    log.debug("ffmpeg %s", " ".join(cmd[1:]))
    proc = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, check=False)
    if check and proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-12:]
        raise MediaError("ffmpeg selhal:\n" + "\n".join(tail))
    return proc


def run_filter_probe(args, timeout=900):
    """Spustí ffmpeg s `-f null` a vrátí spojený stdout+stderr.

    Filtry `metadata=print` a `ametadata=print` píší do stdout, zatímco
    `silencedetect` loguje do stderr — proto sbíráme obojí.
    """
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", *[str(a) for a in args]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


@lru_cache(maxsize=1)
def has_filter(name="drawtext"):
    """Je daný filtr v této ffmpeg buildu zkompilovaný?"""
    proc = subprocess.run([ffmpeg_path(), "-hide_banner", "-filters"],
                          capture_output=True, text=True, check=False)
    return bool(re.search(rf"^\s*\S+\s+{re.escape(name)}\s", proc.stdout or "", re.M))


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?Video:.*?(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s+fps")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?Audio:")
_ROTATE_RE = re.compile(r"rotate\s*:\s*(-?\d+)")


def probe(path):
    """Vrátí {duration, width, height, fps, has_audio, rotation} pro video či fotku."""
    path = str(path)
    if not Path(path).exists():
        raise MediaError(f"Soubor neexistuje: {path}")

    probe_bin = ffprobe_path()
    if probe_bin:
        proc = subprocess.run(
            [probe_bin, "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", path], capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            try:
                return _parse_ffprobe(json.loads(proc.stdout))
            except (ValueError, KeyError):
                log.debug("ffprobe vrátil neparsovatelný JSON, zkouším ffmpeg.")

    proc = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", path],
                          capture_output=True, text=True, check=False)
    return _parse_ffmpeg_stderr(proc.stderr or "")


def _parse_ffprobe(data):
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = 0.0
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    if "/" in rate:
        num, _, den = rate.partition("/")
        fps = float(num) / float(den) if float(den or 0) else 0.0
    duration = float(data.get("format", {}).get("duration")
                     or video.get("duration") or 0.0)
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(side["rotation"])
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 3),
        "has_audio": audio is not None,
        "rotation": rotation,
        "codec": video.get("codec_name"),
    }


def _parse_ffmpeg_stderr(text):
    duration = 0.0
    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    width = height = 0
    vmatch = _VIDEO_RE.search(text)
    if vmatch:
        width, height = int(vmatch.group(1)), int(vmatch.group(2))
    fps = 0.0
    fmatch = _FPS_RE.search(text)
    if fmatch:
        fps = float(fmatch.group(1))
    rotation = 0
    rmatch = _ROTATE_RE.search(text)
    if rmatch:
        rotation = int(rmatch.group(1))
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "has_audio": bool(_AUDIO_RE.search(text)),
        "rotation": rotation,
        "codec": None,
    }


def available():
    """Diagnostika pro `igagent doctor`."""
    try:
        binary = ffmpeg_path()
    except MediaError as exc:
        return {"ok": False, "error": str(exc)}
    proc = subprocess.run([binary, "-version"], capture_output=True, text=True, check=False)
    first = (proc.stdout or "").splitlines()[:1]
    return {"ok": proc.returncode == 0, "binary": binary,
            "version": first[0] if first else "?", "ffprobe": ffprobe_path(),
            "drawtext": has_filter("drawtext"), "zoompan": has_filter("zoompan"),
            "xfade": has_filter("xfade")}
