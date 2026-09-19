from __future__ import annotations

import numpy as np

from reelcut.audio import resample_curve, segments_from_activity


def test_segments_merge_pad_and_min_duration():
    hop = 0.02
    active = np.zeros(500, dtype=bool)  # 10 s
    active[50:150] = True  # 1.0-3.0
    active[160:200] = True  # 3.2-4.0 (gap 0.2 -> merged)
    active[300:305] = True  # 0.1 s blip -> dropped
    segs = segments_from_activity(active, hop, min_duration=0.35, merge_gap=0.45, pad=0.15, total_duration=10.0)
    assert len(segs) == 1
    assert abs(segs[0].start - 0.85) < 1e-6
    assert abs(segs[0].end - 4.15) < 1e-6


def test_resample_curve_mean_and_fill():
    values = np.array([0, 0, 1, 1, 1, 1, 0, 0], dtype=np.float32)
    out = resample_curve(values, 0.05, 0.1, 5)
    assert out.shape == (5,)
    assert abs(out[1] - 1.0) < 1e-6 and abs(out[3] - 0.0) < 1e-6
    assert abs(out[4] - 0.0) < 1e-6  # filled from last known value
