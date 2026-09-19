"""Video analysis: shot detection, motion, sharpness, colourfulness and faces.

Frames are streamed from ffmpeg at a reduced resolution and sample rate so a
multi-minute source is analysed in seconds. Faces are detected with the YuNet
model bundled in ``reelcut/models`` (OpenCV FaceDetectorYN).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import cv2
import numpy as np

from .ffmpeg import FFmpegError, MediaInfo, ffmpeg_bin

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"


@dataclass
class Face:
    x: float  # normalised 0..1 of display width
    y: float
    w: float
    h: float
    score: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class FrameFeatures:
    fps: float
    times: np.ndarray
    motion: np.ndarray
    hist_diff: np.ndarray
    sharpness: np.ndarray
    brightness: np.ndarray
    colorfulness: np.ndarray
    faces: dict[int, list[Face]] = field(default_factory=dict)  # frame index -> faces
    face_every: int = 1
    analysis_size: tuple[int, int] = (0, 0)


def _analysis_dims(info: MediaInfo, width: int) -> tuple[int, int]:
    w = min(width, info.width)
    h = int(round(info.height * w / info.width))
    w -= w % 2
    h -= h % 2
    return max(w, 16), max(h, 16)


def iter_frames(info: MediaInfo, fps: float, width: int) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (timestamp, BGR frame) sampled at ``fps`` and scaled to ``width``."""
    w, h = _analysis_dims(info, width)
    cmd = [
        ffmpeg_bin(), "-v", "error", "-nostdin", "-i", info.path, "-an", "-sn", "-dn",
        "-vf", f"fps={fps},scale={w}:{h}:flags=area",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    nbytes = w * h * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=nbytes * 8)
    assert proc.stdout is not None
    i = 0
    try:
        while True:
            buf = bytearray()
            while len(buf) < nbytes:
                chunk = proc.stdout.read(nbytes - len(buf))
                if not chunk:
                    break
                buf.extend(chunk)
            if len(buf) < nbytes:
                break
            yield i / fps, np.frombuffer(bytes(buf), dtype=np.uint8).reshape(h, w, 3)
            i += 1
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        proc.wait()
        if proc.returncode not in (0, None) and i == 0:
            raise FFmpegError("Video decode failed:\n" + stderr[-2000:])


class FaceDetector:
    """Wrapper around OpenCV's YuNet detector. ``available`` is False if OpenCV lacks it."""

    def __init__(self, size: tuple[int, int], score_threshold: float = 0.7):
        self.available = False
        self._det = None
        if not hasattr(cv2, "FaceDetectorYN") or not MODEL_PATH.is_file():
            return
        try:  # silence backend chatter from the DNN module
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except AttributeError:  # pragma: no cover
            pass
        try:
            self._det = cv2.FaceDetectorYN.create(
                str(MODEL_PATH), "", size, score_threshold=score_threshold, nms_threshold=0.3, top_k=50
            )
            self._det.setInputSize(size)
            self.available = True
        except cv2.error:  # pragma: no cover - depends on build
            self._det = None

    def detect(self, frame: np.ndarray) -> list[Face]:
        if not self.available or self._det is None:
            return []
        h, w = frame.shape[:2]
        _, faces = self._det.detect(frame)
        if faces is None:
            return []
        out = []
        for f in faces:
            x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
            if fw <= 0 or fh <= 0:
                continue
            out.append(Face(x / w, y / h, fw / w, fh / h, score))
        out.sort(key=lambda f: f.w * f.h, reverse=True)
        return out


