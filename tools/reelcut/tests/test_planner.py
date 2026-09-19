from __future__ import annotations

import json

import numpy as np

from reelcut.analysis import resolve_style
from reelcut.planner import Plan, PlanSettings, _best_windows, _split_speech, _subtract, build_plan

from conftest import make_analysis


def test_subtract_intervals():
    assert _subtract((0, 10), []) == [(0, 10)]
    assert _subtract((0, 10), [(2, 4), (6, 7)]) == [(0, 2), (4, 6), (7, 10)]
    assert _subtract((0, 10), [(-1, 3), (8, 12)]) == [(3, 8)]
    assert _subtract((0, 10), [(0, 10)]) == []


def test_best_windows_finds_peak():
    curve = np.zeros(200, dtype=np.float32)
    curve[100:120] = 1.0
    wins = _best_windows(curve, 0.1, 0.0, 20.0, 2.0, 1, min_sep=1.0)
    assert len(wins) == 1
    ws, we, score = wins[0]
    assert abs(ws - 10.0) < 0.15 and abs(we - 12.0) < 0.15
    assert score > 0.99
    more = _best_windows(curve, 0.1, 0.0, 20.0, 2.0, 3, min_sep=1.0)
    starts = sorted(w[0] for w in more)
    assert all(b - a >= 2.0 for a, b in zip(starts, starts[1:]))


def test_split_speech_at_pause():
    an = make_analysis(duration=40.0, speech=[(5.0, 35.0)])
    an.speech_prob[int(20.0 / an.dt):int(20.6 / an.dt)] = 0.05  # a pause at 20s
    parts = _split_speech(an, 5.0, 35.0, limit=20.0)
    assert len(parts) == 2
    assert abs(parts[0][1] - 20.3) < 0.4
    assert all(b - a <= 20.0 + 1e-6 for a, b in parts)


def _clips_of(plan: Plan, kind: str):
    return [c for c in plan.clips if c.kind == kind]


def test_plan_keeps_speech_whole_and_respects_budget(analysis):
    s = PlanSettings(target=20.0)
    plan = build_plan(analysis, s, "auto")
    assert plan.clips, "plan should not be empty"
    assert plan.total <= s.target * (1 + s.tolerance) + 1e-6
    speech = _clips_of(plan, "speech")
    assert speech, "speech should be kept"
    for c in speech:
        seg = next(x for x in analysis.speech_segments if abs(x.start - c.start) < 0.5)
        assert c.start <= seg.start and c.end >= seg.end, "speech must not be cut mid-sentence"
    for c in plan.clips:
        assert 0.0 <= c.start < c.end <= analysis.duration
    # visual clips never cross a shot boundary
    for c in _clips_of(plan, "visual"):
        assert any(sh.start - 1e-6 <= c.start and c.end <= sh.end + 1e-6 for sh in analysis.shots)
    # output timeline is contiguous
    t = 0.0
    for c in plan.clips:
        assert abs(c.out_start - t) < 1e-6
        t += c.duration


def test_hook_is_first_and_is_the_strongest_moment(analysis):
    plan = build_plan(analysis, PlanSettings(target=20.0), "auto")
    assert plan.clips[0].kind == "hook"
    hook = plan.clips[0]
    assert 29.5 <= hook.start <= 32.0, f"hook should come from the 30-34s peak, got {hook.start}"
    assert hook.duration <= 2.5 + 1e-6
    plan_no_hook = build_plan(analysis, PlanSettings(target=20.0, hook=False), "auto")
    assert all(c.kind != "hook" for c in plan_no_hook.clips)
    starts = [c.start for c in plan_no_hook.clips]
    assert starts == sorted(starts)


def test_speech_modes(analysis):
    ignore = build_plan(analysis, PlanSettings(target=15.0, speech_mode="ignore"), "action")
    assert not _clips_of(ignore, "speech")
    both = build_plan(analysis, PlanSettings(target=15.0, speech_mode="keep"), "auto")
    assert len(_clips_of(both, "speech")) == 2  # 8.3 s + 6.3 s fit into 15 s (+8 %)
    keep = build_plan(analysis, PlanSettings(target=12.0, speech_mode="keep"), "auto")
    kept = _clips_of(keep, "speech")
    assert len(kept) == 1 and any("kept 1 of 2" in w for w in keep.warnings)
    assert kept[0].duration <= 12.0 * 1.08


def test_long_speech_is_split_to_fit_target():
    an = make_analysis(duration=50.0, speech=[(5.0, 45.0)])
    an.speech_prob[int(24.8 / an.dt):int(25.4 / an.dt)] = 0.02
    plan = build_plan(an, PlanSettings(target=20.0, max_speech_clip=15.0), "talk")
    speech = _clips_of(plan, "speech")
    assert speech
    assert all(c.duration <= 15.0 + 0.4 for c in speech)
    assert plan.total <= 20.0 * 1.08 + 1e-6


