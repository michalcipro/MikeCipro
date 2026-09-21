"""reelcut - analyse a video and cut its best moments into an Instagram Reel.

Pipeline: probe -> audio analysis (loudness, VAD, onsets) -> video analysis
(shots, motion, sharpness, faces) -> scoring -> edit plan -> ffmpeg render.
"""

__version__ = "0.2.0"