def _colorfulness(frame: np.ndarray) -> float:
    """Hasler & Suesstrunk colourfulness metric (typical range 0..120)."""
    b, g, r = frame[..., 0].astype(np.float32), frame[..., 1].astype(np.float32), frame[..., 2].astype(np.float32)
    rg = r - g
    yb = 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def analyze_video(
    info: MediaInfo,
    *,
    sample_fps: float = 8.0,
    width: int = 384,
    faces: bool = True,
    face_every: int = 4,
    progress: Callable[[str], None] | None = None,
) -> FrameFeatures:
    """Stream frames and compute per-frame features."""
    w, h = _analysis_dims(info, width)
    detector = FaceDetector((w, h)) if faces else None
    expected = int(info.duration * sample_fps) + 1

    times, motion, hist_diff, sharp, bright, color = [], [], [], [], [], []
    face_map: dict[int, list[Face]] = {}
    prev_gray = None
    prev_hist = None
    report_every = max(1, expected // 10)
    for i, (t, frame) in enumerate(iter_frames(info, sample_fps, w)):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist, alpha=1.0, norm_type=cv2.NORM_L1)

        if prev_gray is None:
            m, hd = 0.0, 0.0
        else:
            m = float(cv2.absdiff(small, prev_gray).mean() / 255.0)
            hd = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
        prev_gray, prev_hist = small, hist

        lap = cv2.Laplacian(gray, cv2.CV_32F)
        times.append(t)
        motion.append(m)
        hist_diff.append(hd)
        sharp.append(float(lap.var()))
        bright.append(float(gray.mean() / 255.0))
        color.append(_colorfulness(frame))
        if detector is not None and detector.available and i % face_every == 0:
            face_map[i] = detector.detect(frame)
        if progress and i % report_every == 0:
            progress(f"video analysis {min(100, int(100 * i / max(1, expected)))}%")

    if not times:
        raise FFmpegError("No frames decoded from " + info.path)
    return FrameFeatures(
        fps=sample_fps,
        times=np.asarray(times, dtype=np.float32),
        motion=np.asarray(motion, dtype=np.float32),
        hist_diff=np.asarray(hist_diff, dtype=np.float32),
        sharpness=np.asarray(sharp, dtype=np.float32),
        brightness=np.asarray(bright, dtype=np.float32),
        colorfulness=np.asarray(color, dtype=np.float32),
        faces=face_map,
        face_every=face_every,
        analysis_size=(w, h),
    )


def detect_shots(
    ff: FrameFeatures,
    duration: float,
    *,
    min_shot: float = 0.6,
    hard_threshold: float = 0.32,
    adaptive_k: float = 4.0,
) -> list[tuple[float, float]]:
    """Return shot boundaries as (start, end) pairs covering [0, duration].

    A cut is declared when the colour-histogram distance to the previous frame
    is a local maximum that either exceeds ``hard_threshold`` or stands out
    from its neighbourhood (median + k * MAD) while pixel motion also spikes.
    """
    hd = ff.hist_diff
    n = len(hd)
    if n < 3:
        return [(0.0, duration)]
    win = max(3, int(1.5 * ff.fps))
    cuts: list[int] = []
    min_gap = max(1, int(min_shot * ff.fps))
    last_cut = -min_gap
    for i in range(1, n):
        lo, hi = max(0, i - win), min(n, i + win + 1)
        neigh = np.concatenate([hd[lo:i], hd[i + 1:hi]])
        if len(neigh) == 0:
            continue
        med = float(np.median(neigh))
        mad = float(np.median(np.abs(neigh - med))) + 1e-4
        adaptive = med + adaptive_k * mad
        is_peak = hd[i] >= hd[max(0, i - 1)] and hd[i] >= hd[min(n - 1, i + 1)]
        strong = hd[i] > hard_threshold
        stands_out = hd[i] > max(adaptive, 0.12) and ff.motion[i] > 0.06
        if is_peak and (strong or stands_out) and i - last_cut >= min_gap:
            cuts.append(i)
            last_cut = i
    bounds = [0.0] + [float(ff.times[i]) for i in cuts] + [duration]
    shots = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a <= 0:
            continue
        if shots and b - a < min_shot:
            shots[-1] = (shots[-1][0], b)
        else:
            shots.append((a, b))
    return shots or [(0.0, duration)]


__all__ = ["Face", "FrameFeatures", "FaceDetector", "analyze_video", "detect_shots", "iter_frames"]
