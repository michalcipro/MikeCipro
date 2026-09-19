"""End-to-end tests on a synthetic video (skipped when ffmpeg is unavailable)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from reelcut.analysis import AnalysisSettings, build_analysis, combine_analyses
from reelcut.cli import main
from reelcut.ffmpeg import ffmpeg_bin, find_binary, probe
from reelcut.planner import Clip, PlanSettings, build_plan, finalize_timeline
from reelcut.render import RenderSettings, build_command, render

from sample_video import build_sample, have_espeak

pytestmark = pytest.mark.skipif(find_binary("ffmpeg") is None or find_binary("ffprobe") is None, reason="ffmpeg missing")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    path = tmp_path_factory.mktemp("video") / "sample.mp4"
    return build_sample(path)


@pytest.fixture(scope="module")
def analysis(sample):
    return build_analysis(str(sample), AnalysisSettings())


def test_probe(sample):
    info = probe(sample)
    assert (info.width, info.height) == (1280, 720)
    assert info.has_audio and abs(info.duration - 24.0) < 0.2


def test_shots_match_scene_changes(analysis):
    starts = [s.start for s in analysis.shots]
    for expected in (4.0, 8.0, 14.0, 18.0, 22.0):
        assert any(abs(x - expected) < 0.35 for x in starts), f"missing cut at {expected}: {starts}"
    assert len(analysis.shots) == 6


def test_speech_and_tempo(analysis):
    segs = analysis.speech_segments
    assert segs, "speech should be detected"
    assert any(s.start <= 9.0 and s.end >= 12.0 for s in segs), segs
    assert any(s.start <= 19.0 and s.end >= 21.0 for s in segs), segs
    assert not any(s.end <= 8.0 for s in segs), "the pulse section is not speech"
    assert analysis.tempo_bpm is not None and abs(analysis.tempo_bpm - 120.0) < 3.0
    assert len(analysis.onsets) > 10


def test_plan_and_render(analysis, tmp_path):
    plan = build_plan(analysis, PlanSettings(target=12.0), "auto")
    assert plan.clips and plan.total <= 12.0 * 1.08 + 1e-6
    speech = [c for c in plan.clips if c.kind == "speech"]
    assert speech and all(c.duration >= 4.0 for c in speech)
    for c in plan.clips:
        assert 0 <= c.start < c.end <= analysis.duration + 1e-6
    out = tmp_path / "reel.mp4"
    render(plan, RenderSettings(preset="ultrafast", crf=28), str(out), analysis.source)
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert abs(info.duration - plan.total) < 0.5
    assert info.has_audio


def test_render_fade_and_blur_command(analysis, tmp_path):
    plan = build_plan(analysis, PlanSettings(target=10.0), "action")
    cmd = build_command(plan, analysis.source, RenderSettings(fit="blur", transition="fade", preset="ultrafast"), str(tmp_path / "x.mp4"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "xfade=" in graph and "acrossfade=" in graph and "boxblur" in graph
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-500:]
    expected = plan.total - 0.25 * (len(plan.clips) - 1)
    assert abs(probe(tmp_path / "x.mp4").duration - expected) < 0.5


def test_vertical_source_without_audio(tmp_path):
    src = tmp_path / "vert.mp4"
    subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-t", "5", "-i", "testsrc2=size=720x1280:rate=25",
         "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(src)],
        check=True,
    )
    an = build_analysis(str(src), AnalysisSettings())
    assert not an.source.has_audio and not an.speech_segments
    plan = build_plan(an, PlanSettings(target=3.0), "auto")
    assert plan.clips
    out = tmp_path / "vert_reel.mp4"
    render(plan, RenderSettings(preset="ultrafast", crf=30), str(out), an.source)
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920) and not info.has_audio


def test_cli_smoke(sample, tmp_path, capsys):
    assert main(["analyze", str(sample), "--no-cache", "--timeline", str(tmp_path / "tl.png")]) == 0
    assert (tmp_path / "tl.png").exists()
    out = capsys.readouterr().out
    assert "Shots" in out and "Speech" in out
    assert main(["cut", str(sample), "--target", "10", "-o", str(tmp_path / "r.mp4"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg" in out and "Plan:" in out
    assert (tmp_path / "r_plan.json").exists()
    assert main(["render", str(tmp_path / "r_plan.json"), "-o", str(tmp_path / "r2.mp4"), "--dry-run"]) == 0


@pytest.mark.skipif(not have_espeak(), reason="espeak-ng missing")
def test_real_speech_detected_as_speech(analysis):
    # with real synthetic speech both sentences must be found with decent timing
    segs = sorted(analysis.speech_segments, key=lambda s: s.start)
    assert abs(segs[0].start - 8.3) < 0.6
    assert abs(segs[-1].start - 18.3) < 0.6


@pytest.fixture(scope="module")
def vertical_silent(tmp_path_factory):
    src = tmp_path_factory.mktemp("video2") / "vert.mp4"
    subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-t", "6", "-i", "testsrc2=size=720x1280:rate=25",
         "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(src)],
        check=True,
    )
    return src


def test_multi_source_render(analysis, vertical_silent, tmp_path):
    an2 = build_analysis(str(vertical_silent), AnalysisSettings())
    an = combine_analyses([analysis, an2])
    assert an.is_composite and abs(an.duration - 30.0) < 0.3
    plan = build_plan(an, PlanSettings(target=10.0, min_quality=0.0), "auto")
    assert plan.clips and all(c.source for c in plan.clips)
    for c in plan.clips:  # every clip lies inside its own file
        limit = analysis.duration if c.source == analysis.source.path else an2.duration
        assert 0 <= c.start < c.end <= limit + 1e-6, c
    if str(vertical_silent) not in plan.sources:  # the silent test pattern scores low; force a clip from it
        plan.clips.append(Clip(1.0, 3.0, "visual", 0.5, "manual", source=str(vertical_silent)))
        finalize_timeline(plan.clips)
    assert len(plan.sources) == 2, plan.sources
    for fit in ("crop", "none"):
        out = tmp_path / f"multi_{fit}.mp4"
        render(plan, RenderSettings(preset="ultrafast", crf=30, fit=fit), str(out))
        info = probe(out)
        assert info.has_audio  # silence is generated for the clip without audio
        assert abs(info.duration - plan.total) < 0.5
        if fit == "crop":
            assert (info.width, info.height) == (1080, 1920)
        else:
            assert (info.width, info.height) == (1280, 720)  # first clip's frame, others letterboxed


def test_cli_multi_input(sample, vertical_silent, tmp_path, capsys):
    assert main(["cut", str(sample), str(vertical_silent), "--target", "12", "--min-quality", "0",
                 "-o", str(tmp_path / "m.mp4"), "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "video 1/2" in captured.err and "video 2/2" in captured.err
    assert "Plan:" in captured.out and "ffmpeg" in captured.out
    assert (tmp_path / "m_plan.json").exists()
