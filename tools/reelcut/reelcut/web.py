"""Local web UI (``reelcut web``): upload a video, pick length and style, watch
progress, preview and download the reel, drop clips and re-render.

Binds to 127.0.0.1 only; nothing leaves the machine. Jobs run one at a time in
a background thread; the browser polls ``/api/jobs/<id>``.
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from . import __version__
from .analysis import STYLES, AnalysisSettings, combine_analyses, get_analysis, resolve_style
from .ffmpeg import REELCUT_HOME, FFmpegError, MediaInfo, ffmpeg_bin, probe
from .planner import Clip, Plan, PlanSettings, build_plan, finalize_timeline
from .render import ASPECTS, RenderSettings, pick_video_encoder, render
from .report import timeline_png

WEBUI_DIR = Path(__file__).parent / "webui"
PHASES = {
    "queued": "Ve frontě",
    "analyzing": "Analýza videa",
    "planning": "Plán střihu",
    "rendering": "Render",
    "done": "Hotovo",
    "error": "Chyba",
}
OUTPUT_FILES = {"reel.mp4": "video/mp4", "timeline.png": "image/png", "plan.json": "application/json", "captions.srt": "text/plain"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".wmv", ".flv"}
MAX_SOURCES_PER_IMPORT = 200


def _has(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


# --- parameters ---------------------------------------------------------------

def _bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _num(v: Any, default: float, lo: float, hi: float) -> float:
    if v is None or v == "":
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"Neplatná hodnota: {v!r}")
    return min(hi, max(lo, x))


def _choice(v: Any, options: tuple[str, ...], default: str) -> str:
    if v is None or v == "":
        return default
    v = str(v)
    if v not in options:
        raise ValueError(f"Neplatná volba {v!r}; povolené: {', '.join(options)}")
    return v


def parse_params(raw: dict) -> dict:
    return {
        "target": _num(raw.get("target"), 30.0, 5.0, 180.0),
        "style": _choice(raw.get("style"), tuple(STYLES), "auto"),
        "speech_mode": _choice(raw.get("speech_mode"), ("keep", "prefer", "ignore"), "keep"),
        "hook": _bool(raw.get("hook"), True),
        "hook_length": _num(raw.get("hook_length"), 2.5, 1.0, 6.0),
        "min_clip": _num(raw.get("min_clip"), 1.0, 0.3, 10.0),
        "max_clip": _num(raw.get("max_clip"), 5.0, 0.5, 30.0),
        "max_speech_clip": _num(raw.get("max_speech_clip"), 15.0, 2.0, 90.0),
        "min_quality": _num(raw.get("min_quality"), 0.3, 0.0, 1.0),
        "max_pause": _num(raw.get("max_pause"), 1.0, 0.0, 5.0) if _bool(raw.get("tighten"), True) else 0.0,
        "order": _choice(raw.get("order"), ("chrono", "score"), "chrono"),
        "snap": _bool(raw.get("snap"), True),
        "fit": _choice(raw.get("fit"), ("crop", "blur", "none"), "crop"),
        "aspect": _choice(raw.get("aspect"), tuple(ASPECTS) + ("source",), "9:16"),
        "transition": _choice(raw.get("transition"), ("cut", "fade"), "cut"),
        "transition_duration": _num(raw.get("transition_duration"), 0.25, 0.1, 1.0),
        "crf": int(_num(raw.get("crf"), 18, 10, 35)),
        "preset": _choice(raw.get("preset"), ("ultrafast", "veryfast", "fast", "medium", "slow"), "medium"),
        "loudnorm": _bool(raw.get("loudnorm"), True),
        "transcribe": _bool(raw.get("transcribe"), False),
        "captions": _bool(raw.get("captions"), False),
        "language": (str(raw.get("language")).strip() or None) if raw.get("language") else None,
    }


def _plan_settings(p: dict) -> PlanSettings:
    return PlanSettings(
        target=p["target"], min_clip=p["min_clip"], max_clip=p["max_clip"], max_speech_clip=p["max_speech_clip"],
        speech_mode=p["speech_mode"], hook=p["hook"], hook_length=p["hook_length"], order=p["order"],
        snap_onsets=p["snap"], min_quality=p["min_quality"], max_pause=p.get("max_pause", 1.0),
    )


def _render_settings(p: dict, captions: str | None) -> RenderSettings:
    return RenderSettings(
        fit=p["fit"], aspect=p["aspect"], transition=p["transition"], transition_duration=p["transition_duration"],
        crf=p["crf"], preset=p["preset"], loudnorm=p["loudnorm"], captions=captions,
    )


# --- state --------------------------------------------------------------------

@dataclass
class Source:
    id: str
    path: str
    name: str
    info: MediaInfo
    uploaded: bool
    created: float = field(default_factory=time.time)
    proxy_path: str | None = None  # browser-friendly 540p H.264 copy for the preview player
    proxy_state: str = "none"  # none (original plays fine) | pending | ready | failed

    @property
    def media_path(self) -> str:
        return self.proxy_path if self.proxy_state == "ready" and self.proxy_path else self.path

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "path": self.path, "uploaded": self.uploaded,
            "duration": self.info.duration, "width": self.info.width, "height": self.info.height,
            "fps": self.info.fps, "has_audio": self.info.has_audio, "codec": self.info.video_codec,
            "media_url": f"/api/sources/{self.id}/media", "proxy": self.proxy_state,
        }

    def to_json(self) -> dict:
        return {"id": self.id, "path": self.path, "name": self.name, "uploaded": self.uploaded,
                "created": self.created, "proxy_path": self.proxy_path, "proxy_state": self.proxy_state}


def _needs_proxy(info: MediaInfo) -> bool:
    return info.video_codec != "h264" or info.width * info.height > 1920 * 1080


@dataclass
class Job:
    id: str
    source_ids: list[str]
    params: dict
    dir: Path
    clips_override: list[dict] | None = None
    state: str = "queued"
    progress: float = 0.0
    log: list[str] = field(default_factory=list)
    plan: Plan | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None

    @property
    def source_id(self) -> str:
        return self.source_ids[0]

    def file_url(self, name: str) -> str | None:
        return f"/api/jobs/{self.id}/{name}" if (self.dir / name).is_file() else None

    def thumbs(self) -> list[str | None]:
        if not self.plan:
            return []
        return [f"/api/jobs/{self.id}/thumbs/{i}.jpg" if (self.dir / "thumbs" / f"{i}.jpg").is_file() else None
                for i in range(len(self.plan.clips))]

    def to_json(self) -> dict:
        return {"id": self.id, "source_ids": self.source_ids, "params": self.params, "state": self.state,
                "error": self.error, "log": self.log[-60:], "created": self.created, "finished": self.finished,
                "clips_override": self.clips_override}

    def to_dict(self, sources: list[Source]) -> dict:
        stem = Path(sources[0].name).stem if sources else "video"
        if len(sources) > 1:
            stem += f"_+{len(sources) - 1}"
        return {
            "id": self.id,
            "state": self.state,
            "phase": PHASES.get(self.state, self.state),
            "progress": round(self.progress, 3),
            "log": self.log[-60:],
            "error": self.error,
            "params": self.params,
            "source": sources[0].to_dict() if sources else None,
            "sources": [s.to_dict() for s in sources],
            "plan": self.plan.to_dict() if self.plan else None,
            "output_url": self.file_url("reel.mp4") if self.state == "done" else None,
            "download_name": f"{stem}_reel.mp4",
            "timeline_url": self.file_url("timeline.png"),
            "plan_url": self.file_url("plan.json"),
            "captions_url": self.file_url("captions.srt"),
            "thumbs": self.thumbs(),
            "created": self.created,
            "finished": self.finished,
        }


class Manager:
    """Owns sources and jobs; runs jobs sequentially on a worker thread."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        (workdir / "uploads").mkdir(parents=True, exist_ok=True)
        (workdir / "jobs").mkdir(parents=True, exist_ok=True)
        (workdir / "proxies").mkdir(parents=True, exist_ok=True)
        self.sources: dict[str, Source] = {}
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.queue: "queue.Queue[Job]" = queue.Queue()
        self.proxy_queue: "queue.Queue[Source]" = queue.Queue()
        self._load_state()
        self.worker = threading.Thread(target=self._worker, name="reelcut-worker", daemon=True)
        self.worker.start()
        self.proxy_worker = threading.Thread(target=self._proxy_worker, name="reelcut-proxy", daemon=True)
        self.proxy_worker.start()

    # persistence
    def _save_sources(self) -> None:
        with self.lock:
            data = [s.to_json() for s in sorted(self.sources.values(), key=lambda s: s.created)]
        try:
            (self.workdir / "sources.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _save_job(self, job: Job) -> None:
        try:
            (job.dir / "job.json").write_text(json.dumps(job.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _load_state(self) -> None:
        src_file = self.workdir / "sources.json"
        if src_file.is_file():
            try:
                for d in json.loads(src_file.read_text(encoding="utf-8")):
                    path = Path(d["path"])
                    if not path.is_file():
                        continue
                    try:
                        info = probe(path)
                    except (FFmpegError, OSError):
                        continue
                    src = Source(d["id"], str(path), d.get("name", path.name), info, bool(d.get("uploaded")),
                                 float(d.get("created", 0)), d.get("proxy_path"), d.get("proxy_state", "none"))
                    if src.proxy_state == "ready" and not (src.proxy_path and Path(src.proxy_path).is_file()):
                        src.proxy_state = "none"
                    if src.proxy_state == "pending":
                        src.proxy_state = "none"
                    self.sources[src.id] = src
            except (ValueError, KeyError, TypeError):
                pass
        for jdir in sorted((self.workdir / "jobs").iterdir() if (self.workdir / "jobs").is_dir() else []):
            jf = jdir / "job.json"
            if not jf.is_file():
                continue
            try:
                d = json.loads(jf.read_text(encoding="utf-8"))
                job = Job(d["id"], list(d["source_ids"]), dict(d["params"]), jdir, d.get("clips_override"),
                          d.get("state", "error"), 1.0, list(d.get("log", [])), None, d.get("error"),
                          float(d.get("created", 0)), d.get("finished"))
                if (jdir / "plan.json").is_file():
                    job.plan = Plan.load(jdir / "plan.json")
                if job.state not in ("done", "error"):
                    job.state, job.error = "error", "Přerušeno restartem serveru"
                if job.state == "done" and not (jdir / "reel.mp4").is_file():
                    job.state, job.error = "error", "Výstupní soubor chybí"
                if all(sid in self.sources for sid in job.source_ids):
                    self.jobs[job.id] = job
            except (ValueError, KeyError, TypeError):
                continue

    # preview proxies
    def _proxy_worker(self) -> None:
        while True:
            src = self.proxy_queue.get()
            try:
                self._make_proxy(src)
            except Exception as exc:  # noqa: BLE001
                src.proxy_state = "failed"
                src.proxy_path = None
                print(f"[reelcut] proxy failed for {src.name}: {exc}", flush=True)
            finally:
                self._save_sources()
                self.proxy_queue.task_done()

    def _make_proxy(self, src: Source) -> None:
        out = Path(src.proxy_path) if src.proxy_path else self.workdir / "proxies" / f"{src.id}.mp4"
        src.proxy_path = str(out)
        enc = pick_video_encoder()
        vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"] if enc == "libx264" else ["-c:v", enc, "-b:v", "2M"]
        cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", src.path, "-vf", "scale=-2:540,fps=30", *vcodec,
               "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        cmd += ["-c:a", "aac", "-b:a", "96k", "-ac", "2"] if src.info.has_audio else ["-an"]
        cmd.append(str(out))
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise FFmpegError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "ffmpeg failed")
        src.proxy_state = "ready"

    # sources
    def add_upload(self, filename: str, stream: BinaryIO) -> Source:
        sid = uuid.uuid4().hex[:10]
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "video.mp4"
        d = self.workdir / "uploads" / sid
        d.mkdir(parents=True, exist_ok=True)
        path = d / safe
        with open(path, "wb") as fh:
            shutil.copyfileobj(stream, fh, 1024 * 1024)
        try:
            return self._register(sid, path, filename, uploaded=True)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise

    def add_path(self, raw: str) -> Source:
        path = Path(raw.strip()).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Soubor nenalezen: {path}")
        return self._register(uuid.uuid4().hex[:10], path, path.name, uploaded=False)

    def add_paths(self, raws: list[str]) -> tuple[list[Source], list[str]]:
        """Add files and/or folders (every video inside a folder, sorted by name). Returns (added, skipped)."""
        files: list[Path] = []
        skipped: list[str] = []
        for raw in raws:
            path = Path(raw.strip().strip("'\"")).expanduser()
            if path.is_dir():
                found = sorted(
                    (p for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not p.name.startswith(".")),
                    key=lambda p: p.name.lower(),
                )
                if not found:
                    skipped.append(f"{path}: žádné video ve složce")
                files += found
            elif path.is_file():
                files.append(path)
            else:
                skipped.append(f"{path}: nenalezeno")
        if len(files) > MAX_SOURCES_PER_IMPORT:
            skipped.append(f"Přidáno jen prvních {MAX_SOURCES_PER_IMPORT} z {len(files)} souborů")
            files = files[:MAX_SOURCES_PER_IMPORT]
        added: list[Source] = []
        for f in files:
            try:
                added.append(self._register(uuid.uuid4().hex[:10], f, f.name, uploaded=False))
            except (FFmpegError, OSError) as exc:
                skipped.append(f"{f.name}: nejde přečíst jako video ({str(exc).splitlines()[0][:80]})")
        return added, skipped

    def _register(self, sid: str, path: Path, name: str, *, uploaded: bool) -> Source:
        info = probe(path)
        src = Source(sid, str(path), name, info, uploaded)
        if _needs_proxy(info):
            src.proxy_state = "pending"
            src.proxy_path = str((path.parent if uploaded else self.workdir / "proxies") / f"{sid}_preview.mp4")
        with self.lock:
            self.sources[sid] = src
        self._save_sources()
        if src.proxy_state == "pending":
            self.proxy_queue.put(src)
        return src

    # jobs
    def sources_of(self, job: Job) -> list[Source]:
        with self.lock:
            return [self.sources[i] for i in job.source_ids if i in self.sources]

    def submit(self, source_ids: list[str], params: dict, clips: list[dict] | None = None) -> Job:
        if not source_ids:
            raise KeyError("no sources")
        with self.lock:
            for sid in source_ids:
                if sid not in self.sources:
                    raise KeyError(sid)
        jid = uuid.uuid4().hex[:10]
        d = self.workdir / "jobs" / jid
        d.mkdir(parents=True, exist_ok=True)
        job = Job(jid, list(source_ids), params, d, clips)
        with self.lock:
            self.jobs[jid] = job
        job.log.append("Zařazeno do fronty")
        self._save_job(job)
        self.queue.put(job)
        return job

    def get(self, jid: str) -> Job | None:
        with self.lock:
            return self.jobs.get(jid)

    def _worker(self) -> None:
        while True:
            job = self.queue.get()
            try:
                self._run(job)
            except Exception as exc:  # noqa: BLE001 - surface every failure to the UI
                job.state = "error"
                job.error = str(exc)
                job.log.append(f"Chyba: {exc}")
            finally:
                job.finished = time.time()
                self._save_job(job)
                self.queue.task_done()

    def _run(self, job: Job) -> None:
        sources = self.sources_of(job)
        if len(sources) != len(job.source_ids):
            raise RuntimeError("Zdrojové video už není k dispozici; nahrajte ho znovu.")
        source = sources[0]
        p = job.params
        n_src = len(sources)
        current = {"i": 0}

        def log(msg: str) -> None:
            job.log.append(msg)
            m = re.search(r"video analysis (\d+)%", msg)
            if m:
                job.progress = (current["i"] + int(m.group(1)) / 100) / n_src

        job.state = "analyzing"
        job.progress = 0.0
        parts = []
        for i, src in enumerate(sources):
            current["i"] = i
            if n_src > 1:
                log(f"Video {i + 1}/{n_src}: {src.name}")
            parts.append(get_analysis(
                src.path,
                AnalysisSettings(transcribe=p["transcribe"], language=p["language"]),
                use_cache=True,
                progress=log,
            ))
        an = combine_analyses(parts)
        style = resolve_style(p["style"])

        job.state = "planning"
        job.progress = 0.0
        settings = _plan_settings(p)
        if job.clips_override is not None:
            infos = {s.path: s.info for s in sources}
            clips = [Clip.from_dict(c) for c in job.clips_override]
            for c in clips:
                if c.source is None and n_src > 1:
                    c.source = source.path
                info = infos.get(c.source or source.path, source.info)
                c.start = max(0.0, min(c.start, info.duration))
                c.end = max(c.start, min(c.end, info.duration))
            clips = [c for c in clips if c.duration > 0.1]
            finalize_timeline(clips)
            plan = Plan(source.path, style.name, settings, clips, [], an.duration)
            log(f"Ruční plán: {len(clips)} klipů, {plan.total:.1f}s")
        else:
            plan = build_plan(an, settings, style)
            log(f"Plán: {len(plan.clips)} klipů, {plan.total:.1f}s (cíl {settings.target:.0f}s)")
            for w in plan.warnings:
                log("Upozornění: " + w)
        plan.save(job.dir / "plan.json")
        try:
            timeline_png(an, style, job.dir / "timeline.png", plan)
        except Exception as exc:  # noqa: BLE001 - image is optional
            log(f"Timeline se nepodařilo vykreslit: {exc}")
        job.plan = plan
        if not plan.clips:
            raise RuntimeError("Plán neobsahuje žádný klip. Zkuste delší cílovou délku nebo nižší práh kvality.")

        captions = None
        if p["captions"]:
            words = (an.transcript or {}).get("words") if an.transcript else None
            if words:
                from .captions import build_srt

                srt = job.dir / "captions.srt"
                srt.write_text(build_srt(plan, words), encoding="utf-8")
                captions = str(srt)
            else:
                log("Titulky vyžadují přepis (zapněte Přepis řeči, vyžaduje faster-whisper); přeskočeno.")

        job.state = "rendering"
        job.progress = 0.0

        def on_render(frac: float) -> None:
            job.progress = frac

        render(plan, _render_settings(p, captions), str(job.dir / "reel.mp4"), None if an.is_composite else an.source, progress=on_render)
        self._thumbnails(job, plan)
        job.state = "done"
        job.progress = 1.0
        log("Hotovo")

    def _thumbnails(self, job: Job, plan: Plan) -> None:
        tdir = job.dir / "thumbs"
        tdir.mkdir(exist_ok=True)
        sources = {s.path: s for s in self.sources_of(job)}
        for i, c in enumerate(plan.clips):
            path = c.source or plan.source
            src = sources.get(path)
            media = src.media_path if src else path  # the proxy seeks much faster
            t = c.start + min(0.3, c.duration / 2)
            cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", media,
                   "-frames:v", "1", "-vf", "scale=192:-2", "-q:v", "5", str(tdir / f"{i}.jpg")]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- flask app ----------------------------------------------------------------

def create_app(workdir: Path | None = None):
    try:
        from flask import Flask, abort, jsonify, request, send_file
    except ImportError as exc:  # pragma: no cover - optional at import time
        raise RuntimeError("The web UI needs Flask: pip install flask") from exc

    app = Flask("reelcut", static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = None
    manager = Manager(workdir or REELCUT_HOME / "web")
    app.extensions["reelcut_manager"] = manager

    def job_or_404(jid: str) -> Job:
        job = manager.get(jid)
        if job is None:
            abort(404, description="Úloha nenalezena")
        return job

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def _err(e):  # type: ignore[no-untyped-def]
        return jsonify({"error": getattr(e, "description", str(e))}), getattr(e, "code", 500)

    @app.get("/")
    def index():  # type: ignore[no-untyped-def]
        return send_file(WEBUI_DIR / "index.html")

    @app.get("/api/capabilities")
    def capabilities():  # type: ignore[no-untyped-def]
        return jsonify({
            "version": __version__,
            "styles": [{"name": s.name, "description": s.description} for s in STYLES.values()],
            "aspects": list(ASPECTS) + ["source"],
            "transcribe": _has("faster_whisper"),
            "vad": _has("webrtcvad"),
            "workdir": str(manager.workdir),
        })

    @app.post("/api/sources/upload")
    def upload():  # type: ignore[no-untyped-def]
        fh = request.files.get("file")
        if fh is None or not fh.filename:
            abort(400, description="Chybí soubor")
        try:
            src = manager.add_upload(fh.filename, fh.stream)
        except FFmpegError as exc:
            abort(400, description=f"Soubor nejde přečíst jako video: {exc}")
        return jsonify(src.to_dict()), 201

    @app.post("/api/sources/path")
    def source_path():  # type: ignore[no-untyped-def]
        """Add one file, several files or whole folders (JSON: path | paths). Returns {sources, skipped}."""
        data = request.get_json(silent=True) or {}
        raws = data.get("paths")
        if not isinstance(raws, list):
            raws = [line for line in str(data.get("path", "")).splitlines()]
        raws = [str(r).strip() for r in raws if str(r).strip()]
        if not raws:
            abort(400, description="Zadejte cestu k souboru nebo složce")
        added, skipped = manager.add_paths(raws)
        if not added:
            abort(404, description="; ".join(skipped) or "Nic nenalezeno")
        return jsonify({"sources": [s.to_dict() for s in added], "skipped": skipped}), 201

    @app.get("/api/sources/<sid>")
    def source_info(sid: str):  # type: ignore[no-untyped-def]
        src = manager.sources.get(sid)
        if src is None:
            abort(404, description="Zdroj nenalezen")
        return jsonify(src.to_dict())

    @app.get("/api/sources")
    def list_sources():  # type: ignore[no-untyped-def]
        with manager.lock:
            items = sorted(manager.sources.values(), key=lambda s: s.created)
        return jsonify([s.to_dict() for s in items])

    @app.get("/api/sources/<sid>/media")
    def source_media(sid: str):  # type: ignore[no-untyped-def]
        src = manager.sources.get(sid)
        if src is None:
            abort(404, description="Zdroj nenalezen")
        return send_file(src.media_path, conditional=True)

    @app.post("/api/jobs")
    def create_job():  # type: ignore[no-untyped-def]
        data = request.get_json(silent=True) or {}
        ids = data.get("source_ids")
        if not isinstance(ids, list):
            ids = [data.get("source_id", "")]
        ids = [str(i) for i in ids if i]
        if not ids:
            abort(400, description="Chybí zdrojové video")
        try:
            params = parse_params(data)
            job = manager.submit(ids, params)
        except KeyError:
            abort(404, description="Zdroj nenalezen; nahrajte video znovu")
        except ValueError as exc:
            abort(400, description=str(exc))
        return jsonify(job.to_dict(manager.sources_of(job))), 202

    @app.get("/api/jobs")
    def list_jobs():  # type: ignore[no-untyped-def]
        with manager.lock:
            jobs = sorted(manager.jobs.values(), key=lambda j: j.created, reverse=True)
        out = []
        for j in jobs[:50]:
            srcs = manager.sources_of(j)
            out.append({
                "id": j.id, "state": j.state, "phase": PHASES.get(j.state, j.state), "created": j.created,
                "source_name": " + ".join(s.name for s in srcs) or "?", "target": j.params.get("target"), "style": j.params.get("style"),
                "total": round(j.plan.total, 1) if j.plan else None, "clips": len(j.plan.clips) if j.plan else None,
            })
        return jsonify(out)

    @app.get("/api/jobs/<jid>")
    def get_job(jid: str):  # type: ignore[no-untyped-def]
        job = job_or_404(jid)
        return jsonify(job.to_dict(manager.sources_of(job)))

    @app.post("/api/jobs/<jid>/rerender")
    def rerender(jid: str):  # type: ignore[no-untyped-def]
        job = job_or_404(jid)
        data = request.get_json(silent=True) or {}
        clips = data.get("clips")
        if not isinstance(clips, list) or not clips:
            abort(400, description="Chybí seznam klipů")
        try:
            params = parse_params({**job.params, **(data.get("params") or {})})
            for c in clips:
                float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError) as exc:
            abort(400, description=f"Neplatné klipy: {exc}")
        new = manager.submit(job.source_ids, params, clips=clips)
        return jsonify(new.to_dict(manager.sources_of(new))), 202

    @app.get("/api/jobs/<jid>/thumbs/<int:i>.jpg")
    def job_thumb(jid: str, i: int):  # type: ignore[no-untyped-def]
        job = job_or_404(jid)
        path = job.dir / "thumbs" / f"{i}.jpg"
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/jpeg", conditional=True)

    @app.get("/api/jobs/<jid>/<name>")
    def job_file(jid: str, name: str):  # type: ignore[no-untyped-def]
        job = job_or_404(jid)
        if name not in OUTPUT_FILES:
            abort(404)
        path = job.dir / name
        if not path.is_file():
            abort(404, description="Soubor ještě neexistuje")
        srcs = manager.sources_of(job)
        stem = Path(srcs[0].name).stem if srcs else "video"
        if len(srcs) > 1:
            stem += f"_+{len(srcs) - 1}"
        as_attachment = request.args.get("download") == "1"
        download_name = f"{stem}_reel{path.suffix}" if name == "reel.mp4" else f"{stem}_{name}"
        return send_file(path, mimetype=OUTPUT_FILES[name], conditional=True, as_attachment=as_attachment, download_name=download_name)

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True, workdir: Path | None = None) -> None:
    app = create_app(workdir)
    url = f"http://{host}:{port}/"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"[reelcut] web UI: {url}   (ukončíte klávesami Ctrl+C)", flush=True)
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)


__all__ = ["Manager", "create_app", "parse_params", "serve"]
