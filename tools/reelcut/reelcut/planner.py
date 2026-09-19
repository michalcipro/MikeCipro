"""Edit planning: pick the clips that make the best reel for a target duration.

Rules of the cut:
* Speech segments are atomic. They are kept whole (never cut mid-sentence) and,
  in ``keep`` mode, selected before any visual clip.
* Long speech is split only at its own pauses into parts that fit the target.
* Visual candidates are the best windows of each shot outside speech, scored by
  the style-weighted moment curve, never crossing a shot boundary.
* Cut points snap to audio transients so cuts land on the beat.
* Optional cold open ("hook"): the strongest short moment is moved to the front.
"""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

from .analysis import Analysis, CURVE_NAMES, Style, resolve_style
from .audio import SpeechSegment


@dataclass
class Clip:
    start: float
    end: float
    kind: str  # speech | visual | hook
    score: float
    reason: str = ""
    shot: int = -1
    peak: float = 0.0
    crop_cx: float = 0.5
    crop_cy: float = 0.5
    text: str | None = None
    out_start: float = 0.0
    source: str | None = None  # video file this clip comes from (None = plan.source)
    parts: list[list[float]] = field(default_factory=list)  # sub-ranges kept when pauses are removed

    @property
    def duration(self) -> float:
        if self.parts:
            return sum(b - a for a, b in self.parts)
        return self.end - self.start

    @property
    def out_end(self) -> float:
        return self.out_start + self.duration

    def overlaps(self, other: "Clip") -> bool:
        return self.start < other.end and other.start < self.end

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("start", "end", "score", "peak", "crop_cx", "crop_cy", "out_start"):
            d[k] = round(float(d[k]), 3)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        c = cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
        c.parts = [[float(a), float(b)] for a, b in (c.parts or [])]
        return c


@dataclass
class PlanSettings:
    target: float = 30.0
    tolerance: float = 0.08
    min_clip: float = 1.0
    max_clip: float = 5.0
    max_speech_clip: float = 15.0
    speech_mode: str = "keep"  # keep | prefer | ignore
    speech_pad: float = 0.15
    hook: bool = True
    hook_length: float = 2.5
    order: str = "chrono"  # chrono | score
    snap_onsets: bool = True
    snap_window: float = 0.12
    max_clips_per_shot: int = 2
    min_quality: float = 0.3  # drop visual candidates scoring below this fraction of the best one
    max_pause: float = 1.0  # remove silences inside speech longer than this (0 = keep every pause)
    pause_pad: float = 0.15  # air left on each side of a removed pause

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanSettings":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Plan:
    source: str
    style: str
    settings: PlanSettings
    clips: list[Clip]
    warnings: list[str] = field(default_factory=list)
    source_duration: float = 0.0

    @property
    def total(self) -> float:
        return sum(c.duration for c in self.clips)

    @property
    def sources(self) -> list[str]:
        """Distinct source files used by the clips, in order of first use."""
        out: list[str] = []
        for c in self.clips:
            src = c.source or self.source
            if src not in out:
                out.append(src)
        return out or [self.source]

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "sources": self.sources,
            "source_duration": round(self.source_duration, 3),
            "style": self.style,
            "settings": self.settings.to_dict(),
            "total": round(self.total, 3),
            "clips": [c.to_dict() for c in self.clips],
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        plan = cls(
            source=d["source"],
            style=d.get("style", "auto"),
            settings=PlanSettings.from_dict(d.get("settings", {})),
            clips=[Clip.from_dict(c) for c in d["clips"]],
            warnings=list(d.get("warnings", [])),
            source_duration=float(d.get("source_duration", 0.0)),
        )
        finalize_timeline(plan.clips)
        return plan

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --- helpers ------------------------------------------------------------------

def _subtract(interval: tuple[float, float], blocks: list[tuple[float, float]]) -> list[tuple[float, float]]:
    a, b = interval
    free: list[tuple[float, float]] = []
    cur = a
    for s, e in sorted(blocks):
        if e <= cur:
            continue
        if s >= b:
            break
        if s > cur:
            free.append((cur, min(s, b)))
        cur = max(cur, e)
        if cur >= b:
            break
    if cur < b:
        free.append((cur, b))
    return free


