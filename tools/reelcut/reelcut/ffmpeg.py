"""Thin wrappers around ffmpeg / ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class FFmpegError(RuntimeError):
    """Raised when ffmpeg/ffprobe is missing or a command fails."""


def require_binaries() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise FFmpegError(
            "Missing required binaries: "
            + ", ".join(missing)
            + ". Install ffmpeg (https://ffmpeg.org/download.html) and make sure it is on PATH."
        )


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, capturing output. On failure raise FFmpegError with the stderr tail."""
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
        raise FFmpegError(f"Command failed with code {proc.returncode}: {cmd[0]}\n{tail}")
    return proc


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int  # display width (rotation applied)
    height: int  # display height (rotation applied)
    fps: float
    rotation: int
    has_audio: bool
    audio_sample_rate: int | None
    video_codec: str
    size_bytes: int
    mtime: float

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 1.0

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "rotation": self.rotation,
            "has_audio": self.has_audio,
            "audio_sample_rate": self.audio_sample_rate,
            "video_codec": self.video_codec,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MediaInfo":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


def _parse_rate(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    """Return basic stream information for a media file."""
    require_binaries()
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Input file not found: {p}")
    out = run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
    ).stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise FFmpegError(f"No video stream found in {p}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    rotation = 0
    for sd in video.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                rotation = 0
            break
    tag_rot = (video.get("tags") or {}).get("rotate")
    if rotation == 0 and tag_rot is not None:
        try:
            rotation = int(float(tag_rot))
        except ValueError:
            rotation = 0
    rotation %= 360

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if rotation in (90, 270):
        width, height = height, width
    if width <= 0 or height <= 0:
        raise FFmpegError(f"Could not determine video dimensions of {p}")

    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate")) or 30.0
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    if duration <= 0:
        raise FFmpegError(f"Could not determine duration of {p}")

    stat = p.stat()
    return MediaInfo(
        path=str(p),
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        rotation=rotation,
        has_audio=audio is not None,
        audio_sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        video_codec=video.get("codec_name", "unknown"),
        size_bytes=stat.st_size,
        mtime=stat.st_mtime,
    )


def format_timecode(seconds: float) -> str:
    """mm:ss.d style timecode used in reports."""
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:04.1f}"
