"""Render an edit plan with ffmpeg: input seeking per clip, 9:16 framing, concat or
cross-fades, loudness normalisation and optional burned-in captions."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import FFmpegError, MediaInfo, probe, require_binaries
from .planner import Plan

ASPECTS = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


@dataclass
class RenderSettings:
    fit: str = "crop"  # crop | blur | none
    aspect: str = "9:16"  # 9:16 | 4:5 | 1:1 | 16:9 | source
    fps: int = 30
    transition: str = "cut"  # cut | fade
    transition_duration: float = 0.25
    crf: int = 18
    preset: str = "medium"
    loudnorm: bool = True
    audio_bitrate: str = "192k"
    edge_fade: float = 0.3
    captions: str | None = None  # path to .srt to burn in
    caption_font_size: int = 16
    caption_margin: int = 60
    dry_run: bool = False


def _even(x: float) -> int:
    v = int(round(x))
    return v - (v % 2)


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _frame_filter(info: MediaInfo, settings: RenderSettings, cx: float, cy: float) -> str:
    """Scale/crop chain turning the source frame into the output frame."""
    if settings.aspect == "source" or settings.fit == "none":
        return f"scale={_even(info.width)}:{_even(info.height)}:flags=lanczos"
    tw, th = ASPECTS[settings.aspect]
    r_src, r_t = info.width / info.height, tw / th
    if settings.fit == "blur":
        return (
            f"split[bg][fg];"
            f"[bg]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=24:6[bgb];"
            f"[fg]scale={tw}:{th}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )
    if abs(r_src - r_t) < 0.01:
        return f"scale={tw}:{th}:flags=lanczos"
    if r_src > r_t:  # source wider: crop width around cx
        cw = _even(info.height * r_t)
        cw = min(cw, _even(info.width))
        x = int(round(cx * info.width - cw / 2))
        x = max(0, min(x, info.width - cw))
        return f"crop={cw}:{_even(info.height)}:{x}:0,scale={tw}:{th}:flags=lanczos"
    ch = _even(info.width / r_t)
    ch = min(ch, _even(info.height))
    y = int(round(cy * info.height - ch / 2))
    y = max(0, min(y, info.height - ch))
    return f"crop={_even(info.width)}:{ch}:0:{y},scale={tw}:{th}:flags=lanczos"


def build_command(plan: Plan, info: MediaInfo, settings: RenderSettings, out_path: str) -> list[str]:
    if not plan.clips:
        raise ValueError("Plan has no clips to render")
    if settings.aspect not in ASPECTS and settings.aspect != "source":
        raise ValueError(f"Unknown aspect {settings.aspect}; choose from {', '.join(ASPECTS)} or source")
    n = len(plan.clips)
    has_audio = info.has_audio
    use_fade = settings.transition == "fade" and n > 1
    fd = settings.transition_duration
    if use_fade:
        shortest = min(c.duration for c in plan.clips)
        fd = max(0.05, min(fd, shortest / 2 - 0.01))

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-nostdin", "-y"]
    for c in plan.clips:
        cmd += ["-ss", f"{c.start:.3f}", "-t", f"{c.duration:.3f}", "-i", info.path]

    parts: list[str] = []
    for i, c in enumerate(plan.clips):
        vf = _frame_filter(info, settings, c.crop_cx, c.crop_cy)
        parts.append(
            f"[{i}:v]{vf},fps={settings.fps},setsar=1,format=yuv420p,trim=duration={c.duration:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        if has_audio:
            fade_out_start = max(0.0, c.duration - 0.03)
            parts.append(
                f"[{i}:a]aresample=48000,atrim=duration={c.duration:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03[a{i}]"
            )

    if use_fade:
        prev_v, prev_a = "v0", "a0"
        offset = 0.0
        for i in range(1, n):
            offset += plan.clips[i - 1].duration - fd
            parts.append(f"[{prev_v}][v{i}]xfade=transition=fade:duration={fd:.3f}:offset={offset:.3f}[x{i}]")
            prev_v = f"x{i}"
            if has_audio:
                parts.append(f"[{prev_a}][a{i}]acrossfade=d={fd:.3f}:c1=tri:c2=tri[ax{i}]")
                prev_a = f"ax{i}"
        total = sum(c.duration for c in plan.clips) - fd * (n - 1)
        vc, ac = prev_v, prev_a
    else:
        if n == 1:
            vc, ac = "v0", "a0"
        else:
            inputs = "".join(f"[v{i}][a{i}]" if has_audio else f"[v{i}]" for i in range(n))
            parts.append(f"{inputs}concat=n={n}:v=1:a={1 if has_audio else 0}[vc]" + ("[ac]" if has_audio else ""))
            vc, ac = "vc", "ac"
        total = sum(c.duration for c in plan.clips)

    ef = min(settings.edge_fade, total / 4)
    post_v = f"[{vc}]"
    if ef > 0:
        post_v += f"fade=t=in:st=0:d={ef:.3f},fade=t=out:st={max(0.0, total - ef):.3f}:d={ef:.3f},"
    if settings.captions:
        style = (
            f"FontName=DejaVu Sans,FontSize={settings.caption_font_size},Bold=1,PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV={settings.caption_margin}"
        )
        post_v += f"subtitles=filename='{_escape_filter_path(settings.captions)}':force_style='{style}',"
    post_v = post_v.rstrip(",") + "[vout]"
    parts.append(post_v)

    maps = ["-map", "[vout]"]
    if has_audio:
        post_a = f"[{ac}]"
        if ef > 0:
            post_a += f"afade=t=in:st=0:d={ef:.3f},afade=t=out:st={max(0.0, total - ef):.3f}:d={ef:.3f},"
        if settings.loudnorm:
            post_a += "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
        post_a = post_a.rstrip(",") + "[aout]"
        parts.append(post_a)
        maps += ["-map", "[aout]"]

    cmd += ["-filter_complex", ";".join(parts)] + maps
    cmd += [
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p", "-r", str(settings.fps),
        "-movflags", "+faststart",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", settings.audio_bitrate, "-ar", "48000"]
    cmd.append(out_path)
    return cmd


def render(plan: Plan, settings: RenderSettings, out_path: str, info: MediaInfo | None = None) -> list[str]:
    """Render the plan to ``out_path``. Returns the ffmpeg command used."""
    require_binaries()
    info = info or probe(plan.source)
    cmd = build_command(plan, info, settings, out_path)
    if settings.dry_run:
        return cmd
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise FFmpegError("Render failed:\n" + "\n".join(proc.stderr.strip().splitlines()[-25:]))
    return cmd


__all__ = ["ASPECTS", "RenderSettings", "build_command", "render"]