def _merge_speech(segments: list[SpeechSegment], limit: float, gap: float = 0.35, boundaries: list[float] | None = None) -> list[SpeechSegment]:
    out: list[SpeechSegment] = []
    bounds = boundaries or []
    for seg in sorted(segments, key=lambda s: s.start):
        crosses = out and any(out[-1].start < b < seg.end for b in bounds)
        if out and not crosses and seg.start - out[-1].end <= gap and seg.end - out[-1].start <= limit:
            prev = out[-1]
            text = " ".join(t for t in (prev.text, seg.text) if t) or None
            out[-1] = SpeechSegment(prev.start, max(prev.end, seg.end), text)
        else:
            out.append(SpeechSegment(seg.start, seg.end, seg.text))
    return out


def _split_speech(an: Analysis, start: float, end: float, limit: float, min_part: float = 1.0) -> list[tuple[float, float]]:
    """Split a long speech segment at its quietest pause near the middle (recursive)."""
    dur = end - start
    if dur <= limit or dur < 2 * min_part + 0.2:
        return [(start, end)]
    lo = start + max(min_part, dur / 4)
    hi = end - max(min_part, dur / 4)
    if hi <= lo:
        return [(start, end)]
    ia, ib = an.index(lo), an.index(hi)
    prob = an.speech_prob[ia:ib + 1]
    if len(prob) == 0:
        return [(start, end)]
    # prefer deep pauses; break ties towards the middle
    mid = (ia + ib) / 2
    cost = prob + 0.05 * np.abs(np.arange(ia, ib + 1) - mid) / max(1.0, (ib - ia) / 2)
    p = (ia + int(np.argmin(cost))) * an.dt
    return _split_speech(an, start, p, limit, min_part) + _split_speech(an, p, end, limit, min_part)


def _find_pauses(an: Analysis, a: float, b: float, max_pause: float, pad: float, thr: float = 0.15) -> list[tuple[float, float]]:
    """Silences inside [a, b] longer than ``max_pause`` (shrunk by ``pad`` on both sides)."""
    ia, ib = an.index(a), an.index(b)
    prob = an.speech_prob[ia:ib + 1]
    gaps: list[tuple[float, float]] = []
    i = 0
    n = len(prob)
    while i < n:
        if prob[i] < thr:
            j = i
            while j < n and prob[j] < thr:
                j += 1
            gs, ge = a + i * an.dt, a + j * an.dt
            if i > 0 and j < n and ge - gs >= max_pause:  # interior pause only
                gs, ge = gs + pad, ge - pad
                if ge - gs >= 0.25:
                    gaps.append((gs, ge))
            i = j
        else:
            i += 1
    return gaps


def _best_windows(curve: np.ndarray, dt: float, a: float, b: float, length: float, max_n: int, min_sep: float) -> list[tuple[float, float, float]]:
    ia, ib = int(round(a / dt)), int(round(b / dt))
    ib = min(ib, len(curve))
    if ib <= ia:
        return []
    n = max(1, int(round(length / dt)))
    if ib - ia <= n:
        return [(a, b, float(curve[ia:ib].mean()))]
    seg = curve[ia:ib].astype(np.float64)
    cs = np.concatenate([[0.0], np.cumsum(seg)])
    means = (cs[n:] - cs[:-n]) / n
    mask = np.ones(len(means), dtype=bool)
    sep = int(round(min_sep / dt))
    out = []
    for _ in range(max_n):
        masked = np.where(mask, means, -1.0)
        k = int(np.argmax(masked))
        if masked[k] < 0:
            break
        out.append(((ia + k) * dt, (ia + k + n) * dt, float(means[k])))
        mask[max(0, k - n - sep + 1):k + n + sep] = False
    return out


def _snap(t: float, onsets: list[float], window: float, lo: float, hi: float) -> float:
    if not onsets:
        return t
    i = bisect_left(onsets, t - window)
    best, best_d = t, window
    while i < len(onsets) and onsets[i] <= t + window:
        o = onsets[i]
        if lo <= o <= hi and abs(o - t) < best_d:
            best, best_d = o, abs(o - t)
        i += 1
    return best


def _describe(an: Analysis, a: float, b: float, kind: str) -> str:
    feats = {k: an.mean_between(an.curves[k], a, b) for k in CURVE_NAMES}
    parts = []
    if kind == "speech":
        parts.append("speech kept whole")
    if feats["faces"] > 0.45:
        parts.append("face on screen")
    if feats["motion"] > 0.6:
        parts.append("high motion")
    if feats["audio"] > 0.65:
        parts.append("loud")
    if feats["color"] > 0.7:
        parts.append("colorful")
    if feats["sharp"] > 0.7:
        parts.append("sharp")
    if not parts:
        parts.append("best window of the shot")
    return ", ".join(parts)