def test_quality_floor_and_shrink_to_fit():
    an = make_analysis(duration=30.0, shots=[(0, 10), (10, 20), (20, 30)], hot=[(10.0, 20.0, 0.9)])
    strict = build_plan(an, PlanSettings(target=12.0, hook=False, min_quality=0.5), "action")
    assert all(10.0 - 1e-6 <= c.start and c.end <= 20.0 + 1e-6 for c in strict.clips)
    loose = build_plan(an, PlanSettings(target=12.0, hook=False, min_quality=0.0), "action")
    assert loose.total >= strict.total
    assert loose.total <= 12.0 * 1.08 + 1e-6


def test_face_crop_center_is_used(analysis):
    plan = build_plan(analysis, PlanSettings(target=20.0), "talk")
    speech = [c for c in plan.clips if c.kind in ("speech", "hook") and c.start < 13.0 < c.end]
    assert speech, "the speech segment with faces should be selected"
    assert abs(speech[0].crop_cx - 0.3) < 0.05


def test_plan_json_roundtrip(tmp_path, analysis):
    plan = build_plan(analysis, PlanSettings(target=20.0), "vibe")
    path = tmp_path / "plan.json"
    plan.save(path)
    loaded = Plan.load(path)
    assert loaded.style == "vibe"
    assert len(loaded.clips) == len(plan.clips)
    assert abs(loaded.total - plan.total) < 1e-3
    data = json.loads(path.read_text())
    assert data["clips"][0]["kind"] == plan.clips[0].kind


def test_style_overrides():
    style = resolve_style("auto", {"motion": 5.0})
    assert style.weights["motion"] == 5.0
    assert style.name.endswith("custom")


def test_combined_analysis_and_multi_source_plan():
    from reelcut.analysis import combine_analyses

    a = make_analysis(duration=20.0, shots=[(0, 10), (10, 20)], speech=[(16.0, 19.9)], hot=[(3.0, 6.0, 0.9)])
    a.source.path = "a.mp4"
    b = make_analysis(duration=15.0, shots=[(0, 15)], speech=[(0.1, 4.0)], hot=[(8.0, 11.0, 0.8)], width=720, height=1280)
    b.source.path = "b.mp4"
    an = combine_analyses([a, b])
    assert an.is_composite and an.boundaries == [20.0] and abs(an.duration - 35.0) < 1e-6
    assert [s.start for s in an.shots] == [0.0, 10.0, 20.0]
    assert an.locate(25.0) == (b.source, 5.0) and an.part_range(25.0) == (20.0, 35.0)
    assert an.split_by_boundaries(18.0, 23.0) == [(18.0, 20.0), (20.0, 23.0)]
    assert [(round(s.start, 1), round(s.end, 1)) for s in an.speech_segments] == [(16.0, 19.9), (20.1, 24.0)]

    plan = build_plan(an, PlanSettings(target=20.0), "auto")
    assert plan.clips and set(plan.sources) <= {"a.mp4", "b.mp4"} and len(plan.sources) == 2
    for c in plan.clips:
        assert c.source in ("a.mp4", "b.mp4")
        limit = 20.0 if c.source == "a.mp4" else 15.0
        assert 0.0 <= c.start < c.end <= limit + 1e-6, c
    speech = [c for c in plan.clips if c.kind == "speech"]
    # the two speech segments touch at the boundary but must stay separate clips from separate files
    assert len(speech) == 2 and {c.source for c in speech} == {"a.mp4", "b.mp4"}
    assert all(c.duration <= 4.5 for c in speech)
    loaded = Plan.from_dict(plan.to_dict())
    assert loaded.sources == plan.sources and loaded.clips[0].source == plan.clips[0].source


def test_pauses_inside_speech_are_removed():
    an = make_analysis(duration=40.0, speech=[(10.0, 20.0)])
    an.speech_prob[int(14.0 / an.dt):int(15.6 / an.dt)] = 0.02  # 1.6 s of silence inside the answer
    an.speech_prob[int(17.0 / an.dt):int(17.4 / an.dt)] = 0.02  # short breath: stays
    plan = build_plan(an, PlanSettings(target=15.0, hook=False), "talk")
    speech = sorted([c for c in plan.clips if c.kind == "speech"], key=lambda c: c.start)
    assert len(speech) == 2, speech
    assert abs(speech[0].end - 14.15) < 0.15 and abs(speech[1].start - 15.45) < 0.15
    assert "pause removed" in speech[0].reason and "continues" in speech[1].reason
    assert abs(speech[0].out_start + speech[0].duration - speech[1].out_start) < 1e-6  # consecutive in the reel
    kept = build_plan(an, PlanSettings(target=15.0, hook=False, max_pause=0.0), "talk")
    assert len([c for c in kept.clips if c.kind == "speech"]) == 1
    loaded = Plan.from_dict(plan.to_dict())
    assert len(loaded.clips) == len(plan.clips)
