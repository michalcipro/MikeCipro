"""Optional speech transcription with faster-whisper (pip install reelcut[transcribe]).

Provides sentence-level segments (used as atomic speech units by the planner)
and word timestamps (used for burned-in captions).
"""

from __future__ import annotations


def transcribe(path: str, *, model_size: str = "small", language: str | None = None) -> dict:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Transcription requires faster-whisper. Install with: pip install 'reelcut[transcribe]'"
        ) from exc

    from .audio import load_audio

    audio = load_audio(path)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    out_segments = []
    words = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        out_segments.append({"start": float(seg.start), "end": float(seg.end), "text": text})
        for w in seg.words or []:
            words.append({"start": float(w.start), "end": float(w.end), "word": w.word.strip()})
    return {
        "language": getattr(info, "language", language),
        "model": model_size,
        "segments": out_segments,
        "words": words,
    }


__all__ = ["transcribe"]