def _shot_index(an: Analysis, t: float) -> int:
    for s in an.shots:
        if s.start <= t < s.end:
            return s.index
    return an.shots[-1].index if an.shots else -1


# --- candidate generation -----------------------------------------------------

def speech_candidates(an: Analysis, s: PlanSettings, moment: np.ndarray) -> list[Clip]:
    if s.speech_mode == "ignore" or not an.speech_segments:
        return []
    limit = min(s.max_speech_clip, s.target * (1 + s.tolerance))
    out: list[Clip] = []
    for seg in _merge_speech(an.speech_segments, limit, boundaries=an.boundaries):
        for a, b in _split_speech(an, seg.start, seg.end, limit):
            lo, hi = an.part_range(a)
            a2 = max(lo, a - s.speech_pad)
            b2 = min(hi, b + s.speech_pad)
            if b2 - a2 < 0.5:
                continue
            score = an.mean_between(moment, a2, b2)
            peak = float(moment[an.index(a2):an.index(b2) + 1].max())
            clip = Clip(a2, b2, "speech", score, _describe(an, a2, b2, "speech"), _shot_index(an, a2), peak, text=seg.text)
            if s.max_pause > 0:
                gaps = _find_pauses(an, a2, b2, s.max_pause, s.pause_pad)
                if gaps:
                    clip.parts = [[x, y] for x, y in _subtract((a2, b2), gaps) if y - x > 0.05]
                    clip.reason += f", {len(gaps)} pause{'s' if len(gaps) > 1 else ''} removed"
            out.append(clip)
    return out


def visual_candidates(an: Analysis, s: PlanSettings, moment: np.ndarray, blocked: list[tuple[float, float]]) -> list[Clip]:
    out: list[Clip] = []
    onsets = sorted(an.onsets)
    for shot in an.shots:
        for a, b in _subtract((shot.start, shot.end), blocked):
            if b - a < s.min_clip:
                continue
            length = min(s.max_clip, b - a)
            for ws, we, sc in _best_windows(moment, an.dt, a, b, length, s.max_clips_per_shot, min_sep=1.0):
                if s.snap_onsets:
                    ns = _snap(ws, onsets, s.snap_window, a, we - s.min_clip)
                    ne = _snap(we, onsets, s.snap_window, ns + s.min_clip, b)
                    if ne - ns >= s.min_clip * 0.9:
                        ws, we = ns, ne
                peak = float(moment[an.index(ws):an.index(we) + 1].max())
                out.append(Clip(ws, we, "visual", sc, _describe(an, ws, we, "visual"), shot.index, peak))
    return out


# --- selection ----------------------------------------------------------------

def _diversity(c: Clip, chosen: list[Clip]) -> float:
    f = 1.0
    for o in chosen:
        if o.shot == c.shot and o.shot >= 0:
            f *= 0.75
        if abs(o.start - c.start) < 1.5 or abs(o.end - c.end) < 1.5:
            f *= 0.85
    return f


def _shrink(an: Analysis, s: PlanSettings, moment: np.ndarray, c: Clip, length: float) -> Clip | None:
    """Best sub-window of ``c`` with the given length (used to fill the last bit of budget)."""
    if length < s.min_clip or c.duration <= length + 1e-6:
        return None
    wins = _best_windows(moment, an.dt, c.start, c.end, length, 1, min_sep=0.0)
    if not wins:
        return None
    ws, we, sc = wins[0]
    if s.snap_onsets:
        ns = _snap(ws, sorted(an.onsets), s.snap_window, c.start, c.end - length)
        ws, we = ns, ns + length
    we = min(we, c.end)
    return Clip(ws, we, c.kind, sc, c.reason, c.shot, float(moment[an.index(ws):an.index(we) + 1].max()))


def _greedy_fill(an: Analysis, s: PlanSettings, moment: np.ndarray, pool: list[Clip], chosen: list[Clip], total: float) -> tuple[list[Clip], float]:
    """Add the best-scoring non-overlapping candidates until the budget is spent.

    When nothing fits any more but at least ``min_clip`` of budget is left, the
    best remaining candidate is shrunk to its strongest sub-window of that length.
    """
    budget = s.target * (1 + s.tolerance)
    chosen = list(chosen)
    pool = [c for c in pool if not any(c.overlaps(o) for o in chosen)]
    while pool:
        best, best_val = None, -1.0
        for c in pool:
            if total + c.duration > budget + 1e-6:
                continue
            val = c.score * _diversity(c, chosen)
            if val > best_val:
                best, best_val = c, val
        if best is None:
            remaining = budget - total
            if remaining < s.min_clip:
                break
            ranked = sorted(pool, key=lambda c: c.score * _diversity(c, chosen), reverse=True)
            best = None
            for c in ranked:
                best = _shrink(an, s, moment, c, min(remaining, s.max_clip))
                if best is not None:
                    break
            if best is None:
                break
        chosen.append(best)
        total += best.duration
        pool = [c for c in pool if c is not best and not c.overlaps(best)]
    return chosen, total


