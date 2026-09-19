"""Combine audio and video features into a style-independent Analysis.

The analysis stores normalised feature curves on a fixed 0.1 s grid plus shot
and speech segments. Style weights are applied at planning time, so one cached
analysis can be re-planned with different styles and targets for free.
"""

from __future__ import annotations

import json
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import __version__
from .audio import AudioFeatures, SpeechSegment, _moving_average, _robust_norm, analyze_audio, resample_curve
from .ffmpeg import MediaInfo, probe
from .video import FrameFeatures, analyze_video, detect_shots

DT = 0.1  # seconds per curve sample
CURVE_NAMES = ("audio", "motion", "faces", "sharp", "color", "exposure", "speech")


@dataclass
class Style:
    name: str
    weights: dict[str, float]
    description: str = ""

    def with_overrides(self, overrides: dict[str, float] | None) -> "Style":
        if not overrides:
            return self
        w = dict(self.weights)
        w.update(overrides)
        return Style(self.name + "+custom", w, self.description)


STYLES: dict[str, Style] = {
    "auto": Style(
        "auto",
        {"audio": 1.0, "motion": 1.0, "faces": 0.8, "sharp": 0.4, "color": 0.3, "exposure": 0.4, "speech": 1.0},
        "Balanced: speech, people, motion and sound all count.",
    ),
    "talk": Style(
        "talk",
        {"audio": 0.6, "motion": 0.3, "faces": 1.2, "sharp": 0.4, "color": 0.2, "exposure": 0.4, "speech": 2.0},
        "Interviews and talking heads: speech and faces dominate.",
    ),
    "action": Style(
        "action",
        {"audio": 1.4, "motion": 1.8, "faces": 0.4, "sharp": 0.6, "color": 0.5, "exposure": 0.4, "speech": 0.4},
        "Sports, events, b-roll: loud, fast moments win.",
    ),
    "vibe": Style(
        "vibe",
        {"audio": 0.8, "motion": 1.0, "faces": 0.5, "sharp": 0.8, "color": 1.0, "exposure": 0.6, "speech": 0.5},
        "Travel / aesthetic: sharp, colourful, well exposed shots.",
    ),
}


