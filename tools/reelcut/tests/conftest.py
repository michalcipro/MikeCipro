from __future__ import annotations

import numpy as np
import pytest

from reelcut.analysis import CURVE_NAMES, DT, Analysis, AnalysisSettings, FaceSample, Shot
from reelcut.audio import SpeechSegment
from reelcut.ffmpeg import MediaInfo


def make_analysis(
    duration: float = 60.0,
    shots: list[tuple[float, float]] | None = None,
    speech: list[tuple[float, float]] | None = None,
    hot: list[tuple[float, float, float]] | None = None,
    faces: list[tuple[float, float, float, float]] | None = None,
    width: int = 1920,
    height: int = 1080,
    has_audio: bool = True,
) -> Analysis:
    """Build a synthetic Analysis. ``hot`` = (start, end, level) regions of motion+audio."""
    n = int(np.ceil(duration / DT)) + 1
    curves = {k: np.full(n, 0.1, dtype=np.float32) for k in CURVE_NAMES}
    curves["exposure"][:] = 1.0
    curves["speech"][:] = 0.0
    curves["faces"][:] = 0.0
    for a, b, level in hot or []:
        curves["motion"][int(a / DT):int(b / DT)] = level
        curves["audio"][int(a / DT):int(b / DT)] = level
    speech_prob = np.zeros(n, dtype=np.float32)
    segs = []
    for a, b in speech or []:
        curves["speech"][int(a / DT):int(b / DT)] = 1.0
        speech_prob[int(a / DT):int(b / DT)] = 0.9
        segs.append(SpeechSegment(a, b))
    track = []
    for t, cx, cy, size in faces or []:
        curves["faces"][int(t / DT):int(t / DT) + 5] = 1.0
        track.append(FaceSample(t, cx, cy, size, 1))
    shots = shots or [(0.0, duration)]
    shot_objs = []
    for i, (a, b) in enumerate(shots):
        ia, ib = int(a / DT), max(int(a / DT) + 1, int(b / DT))
        shot_objs.append(Shot(i, a, b, {k: float(curves[k][ia:ib].mean()) for k in CURVE_NAMES}))
    info = MediaInfo("fake.mp4", duration, width, height, 30.0, 0, has_audio, 48000 if has_audio else None, "h264", 1, 0.0)
    return Analysis(
        source=info,
        settings=AnalysisSettings(),
        dt=DT,
        curves=curves,
        speech_prob=speech_prob,
        shots=shot_objs,
        speech_segments=segs,
        onsets=[],
        tempo_bpm=None,
        face_track=track,
    )


@pytest.fixture
def analysis() -> Analysis:
    return make_analysis(
        duration=60.0,
        shots=[(0, 10), (10, 25), (25, 40), (40, 60)],
        speech=[(12.0, 20.0), (44.0, 50.0)],
        hot=[(30.0, 34.0, 0.9), (52.0, 55.0, 0.7), (2.0, 4.0, 0.5)],
        faces=[(13.0, 0.3, 0.4, 0.2), (15.0, 0.3, 0.4, 0.2), (17.0, 0.32, 0.4, 0.2)],
    )
