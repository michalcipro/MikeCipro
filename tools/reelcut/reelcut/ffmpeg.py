"""Thin wrappers around ffmpeg / ffprobe, including binary discovery.

Binaries are looked up in this order:

1. ``REELCUT_FFMPEG`` / ``REELCUT_FFPROBE`` (explicit paths)
2. ``REELCUT_FFMPEG_DIR`` (a directory holding both)
3. ``~/.reelcut/bin``
4. the ``PATH``
5. a static build previously downloaded by ``reelcut setup`` (``~/.reelcut/ffmpeg``)
6. as a last resort, download a static build with the ``static-ffmpeg`` package
   (works without administrator rights, ~100 MB, once).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable


class FFmpegError(RuntimeError):
    """Raised when ffmpeg/ffprobe is missing or a command fails."""


REELCUT_HOME = Path(os.environ.get("REELCUT_HOME", Path.home() / ".reelcut"))
_EXE = ".exe" if sys.platform == "win32" else ""
_cache: dict[str, str] = {}

INSTALL_HINT = (
    "ffmpeg/ffprobe not found. Run 'reelcut setup' to download a static build without "
    "administrator rights, or install ffmpeg (brew install ffmpeg / winget install ffmpeg) "
    "and make sure it is on PATH."
)


def _static_dir() -> Path | None:
    """Directory where static-ffmpeg stores binaries for this platform (may not exist yet)."""
    try:
        from static_ffmpeg.run import get_platform_key  # type: ignore
    except ImportError:
        return None
    return REELCUT_HOME / "ffmpeg" / get_platform_key()


def _download_static(log: Callable[[str], None] | None) -> tuple[str, str] | None:
    try:
        from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise  # type: ignore
    except ImportError:
        if log:
            log("ffmpeg not found and the 'static-ffmpeg' package is not installed, so it cannot be "
                "downloaded automatically: pip install static-ffmpeg")
        return None
    target = _static_dir()
    if target is None:
        return None
    if log:
        log("ffmpeg not found on PATH; downloading a static build (~100 MB, one time) into " + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg, ffprobe = get_or_fetch_platform_executables_else_raise(download_dir=str(target))
    except Exception as exc:  # network errors, unsupported platform
        raise FFmpegError(f"Automatic ffmpeg download failed: {exc}\n{INSTALL_HINT}") from exc
    return ffmpeg, ffprobe


def find_binary(name: str, *, download: bool = False, log: Callable[[str], None] | None = None) -> str | None:
    """Locate ``ffmpeg`` or ``ffprobe`` following the documented search order."""
    if name in _cache:
        return _cache[name]
    candidates: list[Path] = []
    explicit = os.environ.get(f"REELCUT_{name.upper()}")
    if explicit:
        candidates.append(Path(explicit))
    env_dir = os.environ.get("REELCUT_FFMPEG_DIR")
    if env_dir:
        candidates.append(Path(env_dir) / (name + _EXE))
    candidates.append(REELCUT_HOME / "bin" / (name + _EXE))
    on_path = shutil.which(name)
    if on_path:
        candidates.append(Path(on_path))
    static = _static_dir()
    if static is not None:
        candidates.append(static / (name + _EXE))
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            _cache[name] = str(c)
            return _cache[name]
    if download and not _cache.get("_download_failed"):
        try:
            fetched = _download_static(log)
        except FFmpegError:
            _cache["_download_failed"] = "1"
            raise
        if fetched:
            _cache["ffmpeg"], _cache["ffprobe"] = fetched
            return _cache[name]
    return None


def ffmpeg_bin() -> str:
    path = find_binary("ffmpeg", download=True, log=_stderr)
    if not path:
        raise FFmpegError(INSTALL_HINT)
    return path


def ffprobe_bin() -> str:
    path = find_binary("ffprobe", download=True, log=_stderr)
    if not path:
        raise FFmpegError(INSTALL_HINT)
    return path


def _stderr(msg: str) -> None:
    print(f"[reelcut] {msg}", file=sys.stderr, flush=True)


def require_binaries() -> None:
    ffmpeg_bin()
    ffprobe_bin()


def reset_cache() -> None:
    _cache.clear()


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, capturing output. On failure raise FFmpegError with the stderr tail."""
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
        raise FFmpegError(f"Command failed with code {proc.returncode}: {cmd[0]}\n{tail}")
    return proc


def version_line(binary: str) -> str:
    try:
        out = run([binary, "-version"], check=False).stdout
    except OSError as exc:
        return f"not runnable ({exc})"
    return out.splitlines()[0] if out else "unknown"


def capabilities() -> dict[str, set[str]]:
    """Filters and encoders the resolved ffmpeg supports (cached)."""
    if "caps" in _cache:  # type: ignore[comparison-overlap]
        return _cache["caps"]  # type: ignore[return-value]
    binary = ffmpeg_bin()
    filters: set[str] = set()
    for line in run([binary, "-hide_banner", "-filters"], check=False).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and set(parts[0]) <= set("TSC."):
            filters.add(parts[1])
    encoders: set[str] = set()
    for line in run([binary, "-hide_banner", "-encoders"], check=False).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            encoders.add(parts[1])
    caps = {"filters": filters, "encoders": encoders}
    _cache["caps"] = caps  # type: ignore[assignment]
    return caps


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
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Input file not found: {p}")
    out = run(
        [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
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
