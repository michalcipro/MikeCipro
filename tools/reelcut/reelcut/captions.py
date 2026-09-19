"""Build reel-style captions (short word groups) from transcript words mapped onto
the output timeline of a plan."""

from __future__ import annotations

from .planner import Plan


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def caption_entries(plan: Plan, words: list[dict], *, max_words: int = 3, max_span: float = 1.2, min_show: float = 0.35) -> list[tuple[float, float, str]]:
    entries: list[tuple[float, float, str]] = []
    for clip in plan.clips:
        src = clip.source or plan.source
        inside = [
            w for w in words
            if w.get("word") and w.get("source", src) == src
            and w["start"] >= clip.start - 0.05 and w["end"] <= clip.end + 0.05
        ]
        group: list[dict] = []

        def flush() -> None:
            if not group:
                return
            a = clip.out_start + max(0.0, group[0]["start"] - clip.start)
            b = clip.out_start + min(clip.duration, group[-1]["end"] - clip.start)
            b = max(b, a + min_show)
            b = min(b, clip.out_end)
            text = " ".join(w["word"].strip() for w in group).strip()
            if text and b > a:
                entries.append((a, b, text))
            group.clear()

        for w in inside:
            if group and (len(group) >= max_words or w["end"] - group[0]["start"] > max_span):
                flush()
            group.append(w)
        flush()
    # avoid overlaps between consecutive entries
    for i in range(1, len(entries)):
        pa, pb, pt = entries[i - 1]
        a, b, t = entries[i]
        if pb > a:
            entries[i - 1] = (pa, a, pt)
    return entries


def build_srt(plan: Plan, words: list[dict], **kwargs) -> str:
    lines = []
    for i, (a, b, text) in enumerate(caption_entries(plan, words, **kwargs), start=1):
        lines += [str(i), f"{_srt_time(a)} --> {_srt_time(b)}", text.upper(), ""]
    return "\n".join(lines)


__all__ = ["build_srt", "caption_entries"]
