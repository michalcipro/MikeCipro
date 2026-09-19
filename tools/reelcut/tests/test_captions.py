from __future__ import annotations

from reelcut.captions import build_srt, caption_entries
from reelcut.planner import Clip, Plan, PlanSettings, finalize_timeline


def test_captions_follow_output_timeline():
    clips = [Clip(10.0, 13.0, "hook", 0.9), Clip(2.0, 5.0, "speech", 0.8)]
    finalize_timeline(clips)
    plan = Plan("x.mp4", "auto", PlanSettings(), clips, [], 20.0)
    words = [
        {"start": 2.1, "end": 2.4, "word": "Hello"},
        {"start": 2.5, "end": 2.9, "word": "there"},
        {"start": 3.0, "end": 3.4, "word": "friends"},
        {"start": 3.5, "end": 4.0, "word": "again"},
        {"start": 10.5, "end": 11.0, "word": "Wow"},
        {"start": 15.0, "end": 15.5, "word": "unused"},
    ]
    entries = caption_entries(plan, words, max_words=3, max_span=2.0)
    assert [e[2] for e in entries] == ["Wow", "Hello there friends", "again"]
    assert abs(entries[0][0] - 0.5) < 1e-6  # 10.5 inside hook (out 0-3)
    assert abs(entries[1][0] - 3.1) < 1e-6  # 2.1 inside speech clip (out 3-6)
    srt = build_srt(plan, words, max_span=2.0)
    assert "00:00:00,500 --> " in srt and "HELLO THERE FRIENDS" in srt
    short = caption_entries(plan, words)  # default 1.2 s span breaks the long group
    assert [e[2] for e in short] == ["Wow", "Hello there", "friends again"]
