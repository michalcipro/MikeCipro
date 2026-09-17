"""Testy střihu. Vyrábějí a znovu čtou skutečné video — proto jsou pomalejší."""

import subprocess

import pytest

from igagent.media import ffmpeg as ff
from igagent.media.video import (ReelStudio, _atempo_chain, _input_video_chain,
                                 _truncate_clips, _wrap, srt_from_cues)


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """8 s videa, kde první a poslední 2 s jsou hlasité a střed tichý."""
    out = tmp_path_factory.mktemp("video") / "sample.mp4"
    subprocess.run([
        ff.ffmpeg_path(), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=8:sample_rate=44100",
        "-filter_complex", "[1:a]volume='if(lt(mod(t,4),2),0.9,0.001)':eval=frame[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def test_probe_reads_video_properties(sample_video):
    info = ff.probe(sample_video)
    assert info["width"] == 640 and info["height"] == 360
    assert 7.5 < info["duration"] < 8.5
    assert info["has_audio"]


def test_loudness_profile_finds_quiet_parts(brand, tmp_path, sample_video):
    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    profile = studio.loudness_profile(sample_video, window=1.0)
    assert len(profile) >= 7
    loud = [db for t, db in profile if t < 2]
    quiet = [db for t, db in profile if 2 <= t < 4]
    assert min(loud) > max(quiet)             # hlasitá část je opravdu hlasitější


def test_silence_detection(brand, tmp_path, sample_video):
    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    silences = studio.silence_ranges(sample_video, noise_db=-40, min_duration=0.4)
    assert silences
    assert any(1.8 < start < 2.6 for start, _ in silences)


def test_highlights_prefer_the_loud_sections(brand, tmp_path, sample_video):
    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    clips = studio.pick_highlights(sample_video, target_duration=4.0,
                                   clip_min=1.0, clip_max=2.0)
    assert clips
    assert all(duration >= 1.0 for _, duration in clips)
    # žádné dva úseky se nepřekrývají
    ordered = sorted(clips)
    for (start_a, dur_a), (start_b, _) in zip(ordered, ordered[1:]):
        assert start_a + dur_a <= start_b + 0.01


def test_build_produces_a_valid_vertical_reel(brand, tmp_path, sample_video):
    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    out = studio.build(sample_video, clips=[(0.0, 2.0), (4.0, 2.0)],
                       hook={"text": "Příliš žluťoučký hook", "end": 2.0},
                       captions=[{"text": "Titulek s diakritikou", "start": 2.2, "end": 3.8}],
                       name="test")
    info = ff.probe(out)
    assert info["width"] == 1080 and info["height"] == 1920
    # celá požadovaná délka — loudnorm ani -shortest nesmí uříznout konec
    assert 3.9 < info["duration"] < 4.2
    assert info["has_audio"]
    assert abs(info["fps"] - 30) < 1


def test_build_rejects_too_short_result(brand, tmp_path, sample_video):
    from igagent.errors import MediaError

    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    with pytest.raises(MediaError, match="aspoň"):
        studio.build(sample_video, clips=[(0.0, 1.0)], name="short")


def test_jumpcut_shortens_the_video(brand, tmp_path, sample_video):
    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    out = studio.jumpcut(sample_video, noise_db=-40, name="cut")
    info = ff.probe(out)
    assert info["duration"] < 7.0             # ticho vypadlo
    assert info["duration"] >= 3.0


def test_blur_fit_mode_builds(brand, tmp_path, sample_video):
    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    out = studio.build(sample_video, clips=[(0.0, 2.0), (4.0, 2.0)], fit="blur", name="blur")
    assert ff.probe(out)["width"] == 1080


def test_cover_is_extracted_vertically(brand, tmp_path, sample_video):
    from PIL import Image

    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    cover = studio.cover(sample_video, at=1.0, name="c")
    assert Image.open(cover).size == (1080, 1920)


def test_photo_reel_blur_fit_keeps_the_whole_image(brand, tmp_path):
    """U grafiky s textem se nesmí nic uříznout — proto `fit="blur"`."""
    from PIL import Image

    photos = []
    for index in range(2):
        path = tmp_path / f"card{index}.jpg"
        img = Image.new("RGB", (1080, 1350), "black")
        # bílý pruh přes celou šířku — při ořezu na 9:16 by přišel o okraje
        for x in range(1080):
            for y in range(600, 620):
                img.putpixel((x, y), (255, 255, 255))
        img.save(path)
        photos.append(path)

    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    out = studio.from_photos(photos, seconds_each=2.0, fit="blur", name="blurfit")
    info = ff.probe(out)
    assert info["width"] == 1080 and info["height"] == 1920

    frame = tmp_path / "frame.png"
    subprocess.run([ff.ffmpeg_path(), "-y", "-loglevel", "error", "-ss", "0.5",
                    "-i", str(out), "-frames:v", "1", str(frame)], check=True)
    rendered = Image.open(frame).convert("RGB")
    # celá šířka karty se musí vejít → u levého i pravého okraje je tmavé pozadí
    assert sum(rendered.getpixel((2, 960))) < 200
    assert sum(rendered.getpixel((1077, 960))) < 200


def test_reel_from_photos(brand, tmp_path):
    from PIL import Image

    photos = []
    for index, color in enumerate(("navy", "darkred", "darkgreen")):
        path = tmp_path / f"p{index}.jpg"
        Image.new("RGB", (1200, 1600), color).save(path)
        photos.append(path)

    studio = ReelStudio(brand, tmp_path / "w", tmp_path / "o")
    out = studio.from_photos(photos, seconds_each=2.0, hook={"text": "Fotky", "end": 1.5},
                             name="slideshow")
    info = ff.probe(out)
    assert info["width"] == 1080 and info["height"] == 1920
    assert info["duration"] > 3.0


# ---------------------------------------------------------------- jednotky

def test_atempo_chain_splits_large_factors():
    assert _atempo_chain(1.25) == "atempo=1.2500"
    # atempo umí max 2.0, takže 4× se musí rozložit na dva články (2,0 × 2,0)
    assert _atempo_chain(4.0) == "atempo=2.0,atempo=2.0000"
    assert _atempo_chain(6.0) == "atempo=2.0,atempo=2.0,atempo=1.5000"


def test_truncate_clips_respects_budget():
    clips = _truncate_clips([(0, 5), (10, 5), (20, 5)], 7)
    assert sum(d for _, d in clips) == pytest.approx(7, abs=0.01)


def test_blur_chain_uses_unique_labels_per_input():
    first = "".join(_input_video_chain(0, "blur"))
    second = "".join(_input_video_chain(1, "blur"))
    assert "[bg0]" in first and "[bg1]" in second
    assert "[bg0]" not in second


def test_wrap_and_srt():
    assert _wrap("a b c d e f", 3).count("\n") >= 2
    srt = srt_from_cues([{"start": 1.5, "end": 3.25, "text": "Ahoj"}])
    assert "00:00:01,500 --> 00:00:03,250" in srt