@dataclass
class Shot:
    index: int
    start: float
    end: float
    features: dict[str, float]

    @property
    def duration(self) -> float:
        return self.end - self.start

    def score(self, style: Style) -> float:
        total = sum(style.weights.values()) or 1.0
        return sum(style.weights.get(k, 0.0) * self.features.get(k, 0.0) for k in CURVE_NAMES) / total

    def tags(self) -> list[str]:
        f = self.features
        tags = []
        if f.get("speech", 0) > 0.5:
            tags.append("speech")
        if f.get("faces", 0) > 0.45:
            tags.append("face")
        if f.get("motion", 0) > 0.6:
            tags.append("motion")
        elif f.get("motion", 0) < 0.15:
            tags.append("static")
        if f.get("audio", 0) > 0.65:
            tags.append("loud")
        if f.get("color", 0) > 0.7:
            tags.append("colorful")
        if f.get("sharp", 0) < 0.2:
            tags.append("soft")
        if f.get("exposure", 1) < 0.5:
            tags.append("badly-exposed")
        return tags

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "features": {k: round(float(v), 4) for k, v in self.features.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Shot":
        return cls(int(d["index"]), float(d["start"]), float(d["end"]), dict(d["features"]))


@dataclass
class FaceSample:
    t: float
    cx: float
    cy: float
    size: float  # largest face width / frame width
    count: int

    def to_dict(self) -> dict:
        return {"t": round(self.t, 3), "cx": round(self.cx, 4), "cy": round(self.cy, 4),
                "size": round(self.size, 4), "count": self.count}

    @classmethod
    def from_dict(cls, d: dict) -> "FaceSample":
        return cls(float(d["t"]), float(d["cx"]), float(d["cy"]), float(d["size"]), int(d["count"]))


@dataclass
class AnalysisSettings:
    sample_fps: float = 8.0
    analysis_width: int = 384
    faces: bool = True
    face_every: int = 4
    transcribe: bool = False
    language: str | None = None
    whisper_model: str = "small"
    vad_threshold: float = 0.35

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Analysis:
    source: MediaInfo
    settings: AnalysisSettings
    dt: float
    curves: dict[str, np.ndarray]
    speech_prob: np.ndarray
    shots: list[Shot]
    speech_segments: list[SpeechSegment]
    onsets: list[float]
    tempo_bpm: float | None
    face_track: list[FaceSample]
    transcript: dict | None = None
    vad_backend: str = "heuristic"
    version: str = __version__
    created_at: float = field(default_factory=time.time)
    # composite analyses (several videos on one timeline): the parts and where each starts
    sources: list[MediaInfo] = field(default_factory=list)
    offsets: list[float] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.source.duration

    @property
    def is_composite(self) -> bool:
        return len(self.sources) > 1

    @property
    def boundaries(self) -> list[float]:
        """Times where one source video ends and the next begins (empty for a single video)."""
        return list(self.offsets[1:]) if self.is_composite else []

    def part_index(self, t: float) -> int:
        if not self.is_composite:
            return 0
        return min(max(bisect_right(self.offsets, t) - 1, 0), len(self.offsets) - 1)

    def part_range(self, t: float) -> tuple[float, float]:
        """Composite-time interval of the source video that contains ``t``."""
        if not self.is_composite:
            return 0.0, self.duration
        i = self.part_index(t)
        hi = self.offsets[i + 1] if i + 1 < len(self.offsets) else self.duration
        return self.offsets[i], hi

    def locate(self, t: float) -> tuple[MediaInfo, float]:
        """Map a composite time to (source video, local time)."""
        if not self.is_composite:
            return self.source, t
        i = self.part_index(t)
        return self.sources[i], t - self.offsets[i]

    def split_by_boundaries(self, a: float, b: float) -> list[tuple[float, float]]:
        """Cut [a, b] at source boundaries so no piece spans two videos."""
        out = []
        cur = a
        for x in self.boundaries:
            if cur < x < b:
                out.append((cur, x))
                cur = x
        if cur < b:
            out.append((cur, b))
        return out

    @property
    def n(self) -> int:
        return len(self.speech_prob)

    def moment_curve(self, style: Style, smooth_s: float = 0.3) -> np.ndarray:
        """Style-weighted interest per 0.1 s, smoothed."""
        total = sum(style.weights.values()) or 1.0
        m = np.zeros(self.n, dtype=np.float32)
        for k in CURVE_NAMES:
            w = style.weights.get(k, 0.0)
            if w and k in self.curves:
                m += w * self.curves[k]
        m /= total
        return _moving_average(m, int(round(smooth_s / self.dt)))

    def index(self, t: float) -> int:
        return int(min(max(t / self.dt, 0), self.n - 1))

    def mean_between(self, curve: np.ndarray, a: float, b: float) -> float:
        ia, ib = self.index(a), self.index(b)
        if ib <= ia:
            return float(curve[ia])
        return float(curve[ia:ib].mean())

    def face_center(self, a: float, b: float) -> tuple[float, float] | None:
        """Median face centre within [a, b] if faces are present often enough."""
        samples = [f for f in self.face_track if a <= f.t <= b]
        if not samples:
            return None
        with_face = [f for f in samples if f.count > 0]
        if len(with_face) < max(1, 0.2 * len(samples)):
            return None
        cx = float(np.median([f.cx for f in with_face]))
        cy = float(np.median([f.cy for f in with_face]))
        return cx, cy

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "source": self.source.to_dict(),
            "settings": self.settings.to_dict(),
            "dt": self.dt,
            "vad_backend": self.vad_backend,
            "curves": {k: [round(float(x), 4) for x in v] for k, v in self.curves.items()},
            "speech_prob": [round(float(x), 4) for x in self.speech_prob],
            "shots": [s.to_dict() for s in self.shots],
            "speech_segments": [s.to_dict() for s in self.speech_segments],
            "onsets": self.onsets,
            "tempo_bpm": self.tempo_bpm,
            "face_track": [f.to_dict() for f in self.face_track],
            "transcript": self.transcript,
            "sources": [m.to_dict() for m in self.sources],
            "offsets": [round(float(o), 3) for o in self.offsets],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Analysis":
        settings = AnalysisSettings(**{k: v for k, v in d.get("settings", {}).items() if k in AnalysisSettings.__dataclass_fields__})
        return cls(
            source=MediaInfo.from_dict(d["source"]),
            settings=settings,
            dt=float(d["dt"]),
            curves={k: np.asarray(v, dtype=np.float32) for k, v in d["curves"].items()},
            speech_prob=np.asarray(d["speech_prob"], dtype=np.float32),
            shots=[Shot.from_dict(s) for s in d["shots"]],
            speech_segments=[SpeechSegment.from_dict(s) for s in d["speech_segments"]],
            onsets=[float(x) for x in d.get("onsets", [])],
            tempo_bpm=d.get("tempo_bpm"),
            face_track=[FaceSample.from_dict(f) for f in d.get("face_track", [])],
            transcript=d.get("transcript"),
            vad_backend=d.get("vad_backend", "heuristic"),
            version=d.get("version", "?"),
            created_at=float(d.get("created_at", 0)),
            sources=[MediaInfo.from_dict(m) for m in d.get("sources", [])],
            offsets=[float(o) for o in d.get("offsets", [])],
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Analysis":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _face_curves(ff: FrameFeatures, n: int, dt: float) -> tuple[np.ndarray, list[FaceSample]]:
    track: list[FaceSample] = []
    values = np.zeros(n, dtype=np.float32)
    if not ff.faces:
        return values, track
    idxs = sorted(ff.faces)
    for j, i in enumerate(idxs):
        faces = ff.faces[i]
        t = float(ff.times[i])
        t_next = float(ff.times[idxs[j + 1]]) if j + 1 < len(idxs) else t + ff.face_every / ff.fps
        if faces:
            largest = faces[0]
            if len(faces) > 1:
                xs = [f.x for f in faces] + [f.x + f.w for f in faces]
                ys = [f.y for f in faces] + [f.y + f.h for f in faces]
                union_w = max(xs) - min(xs)
                # keep everyone in frame when they fit in a 9:16 crop, else follow the largest face
                if union_w <= 0.5:
                    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
                else:
                    cx, cy = largest.cx, largest.cy
            else:
                cx, cy = largest.cx, largest.cy
            size = largest.w
            val = 0.6 + 0.4 * min(1.0, size * 6.0)
            track.append(FaceSample(t, cx, cy, size, len(faces)))
        else:
            val = 0.0
            track.append(FaceSample(t, 0.5, 0.5, 0.0, 0))
        a, b = int(t / dt), int(t_next / dt)
        values[a:max(a + 1, b)] = val
    return values, track


def build_analysis(
    path: str,
    settings: AnalysisSettings | None = None,
    *,
    progress: Callable[[str], None] | None = None,
) -> Analysis:
    settings = settings or AnalysisSettings()
    log = progress or (lambda msg: None)
    info = probe(path)
    log(f"probed {info.width}x{info.height} @ {info.fps:.2f} fps, {info.duration:.1f}s, audio={'yes' if info.has_audio else 'no'}")

    ff = analyze_video(
        info,
        sample_fps=settings.sample_fps,
        width=settings.analysis_width,
        faces=settings.faces,
        face_every=settings.face_every,
        progress=log,
    )
    shots_raw = detect_shots(ff, info.duration)
    log(f"detected {len(shots_raw)} shots")

    af: AudioFeatures | None = None
    if info.has_audio:
        log("analysing audio")
        af = analyze_audio(path, info.duration, vad_threshold=settings.vad_threshold)
        log(f"speech segments: {len(af.speech_segments)} ({af.vad_backend}); onsets: {len(af.onsets)}")

    transcript = None
    speech_segments = list(af.speech_segments) if af else []
    if settings.transcribe and info.has_audio:
        from .transcript import transcribe

        log(f"transcribing with faster-whisper ({settings.whisper_model})")
        transcript = transcribe(path, model_size=settings.whisper_model, language=settings.language)
        if transcript and transcript.get("segments"):
            speech_segments = [
                SpeechSegment(float(s["start"]), float(s["end"]), s.get("text"))
                for s in transcript["segments"]
                if float(s["end"]) - float(s["start"]) > 0.2
            ]
            log(f"transcript: {len(speech_segments)} sentences")

    n = int(np.ceil(info.duration / DT)) + 1
    curves: dict[str, np.ndarray] = {}
    frame_hop = 1.0 / ff.fps
    curves["motion"] = resample_curve(_robust_norm(ff.motion), frame_hop, DT, n)
    curves["sharp"] = resample_curve(_robust_norm(np.log1p(ff.sharpness)), frame_hop, DT, n)
    curves["color"] = resample_curve(_robust_norm(ff.colorfulness), frame_hop, DT, n)
    exposure = 1.0 - np.clip(np.abs(ff.brightness - 0.45) - 0.2, 0, 0.3) / 0.3
    curves["exposure"] = resample_curve(exposure.astype(np.float32), frame_hop, DT, n)
    curves["faces"], face_track = _face_curves(ff, n, DT)
    if af is not None:
        curves["audio"] = resample_curve(_robust_norm(af.rms_db), af.hop_s, DT, n)
        speech_prob = resample_curve(af.speech_prob, af.hop_s, DT, n)
    else:
        curves["audio"] = np.zeros(n, dtype=np.float32)
        speech_prob = np.zeros(n, dtype=np.float32)
    speech = np.zeros(n, dtype=np.float32)
    for seg in speech_segments:
        speech[int(seg.start / DT):int(np.ceil(seg.end / DT))] = 1.0
    curves["speech"] = speech

    shots = []
    for i, (a, b) in enumerate(shots_raw):
        ia, ib = int(a / DT), max(int(a / DT) + 1, int(b / DT))
        feats = {k: float(curves[k][ia:ib].mean()) for k in CURVE_NAMES}
        shots.append(Shot(i, a, b, feats))

    return Analysis(
        source=info,
        settings=settings,
        dt=DT,
        curves=curves,
        speech_prob=speech_prob,
        shots=shots,
        speech_segments=speech_segments,
        onsets=list(af.onsets) if af else [],
        tempo_bpm=af.tempo_bpm if af else None,
        face_track=face_track,
        transcript=transcript,
        vad_backend=af.vad_backend if af else "none",
    )


def combine_analyses(parts: list[Analysis]) -> Analysis:
    """Place several analyses on one timeline (video after video, in order).

    Curves, shots, speech, onsets and faces are shifted by each part's offset.
    Transcript words keep their local times and are tagged with their source
    path so captions still line up after the plan maps clips back to files.
    """
    if not parts:
        raise ValueError("No analyses to combine")
    if len(parts) == 1:
        return parts[0]
    dt = parts[0].dt
    offsets: list[float] = []
    total = 0.0
    for p in parts:
        offsets.append(total)
        total += p.duration
    n = int(np.ceil(total / dt)) + 1
    curves = {k: np.zeros(n, dtype=np.float32) for k in CURVE_NAMES}
    speech_prob = np.zeros(n, dtype=np.float32)
    shots: list[Shot] = []
    speech: list[SpeechSegment] = []
    onsets: list[float] = []
    faces: list[FaceSample] = []
    words: list[dict] = []
    sentences: list[dict] = []
    for i, (p, off) in enumerate(zip(parts, offsets)):
        ia = int(off / dt)
        ib = int((off + p.duration) / dt) if i + 1 < len(parts) else n
        m = max(0, min(len(p.speech_prob), ib - ia))
        for k in CURVE_NAMES:
            if k in p.curves:
                curves[k][ia:ia + m] = p.curves[k][:m]
        speech_prob[ia:ia + m] = p.speech_prob[:m]
        for sh in p.shots:
            shots.append(Shot(len(shots), sh.start + off, sh.end + off, dict(sh.features)))
        speech += [SpeechSegment(sg.start + off, sg.end + off, sg.text) for sg in p.speech_segments]
        onsets += [round(o + off, 3) for o in p.onsets]
        faces += [FaceSample(f.t + off, f.cx, f.cy, f.size, f.count) for f in p.face_track]
        if p.transcript:
            words += [{**w, "source": p.source.path} for w in p.transcript.get("words", [])]
            sentences += [{**sg, "source": p.source.path} for sg in p.transcript.get("segments", [])]
    first = parts[0].source
    source = MediaInfo(
        path=first.path, duration=total, width=first.width, height=first.height, fps=first.fps, rotation=0,
        has_audio=any(p.source.has_audio for p in parts), audio_sample_rate=first.audio_sample_rate,
        video_codec="composite", size_bytes=sum(p.source.size_bytes for p in parts),
        mtime=max(p.source.mtime for p in parts),
    )
    transcript = {"language": parts[0].transcript.get("language") if parts[0].transcript else None,
                  "segments": sentences, "words": words} if words else None
    return Analysis(
        source=source, settings=parts[0].settings, dt=dt, curves=curves, speech_prob=speech_prob, shots=shots,
        speech_segments=speech, onsets=sorted(onsets), tempo_bpm=None, face_track=faces, transcript=transcript,
        vad_backend=parts[0].vad_backend, sources=[p.source for p in parts], offsets=offsets,
    )


# --- caching -----------------------------------------------------------------

def cache_path(source: str | Path) -> Path:
    p = Path(source)
    return p.with_name(p.name + ".reelcut.json")


def load_cached(source: str | Path, settings: AnalysisSettings) -> Analysis | None:
    cp = cache_path(source)
    if not cp.is_file():
        return None
    try:
        an = Analysis.load(cp)
    except (ValueError, KeyError, TypeError):
        return None
    info = probe(source)
    same_source = abs(an.source.size_bytes - info.size_bytes) == 0 and abs(an.source.mtime - info.mtime) < 1.0
    same_settings = an.settings.to_dict() == settings.to_dict()
    if same_source and same_settings and an.version == __version__:
        return an
    return None


def get_analysis(
    source: str,
    settings: AnalysisSettings,
    *,
    use_cache: bool = True,
    progress: Callable[[str], None] | None = None,
) -> Analysis:
    if use_cache:
        cached = load_cached(source, settings)
        if cached is not None:
            if progress:
                progress(f"using cached analysis {cache_path(source).name}")
            return cached
    an = build_analysis(source, settings, progress=progress)
    if use_cache:
        try:
            an.save(cache_path(source))
        except OSError:
            pass
    return an


def get_combined_analysis(
    sources: list[str],
    settings: AnalysisSettings,
    *,
    use_cache: bool = True,
    progress: Callable[[str], None] | None = None,
) -> Analysis:
    """Analyse each video (cached individually) and place them on one timeline."""
    parts = []
    for i, src in enumerate(sources):
        if progress and len(sources) > 1:
            progress(f"video {i + 1}/{len(sources)}: {Path(src).name}")
        parts.append(get_analysis(src, settings, use_cache=use_cache, progress=progress))
    return combine_analyses(parts)


def resolve_style(name: str, overrides: dict[str, float] | None = None) -> Style:
    if name not in STYLES:
        raise ValueError(f"Unknown style '{name}'. Choose from: {', '.join(STYLES)}")
    return STYLES[name].with_overrides(overrides)


__all__ = [
    "Analysis", "AnalysisSettings", "FaceSample", "Shot", "Style", "STYLES", "CURVE_NAMES", "DT",
    "build_analysis", "combine_analyses", "get_analysis", "get_combined_analysis", "cache_path", "load_cached", "resolve_style",
]