def select_clips(an: Analysis, s: PlanSettings, moment: np.ndarray, speech: list[Clip], visual: list[Clip]) -> tuple[list[Clip], list[str]]:
    budget = s.target * (1 + s.tolerance)
    chosen: list[Clip] = []
    warnings: list[str] = []
    total = 0.0
    if s.speech_mode == "keep":
        kept = 0
        for c in sorted(speech, key=lambda c: c.score, reverse=True):
            if total + c.duration <= budget:
                chosen.append(c)
                total += c.duration
                kept += 1
        if kept < len(speech):
            warnings.append(
                f"Speech totals {sum(c.duration for c in speech):.1f}s but the target is {s.target:.0f}s: "
                f"kept {kept} of {len(speech)} speech parts (whole, never cut mid-sentence). "
                "Raise --target to keep more."
            )
        pool = list(visual)
    elif s.speech_mode == "prefer":
        pool = [Clip(**{**c.__dict__, "score": min(1.0, c.score + 0.25)}) for c in speech] + list(visual)
    else:
        pool = list(visual)
    chosen, _ = _greedy_fill(an, s, moment, pool, chosen, total)
    return chosen, warnings


def apply_hook(
    an: Analysis,
    s: PlanSettings,
    chosen: list[Clip],
    moment: np.ndarray,
    blocked: list[tuple[float, float]],
    pool: list[Clip] | None = None,
) -> tuple[list[Clip], Clip | None]:
    """Find the strongest short moment and move it to the front as a cold open.

    Candidates are the best short windows outside speech plus any short speech
    clip already chosen. If the hook is carved out of a chosen visual clip, the
    part of that clip *before* the hook is dropped (it would jump back in time
    inside the same shot) and the freed budget is refilled from ``pool``.
    """
    budget = s.target * (1 + s.tolerance)
    length = min(s.hook_length, s.target / 4)

    def peak_of(a: float, b: float) -> float:
        return float(moment[an.index(a):an.index(b) + 1].max())

    def rank(a: float, b: float, mean: float) -> float:
        return peak_of(a, b) * 0.6 + mean * 0.4

    candidates: list[tuple[float, Clip]] = []
    free = [piece for a, b in _subtract((0.0, an.duration), blocked) for piece in an.split_by_boundaries(a, b)]
    for a, b in free:
        if b - a >= length:
            for ws, we, sc in _best_windows(moment, an.dt, a, b, length, 3, min_sep=2.0):
                candidates.append((rank(ws, we, sc), Clip(ws, we, "hook", sc, "cold open: strongest moment", _shot_index(an, ws), peak_of(ws, we))))
    for c in chosen:
        if c.kind == "speech" and c.duration <= max(length * 1.6, 4.0):
            candidates.append((rank(c.start, c.end, c.score), c))
    if not candidates:
        return chosen, None
    candidates.sort(key=lambda x: x[0], reverse=True)
    hook = candidates[0][1]

    if hook.kind == "speech":
        rest = [c for c in chosen if c is not hook]
        hook = Clip(hook.start, hook.end, "hook", hook.score, "cold open: " + hook.reason, hook.shot, hook.peak, text=hook.text)
        return rest, hook

    hs, he = hook.start, min(hook.end, an.duration)
    if s.snap_onsets:
        onsets = sorted(an.onsets)
        ns = _snap(hs, onsets, s.snap_window, hs - s.snap_window, he - length * 0.8)
        hs, he = ns, min(ns + length, an.duration)
    hook = Clip(hs, he, "hook", hook.score, hook.reason, hook.shot, peak_of(hs, he))
    first = min(chosen, key=lambda c: c.start) if chosen else None
    if first is not None and first.kind != "speech" and first.overlaps(hook) and abs(first.start - hs) < 0.5:
        return chosen, None  # the reel already opens with this moment

    new: list[Clip] = []
    for c in chosen:
        if c.kind == "speech" or not c.overlaps(hook):
            new.append(c)
            continue
        for a, b in _subtract((c.start, c.end), [(hs, he)]):
            if b - a < s.min_clip:
                continue
            if b <= hs + 1e-6 and c.shot == hook.shot:
                continue  # would jump back in time inside the same shot right after the cold open
            new.append(Clip(a, b, c.kind, c.score, c.reason, c.shot, c.peak))
    total = sum(c.duration for c in new) + hook.duration
    visuals = sorted([c for c in new if c.kind != "speech"], key=lambda c: c.score)
    while total > budget and visuals:
        drop = visuals.pop(0)
        new.remove(drop)
        total -= drop.duration
    if total > budget:
        return chosen, None
    if pool:
        refill = [c for c in pool if not c.overlaps(hook) and not (c.shot == hook.shot and c.end <= hs + 1e-6)]
        new, total = _greedy_fill(an, s, moment, refill, new, total)
    # a cold open must not cost a big part of the reel (single-shot sources have nothing to refill with)
    if total < 0.85 * sum(c.duration for c in chosen):
        return chosen, None
    return new, hook


