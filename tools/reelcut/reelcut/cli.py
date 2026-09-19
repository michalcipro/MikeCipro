"""Command line interface: reelcut analyze | plan | cut | render."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .analysis import STYLES, AnalysisSettings, get_analysis, resolve_style
from . import ffmpeg as ff
from .ffmpeg import FFmpegError
from .planner import Plan, PlanSettings, build_plan
from .render import ASPECTS, RenderSettings, render
from .report import format_analysis, format_plan, timeline_png


def _log(msg: str) -> None:
    print(f"[reelcut] {msg}", file=sys.stderr, flush=True)


def _parse_weights(text: str | None) -> dict[str, float] | None:
    if not text:
        return None
    out: dict[str, float] = {}
    for item in text.split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"bad weight '{item}', expected name=value")
        k, v = item.split("=", 1)
        out[k.strip()] = float(v)
    return out


def _add_analysis_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("analysis")
    g.add_argument("--style", default="auto", choices=sorted(STYLES), help="scoring profile (default: auto)")
    g.add_argument("--weights", type=_parse_weights, default=None,
                   help="override style weights, e.g. motion=2,speech=0.5 (curves: audio,motion,faces,sharp,color,exposure,speech)")
    g.add_argument("--sample-fps", type=float, default=8.0, help="frames per second analysed (default 8)")
    g.add_argument("--no-faces", action="store_true", help="skip face detection")
    g.add_argument("--transcribe", action="store_true", help="transcribe speech with faster-whisper (sentence-accurate cuts, captions)")
    g.add_argument("--language", default=None, help="transcription language code, e.g. cs, en (default: auto)")
    g.add_argument("--whisper-model", default="small", help="faster-whisper model size (default small)")
    g.add_argument("--vad-threshold", type=float, default=0.35, help="speech detection sensitivity 0..1 (lower = more speech)")
    g.add_argument("--no-cache", action="store_true", help="ignore/skip the <input>.reelcut.json analysis cache")


def _add_plan_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("planning")
    g.add_argument("-t", "--target", type=float, default=30.0, help="target reel length in seconds (default 30)")
    g.add_argument("--min-clip", type=float, default=1.0, help="shortest visual clip (default 1.0s)")
    g.add_argument("--max-clip", type=float, default=5.0, help="longest visual clip (default 5.0s)")
    g.add_argument("--max-speech-clip", type=float, default=15.0, help="split speech longer than this at pauses (default 15s)")
    g.add_argument("--speech", dest="speech_mode", default="keep", choices=["keep", "prefer", "ignore"],
                   help="keep: speech first and whole; prefer: speech gets a bonus; ignore: treat like any footage")
    g.add_argument("--no-hook", action="store_true", help="do not open with the strongest moment")
    g.add_argument("--hook-length", type=float, default=2.5, help="cold-open length in seconds (default 2.5)")
    g.add_argument("--order", default="chrono", choices=["chrono", "score"], help="clip order after the hook")
    g.add_argument("--no-snap", action="store_true", help="do not snap cuts to audio transients")
    g.add_argument("--min-quality", type=float, default=0.3,
                   help="skip visual clips scoring below this fraction of the best clip (default 0.3; 0 = fill at any cost)")


def _add_render_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("render")
    g.add_argument("-o", "--output", default=None, help="output file (default <input>_reel.mp4)")
    g.add_argument("--aspect", default="9:16", choices=sorted(ASPECTS) + ["source"], help="output aspect (default 9:16)")
    g.add_argument("--fit", default="crop", choices=["crop", "blur", "none"],
                   help="crop: smart crop following faces; blur: blurred background; none: keep source frame")
    g.add_argument("--transition", default="cut", choices=["cut", "fade"], help="hard cuts (default) or cross-fades")
    g.add_argument("--transition-duration", type=float, default=0.25)
    g.add_argument("--fps", type=int, default=30)
    g.add_argument("--crf", type=int, default=18, help="x264 quality, lower is better (default 18)")
    g.add_argument("--preset", default="medium", help="x264 preset (default medium)")
    g.add_argument("--no-loudnorm", action="store_true", help="skip -14 LUFS loudness normalisation")
    g.add_argument("--captions", action="store_true", help="burn word-group captions (needs --transcribe)")
    g.add_argument("--caption-size", type=int, default=16, help="caption font size (default 16)")
    g.add_argument("--dry-run", action="store_true", help="print the ffmpeg command instead of rendering")


def _analysis_settings(a: argparse.Namespace) -> AnalysisSettings:
    return AnalysisSettings(
        sample_fps=a.sample_fps,
        faces=not a.no_faces,
        transcribe=a.transcribe,
        language=a.language,
        whisper_model=a.whisper_model,
        vad_threshold=a.vad_threshold,
    )


def _plan_settings(a: argparse.Namespace) -> PlanSettings:
    return PlanSettings(
        target=a.target,
        min_clip=a.min_clip,
        max_clip=a.max_clip,
        max_speech_clip=a.max_speech_clip,
        speech_mode=a.speech_mode,
        hook=not a.no_hook,
        hook_length=a.hook_length,
        order=a.order,
        snap_onsets=not a.no_snap,
        min_quality=a.min_quality,
    )


def _render_settings(a: argparse.Namespace, captions_path: str | None) -> RenderSettings:
    return RenderSettings(
        fit=a.fit,
        aspect=a.aspect,
        fps=a.fps,
        transition=a.transition,
        transition_duration=a.transition_duration,
        crf=a.crf,
        preset=a.preset,
        loudnorm=not a.no_loudnorm,
        captions=captions_path,
        caption_font_size=a.caption_size,
        dry_run=a.dry_run,
    )


def _render_progress():
    shown = {"last": -1}

    def cb(frac: float) -> None:
        pct = int(frac * 100)
        if pct >= shown["last"] + 10 or (pct == 100 and shown["last"] != 100):
            shown["last"] = pct
            _log(f"render {pct}%")

    return cb


def _default_output(inp: str, suffix: str, ext: str) -> str:
    p = Path(inp)
    return str(p.with_name(p.stem + suffix + ext))


def _write_captions(an, plan: Plan, out_video: str) -> str | None:
    from .captions import build_srt

    words = (an.transcript or {}).get("words") if an.transcript else None
    if not words:
        _log("captions requested but no transcript is available; run with --transcribe (needs faster-whisper)")
        return None
    srt_path = _default_output(out_video, "", ".srt")
    Path(srt_path).write_text(build_srt(plan, words), encoding="utf-8")
    _log(f"captions written to {srt_path}")
    return srt_path


def cmd_analyze(a: argparse.Namespace) -> int:
    style = resolve_style(a.style, a.weights)
    an = get_analysis(a.input, _analysis_settings(a), use_cache=not a.no_cache, progress=_log)
    print(format_analysis(an, style))
    if a.json:
        an.save(a.json)
        _log(f"analysis saved to {a.json}")
    if a.timeline:
        timeline_png(an, style, a.timeline)
        _log(f"timeline image written to {a.timeline}")
    return 0


def cmd_plan(a: argparse.Namespace) -> int:
    style = resolve_style(a.style, a.weights)
    an = get_analysis(a.input, _analysis_settings(a), use_cache=not a.no_cache, progress=_log)
    plan = build_plan(an, _plan_settings(a), style)
    print(format_plan(plan))
    out = a.json or _default_output(a.input, "_plan", ".json")
    plan.save(out)
    _log(f"plan saved to {out} (edit it and run: reelcut render {out})")
    if a.timeline:
        timeline_png(an, style, a.timeline, plan)
        _log(f"timeline image written to {a.timeline}")
    return 0


def cmd_cut(a: argparse.Namespace) -> int:
    style = resolve_style(a.style, a.weights)
    an = get_analysis(a.input, _analysis_settings(a), use_cache=not a.no_cache, progress=_log)
    plan = build_plan(an, _plan_settings(a), style)
    print(format_plan(plan))
    if not plan.clips:
        _log("nothing to render")
        return 2
    out = a.output or _default_output(a.input, "_reel", ".mp4")
    plan.save(_default_output(out, "_plan", ".json"))
    captions = _write_captions(an, plan, out) if a.captions else None
    if a.timeline:
        timeline_png(an, style, a.timeline, plan)
        _log(f"timeline image written to {a.timeline}")
    settings = _render_settings(a, captions)
    _log(f"rendering {plan.total:.1f}s reel to {out}")
    cmd = render(plan, settings, out, an.source, progress=None if a.dry_run else _render_progress())
    if a.dry_run:
        print(" ".join(_quote(c) for c in cmd))
    else:
        _log(f"done: {out}")
    return 0


def cmd_render(a: argparse.Namespace) -> int:
    plan = Plan.load(a.plan)
    if a.input:
        plan.source = a.input
    out = a.output or _default_output(plan.source, "_reel", ".mp4")
    captions = None
    if a.captions:
        from .analysis import cache_path, Analysis

        cp = cache_path(plan.source)
        if cp.is_file():
            captions = _write_captions(Analysis.load(cp), plan, out)
        else:
            _log("captions need the analysis cache next to the source; run 'reelcut analyze --transcribe' first")
    print(format_plan(plan))
    settings = _render_settings(a, captions)
    _log(f"rendering {plan.total:.1f}s reel to {out}")
    cmd = render(plan, settings, out, progress=None if a.dry_run else _render_progress())
    if a.dry_run:
        print(" ".join(_quote(c) for c in cmd))
    else:
        _log(f"done: {out}")
    return 0


def cmd_web(a: argparse.Namespace) -> int:
    from pathlib import Path as _P

    from .web import serve

    serve(a.host, a.port, open_browser=not a.no_browser, workdir=_P(a.workdir) if a.workdir else None)
    return 0


def cmd_setup(a: argparse.Namespace) -> int:
    """Check (and, without admin rights, download) everything reelcut needs."""
    import cv2

    from .render import pick_video_encoder
    from .video import MODEL_PATH

    ok = True
    print(f"python   {sys.version.split()[0]}  ({sys.executable})")
    binaries: dict[str, str | None] = {}
    for name in ("ffmpeg", "ffprobe"):
        try:
            binaries[name] = ff.find_binary(name, download=not a.no_download, log=_log)
        except FFmpegError as exc:
            _log(str(exc))
            binaries[name] = None
    for name, path in binaries.items():
        if path:
            print(f"{name:9}{ff.version_line(path)}\n         {path}")
        else:
            print(f"{name:9}MISSING")
            ok = False
    if all(binaries.values()):
        caps = ff.capabilities()
        for filt, feature in (
            ("xfade", "--transition fade"),
            ("loudnorm", "loudness normalisation"),
            ("subtitles", "--captions (burned-in captions)"),
        ):
            state = "ok" if filt in caps["filters"] else f"missing -> {feature} unavailable"
            print(f"filter   {filt:10}{state}")
        try:
            print(f"encoder  {pick_video_encoder()}")
        except FFmpegError as exc:
            print(f"encoder  MISSING ({exc})")
            ok = False
    faces_ok = hasattr(cv2, "FaceDetectorYN") and MODEL_PATH.is_file()
    print(f"faces    {'ok (YuNet)' if faces_ok else 'unavailable (OpenCV without FaceDetectorYN)'}")
    for module, extra, feature in (("webrtcvad", "vad", "better speech detection"), ("faster_whisper", "transcribe", "transcript + captions")):
        try:
            __import__(module)
            print(f"optional {module:15}installed ({feature})")
        except ImportError:
            print(f"optional {module:15}not installed ({feature}); pip install 'reelcut[{extra}]'")
    print("\nReady: run  reelcut cut video.mp4 -t 30" if ok else "\nNot ready: see the MISSING lines above.")
    return 0 if ok else 1


def _quote(s: str) -> str:
    return s if all(ch.isalnum() or ch in "-_./:=+,[]" for ch in s) else "'" + s.replace("'", "'\\''") + "'"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reelcut",
        description="Analyse a video and cut its best moments into an Instagram Reel. Speech and interviews are kept whole.",
    )
    p.add_argument("--version", action="version", version=f"reelcut {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pa = sub.add_parser("analyze", help="analyse the video and print shots, speech and strongest moments")
    pa.add_argument("input")
    pa.add_argument("--json", default=None, help="also save the full analysis to this file")
    pa.add_argument("--timeline", default=None, help="write a timeline PNG")
    _add_analysis_args(pa)
    pa.set_defaults(func=cmd_analyze)

    pp = sub.add_parser("plan", help="build the edit plan (EDL) without rendering")
    pp.add_argument("input")
    pp.add_argument("--json", default=None, help="plan output path (default <input>_plan.json)")
    pp.add_argument("--timeline", default=None, help="write a timeline PNG with the chosen clips")
    _add_analysis_args(pp)
    _add_plan_args(pp)
    pp.set_defaults(func=cmd_plan)

    pc = sub.add_parser("cut", help="analyse, plan and render the reel in one go")
    pc.add_argument("input")
    pc.add_argument("--timeline", default=None, help="write a timeline PNG with the chosen clips")
    _add_analysis_args(pc)
    _add_plan_args(pc)
    _add_render_args(pc)
    pc.set_defaults(func=cmd_cut)

    pw = sub.add_parser("web", help="start the local web UI (http://127.0.0.1:8765)")
    pw.add_argument("--port", type=int, default=8765)
    pw.add_argument("--host", default="127.0.0.1", help="bind address (keep 127.0.0.1 unless you know why)")
    pw.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    pw.add_argument("--workdir", default=None, help="where uploads and results are stored (default ~/.reelcut/web)")
    pw.set_defaults(func=cmd_web)

    ps = sub.add_parser("setup", help="check the installation; downloads ffmpeg without admin rights if missing")
    ps.add_argument("--no-download", action="store_true", help="only report, never download ffmpeg")
    ps.set_defaults(func=cmd_setup)

    pr = sub.add_parser("render", help="render a (hand-edited) plan JSON")
    pr.add_argument("plan")
    pr.add_argument("--input", default=None, help="override the source video path stored in the plan")
    _add_render_args(pr)
    pr.set_defaults(func=cmd_render)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        _log(f"error: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
