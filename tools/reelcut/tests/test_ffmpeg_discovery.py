from __future__ import annotations

import os
import stat

import pytest

from reelcut import ffmpeg as ff
from reelcut.render import RenderSettings, _encoder_args, pick_video_encoder


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    ff.reset_cache()
    monkeypatch.setattr(ff, "REELCUT_HOME", tmp_path / "home")
    for var in ("REELCUT_FFMPEG", "REELCUT_FFPROBE", "REELCUT_FFMPEG_DIR"):
        monkeypatch.delenv(var, raising=False)
    yield
    ff.reset_cache()


def _fake_exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho fake\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_env_dir_wins_over_path(monkeypatch, tmp_path):
    exe = _fake_exe(tmp_path / "bin" / "ffmpeg")
    monkeypatch.setenv("REELCUT_FFMPEG_DIR", str(tmp_path / "bin"))
    assert ff.find_binary("ffmpeg") == str(exe)


def test_explicit_path_env(monkeypatch, tmp_path):
    exe = _fake_exe(tmp_path / "custom" / "ffprobe-custom")
    monkeypatch.setenv("REELCUT_FFPROBE", str(exe))
    assert ff.find_binary("ffprobe") == str(exe)


def test_home_bin_is_searched(monkeypatch, tmp_path):
    exe = _fake_exe(tmp_path / "home" / "bin" / "ffmpeg")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert ff.find_binary("ffmpeg") == str(exe)


def test_missing_without_download(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert ff.find_binary("ffmpeg", download=False) is None


def test_encoder_fallback(monkeypatch):
    monkeypatch.setattr("reelcut.render.capabilities", lambda: {"encoders": {"h264_videotoolbox", "aac"}, "filters": set()})
    assert pick_video_encoder() == "h264_videotoolbox"
    with pytest.raises(ff.FFmpegError):
        pick_video_encoder("libx264")
    monkeypatch.setattr("reelcut.render.capabilities", lambda: {"encoders": set(), "filters": set()})
    with pytest.raises(ff.FFmpegError):
        pick_video_encoder()


def test_encoder_args():
    s = RenderSettings(crf=20, preset="fast")
    assert "-crf" in _encoder_args("libx264", s) and "fast" in _encoder_args("libx264", s)
    vt = _encoder_args("h264_videotoolbox", s)
    assert "-crf" not in vt and "-b:v" in vt