def expand_parts(clips: list[Clip]) -> list[Clip]:
    """Turn clips with removed pauses into consecutive clips (jump cuts)."""
    out: list[Clip] = []
    for c in clips:
        if len(c.parts) > 1:
            for i, (a, b) in enumerate(c.parts):
                out.append(Clip(a, b, c.kind, c.score, c.reason if i == 0 else "continues after a removed pause",
                                c.shot, c.peak, c.crop_cx, c.crop_cy, c.text if i == 0 else None, source=c.source))
        else:
            if c.parts:
                c.start, c.end = c.parts[0]
                c.parts = []
            out.append(c)
    return out


def localize_clips(an: Analysis, clips: list[Clip]) -> None:
    """Map composite-timeline clips back to their source file and local times."""
    if not an.is_composite:
        return
    for c in clips:
        info, local = an.locate(c.start)
        dur = c.duration
        c.source = info.path
        c.start = max(0.0, min(local, info.duration))
        c.end = max(c.start, min(info.duration, c.start + dur))
    clips[:] = [c for c in clips if c.duration > 0.05]


def finalize_timeline(clips: list[Clip]) -> None:
    t = 0.0
    for c in clips:
        c.out_start = t
        t += c.duration


def build_plan(an: Analysis, s: PlanSettings, style: Style | str = "auto") -> Plan:
    style_obj = resolve_style(style) if isinstance(style, str) else style
    moment = an.moment_curve(style_obj)
    blocked: list[tuple[float, float]] = []
    if s.speech_mode != "ignore":
        blocked = [(seg.start - s.speech_pad, seg.end + s.speech_pad) for seg in an.speech_segments]

    speech = speech_candidates(an, s, moment)
    visual = visual_candidates(an, s, moment, blocked)
    warnings: list[str] = []
    if visual:
        floor = s.min_quality * max(c.score for c in visual)
        dropped = [c for c in visual if c.score < floor]
        visual = [c for c in visual if c.score >= floor]
        if dropped:
            warnings.append(f"Skipped {len(dropped)} weak candidate clip(s) below the quality floor (--min-quality {s.min_quality}).")
    chosen, more = select_clips(an, s, moment, speech, visual)
    warnings += more
    if not chosen:
        warnings.append("No clip fits the target; try a smaller --min-clip or larger --target.")

    hook = None
    if s.hook and len(chosen) >= 1:
        chosen, hook = apply_hook(an, s, chosen, moment, blocked, pool=visual)

    if s.order == "score":
        chosen.sort(key=lambda c: c.score, reverse=True)
    else:
        chosen.sort(key=lambda c: c.start)
    clips = expand_parts(([hook] if hook else []) + chosen)

    for c in clips:
        c.start = max(0.0, min(c.start, an.duration))
        c.end = max(c.start, min(c.end, an.duration))
        fc = an.face_center(c.start, c.end)
        if fc:
            c.crop_cx, c.crop_cy = fc
    localize_clips(an, clips)
    finalize_timeline(clips)

    total = sum(c.duration for c in clips)
    if total < s.target * (1 - s.tolerance) - 0.5:
        warnings.append(
            f"Reel is {total:.1f}s, short of the {s.target:.0f}s target: not enough distinct material "
            "(try --max-clip higher or --min-clip lower)."
        )
    return Plan(an.source.path, style_obj.name, s, clips, warnings, an.duration)


__all__ = ["Clip", "Plan", "PlanSettings", "build_plan", "expand_parts", "finalize_timeline", "localize_clips"]
