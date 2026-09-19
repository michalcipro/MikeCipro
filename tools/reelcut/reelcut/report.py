"""Human readable reports and a timeline image of the analysis and plan."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .analysis import Analysis, Style
from .ffmpeg import format_timecode as tc
from .planner import Plan


def format_analysis(an: Analysis, style: Style) -> str:
    src = an.source
    lines = [
        f"Source: {src.path}",
        f"  {src.width}x{src.height} ({'vertical' if src.is_vertical else 'horizontal'}), {src.fps:.2f} fps, "
        f"{src.duration:.1f}s, audio: {'yes' if src.has_audio else 'no'}",
        f"  shots: {len(an.shots)}, speech segments: {len(an.speech_segments)} (VAD: {an.vad_backend}), "
        f"onsets: {len(an.onsets)}, tempo: {f'{an.tempo_bpm} BPM' if an.tempo_bpm else 'n/a'}",
        f"  style: {style.name} - {style.description}",
        "",
        "Shots (score uses the selected style):",
        f"  {'#':>3}  {'start':>7}  {'end':>7}  {'len':>5}  {'score':>5}  tags",
    ]
    for s in an.shots:
        lines.append(f"  {s.index:>3}  {tc(s.start):>7}  {tc(s.end):>7}  {s.duration:>5.1f}  {s.score(style):>5.2f}  {', '.join(s.tags())}")
    if an.speech_segments:
        lines += ["", "Speech (kept whole by the planner):"]
        for seg in an.speech_segments:
            text = f'  "{seg.text}"' if seg.text else ""
            lines.append(f"  {tc(seg.start)} - {tc(seg.end)}  ({seg.duration:.1f}s){text}")
    moment = an.moment_curve(style)
    top = np.argsort(moment)[::-1]
    picked: list[float] = []
    for i in top:
        t = float(i * an.dt)
        if all(abs(t - p) > 3.0 for p in picked):
            picked.append(t)
        if len(picked) >= 5:
            break
    lines += ["", "Strongest moments: " + ", ".join(f"{tc(t)} ({moment[int(t / an.dt)]:.2f})" for t in sorted(picked))]
    return "\n".join(lines)


def format_plan(plan: Plan) -> str:
    lines = [
        f"Plan: target {plan.settings.target:.0f}s -> {plan.total:.1f}s in {len(plan.clips)} clips "
        f"(style {plan.style}, speech mode {plan.settings.speech_mode}, hook {'on' if plan.settings.hook else 'off'})",
        f"  {'#':>2}  {'out':>13}  {'source':>15}  {'len':>4}  {'kind':<6}  {'score':>5}  reason",
    ]
    multi = len(plan.sources) > 1
    for i, c in enumerate(plan.clips, start=1):
        text = f'  "{c.text[:60]}"' if c.text else ""
        where = f"{Path(c.source or plan.source).name} " if multi else ""
        lines.append(
            f"  {i:>2}  {tc(c.out_start):>6}-{tc(c.out_end):<6}  {where}{tc(c.start):>7}-{tc(c.end):<7}  {c.duration:>4.1f}  "
            f"{c.kind:<6}  {c.score:>5.2f}  {c.reason}{text}"
        )
    for w in plan.warnings:
        lines.append(f"  ! {w}")
    return "\n".join(lines)


def timeline_png(an: Analysis, style: Style, path: str | Path, plan: Plan | None = None) -> None:
    """Draw the moment curve, speech/face bands, shot cuts and selected clips."""
    import cv2

    W, H = 1600, 440
    left, right, top = 60, 20, 30
    curve_h = 220
    band_h = 22
    img = np.full((H, W, 3), 250, dtype=np.uint8)
    dur = max(an.duration, 1e-3)

    def x_of(t: float) -> int:
        return int(left + (W - left - right) * min(max(t, 0), dur) / dur)

    moment = an.moment_curve(style)
    base_y = top + curve_h
    pts = [(left, base_y)]
    for i, v in enumerate(moment):
        pts.append((x_of(i * an.dt), int(base_y - curve_h * float(v))))
    pts.append((x_of(dur), base_y))
    cv2.fillPoly(img, [np.array(pts, dtype=np.int32)], (215, 200, 120))
    cv2.polylines(img, [np.array(pts[1:-1], dtype=np.int32)], False, (120, 90, 20), 1, cv2.LINE_AA)
    cv2.rectangle(img, (left, top), (x_of(dur), base_y), (150, 150, 150), 1)
    cv2.putText(img, f"interest ({style.name})", (left + 6, top + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 60, 60), 1, cv2.LINE_AA)

    for s in an.shots[1:]:
        x = x_of(s.start)
        cv2.line(img, (x, top), (x, base_y), (90, 90, 90), 1)
    if an.is_composite:
        for m, off in zip(an.sources, an.offsets):
            x = x_of(off)
            cv2.line(img, (x, top - 8), (x, base_y), (20, 20, 20), 2)
            cv2.putText(img, Path(m.path).name[:28], (x + 4, base_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (20, 20, 20), 1, cv2.LINE_AA)

    y = base_y + 12
    for name, curve, color in (
        ("speech", an.curves.get("speech"), (70, 160, 70)),
        ("faces", an.curves.get("faces"), (200, 120, 60)),
        ("audio", an.curves.get("audio"), (60, 60, 200)),
    ):
        if curve is None:
            continue
        cv2.putText(img, name, (6, y + band_h - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 60, 60), 1, cv2.LINE_AA)
        for i, v in enumerate(curve):
            if v <= 0.05:
                continue
            x0, x1 = x_of(i * an.dt), x_of((i + 1) * an.dt)
            shade = tuple(int(255 - (255 - c) * min(1.0, float(v))) for c in color)
            cv2.rectangle(img, (x0, y), (max(x1, x0 + 1), y + band_h), shade, -1)
        cv2.rectangle(img, (left, y), (x_of(dur), y + band_h), (150, 150, 150), 1)
        y += band_h + 6

    if plan is not None:
        colors = {"speech": (60, 160, 60), "visual": (200, 90, 40), "hook": (30, 30, 220)}
        for i, c in enumerate(plan.clips, start=1):
            x0, x1 = x_of(c.start), x_of(c.end)
            col = colors.get(c.kind, (0, 0, 0))
            overlay = img.copy()
            cv2.rectangle(overlay, (x0, top), (x1, base_y), col, -1)
            cv2.addWeighted(overlay, 0.25, img, 0.75, 0, img)
            cv2.rectangle(img, (x0, top), (x1, base_y), col, 2)
            cv2.putText(img, str(i), (x0 + 3, top + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2, cv2.LINE_AA)

    axis_y = y + 4
    cv2.line(img, (left, axis_y), (x_of(dur), axis_y), (60, 60, 60), 1)
    step = 5 if dur <= 90 else (10 if dur <= 300 else 30)
    t = 0.0
    while t <= dur:
        x = x_of(t)
        cv2.line(img, (x, axis_y), (x, axis_y + 6), (60, 60, 60), 1)
        cv2.putText(img, tc(t), (x - 18, axis_y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (60, 60, 60), 1, cv2.LINE_AA)
        t += step
    title = " + ".join(Path(m.path).name for m in an.sources) if an.is_composite else Path(an.source.path).name
    if plan is not None:
        title += f"  |  plan {plan.total:.1f}s / {len(plan.clips)} clips  (green=speech, orange=visual, red=hook)"
    cv2.putText(img, title, (left, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), img)


__all__ = ["format_analysis", "format_plan", "timeline_png"]
