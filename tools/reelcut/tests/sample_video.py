"""Build a synthetic test video with ffmpeg (and espeak-ng when available).

Timeline (seconds):
  0-4   dull static colour, quiet          (boring)
  4-8   moving test pattern + 120 BPM pulses (action)
  8-14  warm static colour + spoken sentence (interview 1)
  14-18 static colour bars, quiet          (boring)
  18-22 cool static colour + spoken sentence (interview 2)
  22-24 moving test pattern + pulses       (action)
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from reelcut.ffmpeg import ffmpeg_bin

SPEECH_1 = "This is the interview part of the video. The reel tool should keep this whole sentence intact and never cut it in the middle."
SPEECH_2 = "And here is a second answer from the interview, which also has to stay complete in the final reel."

SEGMENTS = [
    ("color=c=0x334455:size=1280x720:rate=25", 4),
    ("testsrc2=size=1280x720:rate=25", 4),
    ("color=c=0xC08040:size=1280x720:rate=25", 6),
    ("smptebars=size=1280x720:rate=25", 4),
    ("color=c=0x4080C0:size=1280x720:rate=25", 4),
    ("testsrc2=size=1280x720:rate=25", 2),
]
DURATION = sum(d for _, d in SEGMENTS)


def have_espeak() -> bool:
    return shutil.which("espeak-ng") is not None or shutil.which("espeak") is not None


def _speech_wav(text: str, path: Path) -> None:
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    subprocess.run([exe, "-s", "165", "-w", str(path), text], check=True, capture_output=True)


def build_sample(out: str | Path, *, speech: bool = True) -> Path:
    out = Path(out)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cmd = [ffmpeg_bin(), "-v", "error", "-y"]
        for src, dur in SEGMENTS:
            cmd += ["-f", "lavfi", "-t", str(dur), "-i", src]
        n_video = len(SEGMENTS)
        # audio beds
        cmd += ["-f", "lavfi", "-t", str(DURATION), "-i", "anoisesrc=color=pink:amplitude=0.01:seed=1"]
        pulses = "aevalsrc='0.7*sin(2*PI*110*t)*gt(mod(t,0.5),0.38)':s=16000"
        cmd += ["-f", "lavfi", "-t", "4", "-i", pulses]
        cmd += ["-f", "lavfi", "-t", "2", "-i", pulses]
        audio_inputs = [n_video, n_video + 1, n_video + 2]
        delays = {n_video + 1: 4000, n_video + 2: 22000}
        use_speech = speech and have_espeak()
        if use_speech:
            w1, w2 = tmp / "s1.wav", tmp / "s2.wav"
            _speech_wav(SPEECH_1, w1)
            _speech_wav(SPEECH_2, w2)
            cmd += ["-i", str(w1), "-i", str(w2)]
            audio_inputs += [n_video + 3, n_video + 4]
            delays[n_video + 3] = 8300
            delays[n_video + 4] = 18300
        else:  # speech-like modulated tone so VAD tests still have something to find
            talk = "aevalsrc='0.5*sin(2*PI*180*t)*(0.55+0.45*sin(2*PI*4.3*t))*(0.6+0.4*sin(2*PI*0.9*t))':s=16000"
            cmd += ["-f", "lavfi", "-t", "5", "-i", talk, "-f", "lavfi", "-t", "3.2", "-i", talk]
            audio_inputs += [n_video + 3, n_video + 4]
            delays[n_video + 3] = 8300
            delays[n_video + 4] = 18300

        parts = []
        for i in range(n_video):
            parts.append(f"[{i}:v]format=yuv420p,setsar=1[v{i}]")
        parts.append("".join(f"[v{i}]" for i in range(n_video)) + f"concat=n={n_video}:v=1:a=0[vout]")
        labels = []
        for idx in audio_inputs:
            lab = f"a{idx}"
            chain = f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=mono"
            if idx in delays:
                chain += f",adelay={delays[idx]}:all=1"
            chain += f",apad=whole_dur={DURATION}[{lab}]"
            parts.append(chain)
            labels.append(f"[{lab}]")
        parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=first[aout]")
        cmd += [
            "-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
            "-t", str(DURATION), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    return out


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "sample.mp4"
    print(build_sample(target))
