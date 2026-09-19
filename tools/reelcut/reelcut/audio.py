"""Audio analysis: loudness envelope, speech detection (VAD), onsets and tempo.

Everything works on a mono 16 kHz signal decoded by ffmpeg. Speech detection
combines an optional WebRTC VAD with a heuristic speech/music discriminator
(speech band energy, spectral flatness and 2-8 Hz syllabic modulation).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import numpy as np

from .ffmpeg import FFmpegError, ffmpeg_bin

try:  # optional, better VAD
    import webrtcvad  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    webrtcvad = None

SAMPLE_RATE = 16000
HOP_S = 0.02  # 20 ms frames (webrtcvad compatible)
WIN_S = 0.04


@dataclass
class SpeechSegment:
    start: float
    end: float
    text: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = {"start": round(self.start, 3), "end": round(self.end, 3)}
        if self.text:
            d["text"] = self.text
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SpeechSegment":
        return cls(float(d["start"]), float(d["end"]), d.get("text"))


@dataclass
class AudioFeatures:
    hop_s: float
    times: np.ndarray  # frame centre times
    rms_db: np.ndarray
    speech_prob: np.ndarray  # 0..1 per frame
    onset: np.ndarray  # spectral flux, 0..1
    speech_segments: list[SpeechSegment] = field(default_factory=list)
    onsets: list[float] = field(default_factory=list)
    tempo_bpm: float | None = None
    tempo_confidence: float = 0.0
    vad_backend: str = "heuristic"


def load_audio(path: str, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode the audio track to mono float32 in [-1, 1]."""
    cmd = [
        ffmpeg_bin(), "-v", "error", "-nostdin", "-i", path, "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", str(sr), "-f", "s16le", "-acodec", "pcm_s16le", "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise FFmpegError("Audio decode failed:\n" + proc.stderr.decode(errors="replace")[-2000:])
    pcm = np.frombuffer(proc.stdout, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def _frame(y: np.ndarray, win: int, hop: int) -> np.ndarray:
    """Return a (n_frames, win) view; the signal is padded so frames are centred."""
    pad = win // 2
    y = np.pad(y, (pad, pad + win))
    n = 1 + (len(y) - win) // hop
    shape = (n, win)
    strides = (y.strides[0] * hop, y.strides[0])
    return np.lib.stride_tricks.as_strided(y, shape=shape, strides=strides)


def _robust_norm(x: np.ndarray, lo_pct: float = 5, hi_pct: float = 95) -> np.ndarray:
    if len(x) == 0:
        return x
    lo, hi = np.percentile(x, lo_pct), np.percentile(x, hi_pct)
    if hi - lo < 1e-9:
        return np.full_like(x, 0.5)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def _moving_average(x: np.ndarray, n: int) -> np.ndarray:
    n = max(1, int(n))
    if n == 1 or len(x) == 0:
        return x.astype(np.float32)
    kernel = np.ones(n, dtype=np.float32) / n
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def _median_filter(x: np.ndarray, n: int) -> np.ndarray:
    n = max(1, int(n) | 1)
    if n == 1 or len(x) < n:
        return x
    pad = n // 2
    xp = np.pad(x, (pad, pad), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(xp, n)
    return np.median(windows, axis=1).astype(x.dtype)


def _modulation_ratio(rms: np.ndarray, frame_rate: float, win_s: float = 1.28) -> np.ndarray:
    """Energy share of 2-8 Hz modulation in the loudness envelope (syllable rate)."""
    n = int(round(win_s * frame_rate))
    n = max(16, n)
    if len(rms) < n:
        return np.zeros_like(rms)
    pad = n // 2
    env = np.pad(rms, (pad, n - pad), mode="edge")
    frames = np.lib.stride_tricks.sliding_window_view(env, n)[: len(rms)]
    frames = frames - frames.mean(axis=1, keepdims=True)
    window = np.hanning(n).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / frame_rate)
    band = (freqs >= 2.0) & (freqs <= 8.0)
    total = (freqs >= 0.7) & (freqs <= 25.0)
    num = spec[:, band].sum(axis=1)
    den = spec[:, total].sum(axis=1) + 1e-12
    return (num / den).astype(np.float32)


def _webrtc_flags(y: np.ndarray, sr: int, hop: int, n_frames: int, aggressiveness: int = 2) -> np.ndarray | None:
    if webrtcvad is None:
        return None
    vad = webrtcvad.Vad(aggressiveness)
    pcm = (np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes()
    flags = np.zeros(n_frames, dtype=np.float32)
    nbytes = hop * 2
    for i in range(n_frames):
        chunk = pcm[i * nbytes:(i + 1) * nbytes]
        if len(chunk) < nbytes:
            break
        try:
            flags[i] = 1.0 if vad.is_speech(chunk, sr) else 0.0
        except Exception:  # pragma: no cover - defensive
            return None
    return flags


def segments_from_activity(
    active: np.ndarray,
    hop_s: float,
    *,
    min_duration: float = 0.35,
    merge_gap: float = 0.45,
    pad: float = 0.15,
    total_duration: float | None = None,
) -> list[SpeechSegment]:
    """Turn a boolean per-frame activity array into merged, padded segments."""
    segs: list[list[float]] = []
    n = len(active)
    i = 0
    while i < n:
        if active[i]:
            j = i
            while j < n and active[j]:
                j += 1
            segs.append([i * hop_s, j * hop_s])
            i = j
        else:
            i += 1
    segs = [s for s in segs if s[1] - s[0] >= min_duration]
    merged: list[list[float]] = []
    for s in segs:
        if merged and s[0] - merged[-1][1] <= merge_gap:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    out = []
    limit = total_duration if total_duration is not None else n * hop_s
    for s in merged:
        a = max(0.0, s[0] - pad)
        b = min(limit, s[1] + pad)
        if b - a >= min_duration:
            out.append(SpeechSegment(a, b))
    return out


def analyze_audio(
    path: str,
    duration: float,
    *,
    vad_threshold: float = 0.35,
    vad_aggressiveness: int = 2,
) -> AudioFeatures:
    """Compute loudness, speech probability, speech segments and onsets."""
    y = load_audio(path)
    sr = SAMPLE_RATE
    hop = int(round(HOP_S * sr))
    win = int(round(WIN_S * sr))
    frame_rate = 1.0 / HOP_S
    if len(y) < win:
        y = np.pad(y, (0, win - len(y)))
    frames = _frame(y, win, hop)
    n = len(frames)
    times = np.arange(n, dtype=np.float32) * HOP_S

    window = np.hanning(win).astype(np.float32)
    freqs = np.fft.rfftfreq(win, d=1.0 / sr)
    speech_band = (freqs >= 300) & (freqs <= 3400)
    rms = np.zeros(n, dtype=np.float32)
    band_ratio = np.zeros(n, dtype=np.float32)
    flatness = np.zeros(n, dtype=np.float32)
    flux = np.zeros(n, dtype=np.float32)
    prev_log = None
    chunk = 4096
    for s in range(0, n, chunk):
        f = frames[s:s + chunk].astype(np.float32)
        rms[s:s + chunk] = np.sqrt(np.mean(f * f, axis=1) + 1e-12)
        spec = np.abs(np.fft.rfft(f * window, axis=1))
        total = spec.sum(axis=1) + 1e-9
        band_ratio[s:s + chunk] = spec[:, speech_band].sum(axis=1) / total
        log_spec = np.log(spec + 1e-9)
        flatness[s:s + chunk] = np.exp(log_spec.mean(axis=1)) / (spec.mean(axis=1) + 1e-9)
        comp = np.log1p(spec)
        if prev_log is None:
            prev = np.vstack([comp[:1], comp[:-1]])
        else:
            prev = np.vstack([prev_log, comp[:-1]])
        flux[s:s + chunk] = np.maximum(comp - prev, 0).sum(axis=1)
        prev_log = comp[-1:]

    rms_db = (20 * np.log10(rms + 1e-9)).astype(np.float32)
    noise_floor = float(np.percentile(rms_db, 10))
    s_energy = np.clip((rms_db - noise_floor - 6.0) / 20.0, 0, 1)
    s_band = np.clip((band_ratio - 0.30) / 0.40, 0, 1)
    s_flat = np.clip((0.50 - flatness) / 0.40, 0, 1)
    mod = _modulation_ratio(rms, frame_rate)
    s_mod = np.clip((mod - 0.15) / 0.35, 0, 1)

    heuristic = s_energy * (0.35 * s_band + 0.25 * s_flat + 0.40 * s_mod)
    backend = "heuristic"
    flags = _webrtc_flags(y, sr, hop, n, vad_aggressiveness)
    if flags is not None:
        backend = "webrtcvad+heuristic"
        base = _moving_average(flags, int(0.3 * frame_rate))
        # WebRTC VAD fires on music too; gate it with the syllabic-modulation cue.
        speech_prob = base * (0.45 + 0.55 * np.clip(s_mod * 1.5, 0, 1)) * (0.5 + 0.5 * s_energy)
        speech_prob = np.maximum(speech_prob, heuristic * 0.8)
    else:
        speech_prob = heuristic
    speech_prob = _median_filter(speech_prob.astype(np.float32), int(0.2 * frame_rate))
    speech_prob = _moving_average(speech_prob, int(0.3 * frame_rate))
    speech_prob = np.clip(speech_prob, 0, 1)

    # hysteresis thresholding
    on_thr, off_thr = vad_threshold, vad_threshold * 0.6
    active = np.zeros(n, dtype=bool)
    state = False
    for i in range(n):
        if state:
            state = speech_prob[i] > off_thr
        else:
            state = speech_prob[i] > on_thr
        active[i] = state
    segments = segments_from_activity(active, HOP_S, total_duration=duration)

    # onsets / tempo from spectral flux
    flux_n = flux / (np.percentile(flux, 99) + 1e-9)
    flux_n = np.clip(flux_n, 0, 1).astype(np.float32)
    local_mean = _moving_average(flux_n, int(0.5 * frame_rate))
    novelty = np.maximum(flux_n - local_mean, 0)
    onsets: list[float] = []
    thr = 0.12
    last = -1.0
    for i in range(1, n - 1):
        if novelty[i] > thr and novelty[i] >= novelty[i - 1] and novelty[i] >= novelty[i + 1]:
            t = float(times[i])
            if t - last >= 0.1:
                onsets.append(round(t, 3))
                last = t
    tempo, conf = _estimate_tempo(novelty, frame_rate)

    return AudioFeatures(
        hop_s=HOP_S,
        times=times,
        rms_db=rms_db,
        speech_prob=speech_prob,
        onset=flux_n,
        speech_segments=segments,
        onsets=onsets,
        tempo_bpm=tempo,
        tempo_confidence=conf,
        vad_backend=backend,
    )


def _estimate_tempo(novelty: np.ndarray, frame_rate: float) -> tuple[float | None, float]:
    x = novelty - novelty.mean()
    if len(x) < int(4 * frame_rate) or np.allclose(x, 0):
        return None, 0.0
    min_lag, max_lag = int(frame_rate * 60 / 200), int(frame_rate * 60 / 60)
    if len(x) <= max_lag + 1:
        return None, 0.0
    acf = np.correlate(x, x, mode="full")[len(x) - 1:]
    acf = acf / (acf[0] + 1e-12)
    seg = acf[min_lag:max_lag + 1]
    k = int(np.argmax(seg))
    lag = min_lag + k
    conf = float(seg[k])
    if conf < 0.15:
        return None, conf
    return round(60.0 * frame_rate / lag, 1), round(conf, 3)


def resample_curve(values: np.ndarray, src_hop: float, dst_dt: float, n_out: int, reduce: str = "mean") -> np.ndarray:
    """Resample a per-frame curve to a fixed grid of n_out bins of dst_dt seconds."""
    out = np.zeros(n_out, dtype=np.float32)
    if len(values) == 0:
        return out
    idx = np.minimum((np.arange(len(values)) * src_hop / dst_dt).astype(int), n_out - 1)
    if reduce == "max":
        np.maximum.at(out, idx, values.astype(np.float32))
        seen = np.zeros(n_out, dtype=bool)
        seen[idx] = True
    else:
        counts = np.zeros(n_out, dtype=np.float32)
        np.add.at(out, idx, values.astype(np.float32))
        np.add.at(counts, idx, 1)
        seen = counts > 0
        out[seen] /= counts[seen]
    # forward/backward fill bins that received no sample
    if not seen.all():
        filled = np.where(seen)[0]
        if len(filled) == 0:
            return out
        out = np.interp(np.arange(n_out), filled, out[filled]).astype(np.float32)
    return out


__all__ = [
    "AudioFeatures",
    "SpeechSegment",
    "analyze_audio",
    "load_audio",
    "resample_curve",
    "segments_from_activity",
    "_robust_norm",
    "_moving_average",
]
