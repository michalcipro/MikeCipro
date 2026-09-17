"""Střih Reels.

Co to umí:
  * najít v delším videu zajímavá místa (změny scény + hlasitost) a poskládat
    z nich sestřih v cílové délce,
  * vyhodit hluchá místa (jump cut) u mluveného videa,
  * překlopit cokoliv do 9:16 (ořez nebo rozmazané pozadí),
  * vypálit hook a titulky přímo do obrazu,
  * přimíchat hudbu s uhnutím pod hlasem (ducking) a srovnat hlasitost,
  * vytáhnout/obrandit obálku,
  * udělat Reel z fotek (Ken Burns).

Texty se do ffmpeg předávají přes `textfile=`, ne inline — česká diakritika
a interpunkce tak nemusí procházet escapováním filtergrafu.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..errors import MediaError
from ..util import ensure_dir, get_logger, slugify
from . import ffmpeg as ff
from . import fonts as fontlib
from .overlays import TextOverlayRenderer

log = get_logger(__name__)

REEL_W, REEL_H = 1080, 1920
REEL_FPS = 30
MIN_REEL_SECONDS = 3.0
MAX_REEL_SECONDS = 180.0        # Instagram zvládne víc, ale delší Reel nikdo nedokouká

_SCENE_RE = re.compile(r"pts_time:(\d+\.?\d*)")
_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?\d+\.?\d*|-inf)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+\.?\d*)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+\.?\d*)")


class ReelStudio:
    def __init__(self, brand, work_dir, out_dir):
        self.brand = brand
        self.work_dir = ensure_dir(work_dir)
        self.out_dir = ensure_dir(out_dir)
        self.font_bold = fontlib.resolve("bold", brand.fonts or {})
        self.font_regular = fontlib.resolve("regular", brand.fonts or {})
        self.overlays = TextOverlayRenderer(brand, self.work_dir, (REEL_W, REEL_H))

    # ================================================================ analýza
    @staticmethod
    def probe(path):
        return ff.probe(path)

    def scene_changes(self, path, threshold=0.28, limit=400):
        """Časy, kde se výrazně mění obraz."""
        stderr = ff.run_filter_probe([
            "-i", str(path), "-filter:v",
            f"select='gt(scene,{threshold})',metadata=print:file=-",
            "-an", "-f", "null", "-"])
        times = [float(m) for m in _SCENE_RE.findall(stderr)]
        return sorted(set(times))[:limit]

    def loudness_profile(self, path, window=1.0):
        """Hrubý průběh hlasitosti: [(čas, dBFS)] po `window` sekundách."""
        info = ff.probe(path)
        if not info.get("has_audio"):
            return []
        stderr = ff.run_filter_probe([
            "-i", str(path), "-vn", "-af",
            f"aresample=8000,asetnsamples=n={int(8000 * window)},"
            "astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f", "null", "-"])
        values = []
        for index, raw in enumerate(_RMS_RE.findall(stderr)):
            db = -90.0 if raw == "-inf" else float(raw)
            values.append((index * window, db))
        return values

    def silence_ranges(self, path, noise_db=-32, min_duration=0.4):
        """Úseky ticha — vstup pro jump cut."""
        info = ff.probe(path)
        if not info.get("has_audio"):
            return []
        stderr = ff.run_filter_probe([
            "-i", str(path), "-vn", "-af",
            f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-"])
        starts = [float(v) for v in _SILENCE_START_RE.findall(stderr)]
        ends = [float(v) for v in _SILENCE_END_RE.findall(stderr)]
        ranges = []
        for index, start in enumerate(starts):
            end = ends[index] if index < len(ends) else info["duration"]
            ranges.append((start, end))
        return ranges

    def speech_segments(self, path, noise_db=-32, min_silence=0.45, pad=0.12):
        """Doplněk k tichu: úseky, kde se mluví (s malým nádechem kolem)."""
        info = ff.probe(path)
        duration = info["duration"]
        silences = self.silence_ranges(path, noise_db, min_silence)
        if not silences:
            return [(0.0, duration)]
        segments, cursor = [], 0.0
        for start, end in silences:
            if start - cursor > 0.35:
                segments.append((max(0.0, cursor - pad), min(duration, start + pad)))
            cursor = end
        if duration - cursor > 0.35:
            segments.append((max(0.0, cursor - pad), duration))
        return segments

    # ------------------------------------------------------------ výběr klipů
    def pick_highlights(self, path, target_duration=28.0, clip_min=2.2, clip_max=6.0,
                        skip_edges=0.04):
        """Vybere nepřekrývající se úseky s nejvyšší „energií".

        Energie = hlasitost (mluvené slovo) + hustota změn scény (akce).
        Když nemáme ani zvuk, ani scény, rozdělí video rovnoměrně.
        """
        info = ff.probe(path)
        duration = info.get("duration") or 0.0
        if duration <= 0:
            raise MediaError(f"Nepodařilo se zjistit délku videa: {path}")
        if duration <= target_duration * 1.15:
            return [(0.0, min(duration, MAX_REEL_SECONDS))]

        start_bound = duration * skip_edges
        end_bound = duration * (1 - skip_edges)
        step = 0.5
        buckets = [start_bound + i * step
                   for i in range(int((end_bound - start_bound) / step))]
        if not buckets:
            return [(0.0, min(duration, target_duration))]

        loud = self.loudness_profile(path, window=1.0)
        scenes = self.scene_changes(path)

        def loudness_at(t):
            if not loud:
                return 0.5
            index = min(int(t), len(loud) - 1)
            db = loud[index][1]
            return max(0.0, min(1.0, (db + 60) / 45))       # -60 dB → 0, -15 dB → 1

        def scene_density(t, radius=2.0):
            if not scenes:
                return 0.0
            near = sum(1 for s in scenes if abs(s - t) <= radius)
            return min(1.0, near / 3.0)

        scored = [(t, 0.62 * loudness_at(t) + 0.38 * scene_density(t)) for t in buckets]
        scored.sort(key=lambda pair: pair[1], reverse=True)

        clips, used = [], []
        clip_len = max(clip_min, min(clip_max, target_duration / 5))
        total = 0.0
        for time_point, _score in scored:
            if total >= target_duration:
                break
            start = max(start_bound, time_point - clip_len * 0.35)
            end = min(end_bound, start + clip_len)
            if end - start < clip_min:
                continue
            if any(start < u_end and end > u_start for u_start, u_end in used):
                continue
            # zarovnej na nejbližší změnu scény, ať střih nesekne uprostřed pohybu
            snapped = _snap_to_scene(start, scenes, tolerance=0.6)
            if not any(snapped < u_end and snapped + (end - start) > u_start
                       for u_start, u_end in used):
                start = snapped
                end = min(end_bound, start + clip_len)
            used.append((start, end))
            clips.append((round(start, 2), round(end - start, 2)))
            total += end - start
        clips.sort()
        if not clips:
            clips = [(0.0, min(duration, target_duration))]
        return clips

    # ================================================================ sestřih
    def build(self, source, clips=None, target_duration=28.0, fit="crop",
              hook=None, captions=None, music=None, music_volume=0.16,
              keep_original_audio=True, normalize_audio=True, watermark=True,
              progress_bar=False, speed=1.0, name=None, out_path=None):
        """Poskládá hotový Reel 1080×1920 / 30 fps / H.264+AAC."""
        source = Path(source)
        info = ff.probe(source)
        if clips is None:
            clips = self.pick_highlights(source, target_duration=target_duration)
        clips = [(float(s), float(d)) for s, d in clips if float(d) > 0.2]
        if not clips:
            raise MediaError("Nezbyl žádný použitelný úsek videa.")

        total = sum(d for _, d in clips) / max(speed, 0.01)
        if total < MIN_REEL_SECONDS:
            raise MediaError(
                f"Výsledek by měl {total:.1f}s, Instagram chce aspoň {MIN_REEL_SECONDS}s.")
        if total > MAX_REEL_SECONDS:
            log.warning("Sestřih má %.0fs, zkracuji na %.0fs.", total, MAX_REEL_SECONDS)
            clips = _truncate_clips(clips, MAX_REEL_SECONDS * speed)
            total = sum(d for _, d in clips) / max(speed, 0.01)

        has_audio = info.get("has_audio") and keep_original_audio
        inputs, filters, vlabels, alabels = [], [], [], []

        for index, (start, duration) in enumerate(clips):
            inputs += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source)]
            filters.extend(_input_video_chain(index, fit))
            vlabels.append(f"[v{index}]")
            if has_audio:
                filters.append(f"[{index}:a]aresample=async=1:first_pts=0[a{index}]")
                alabels.append(f"[a{index}]")

        next_input = len(clips)
        if vlabels:
            filters.append(f"{''.join(vlabels)}concat=n={len(vlabels)}:v=1:a=0[vcat]")
        vstream = "[vcat]"

        if alabels:
            filters.append(f"{''.join(alabels)}concat=n={len(alabels)}:v=0:a=1[acat]")
            astream = "[acat]"
        else:
            inputs += ["-f", "lavfi", "-t", f"{total * speed:.3f}",
                       "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            astream = f"[{next_input}:a]"
            next_input += 1

        if speed and abs(speed - 1.0) > 0.01:
            filters.append(f"{vstream}setpts=PTS/{speed}[vspd]")
            vstream = "[vspd]"
            filters.append(f"{astream}{_atempo_chain(speed)}[aspd]")
            astream = "[aspd]"

        # hudba pod hlasem (s uhnutím, aby nepřekřičela mluvené slovo)
        if music and Path(music).exists():
            inputs += ["-stream_loop", "-1", "-i", str(music)]
            music_label = f"[{next_input}:a]"
            next_input += 1
            filters.append(
                f"{music_label}volume={music_volume},atrim=0:{total * speed:.3f},"
                f"afade=t=in:st=0:d=0.6,afade=t=out:st={max(0.0, total * speed - 1.2):.3f}:d=1.2"
                "[music]")
            if has_audio:
                filters.append(f"{astream}asplit=2[voice][sc]")
                filters.append("[music][sc]sidechaincompress=threshold=0.05:ratio=8:"
                               "attack=5:release=300[duck]")
                filters.append("[voice][duck]amix=inputs=2:duration=first:"
                               "dropout_transition=0,volume=1.4[amix]")
            else:
                filters.append("[music]anull[amix]")
            astream = "[amix]"

        if normalize_audio:
            # loudnorm má dopředný buffer a jeho stream končí dřív než obraz;
            # `apad` chybějící konec doplní tichem, aby se video neuřízlo.
            filters.append(f"{astream}loudnorm=I=-14:TP=-1.5:LRA=11,apad[anorm]")
            astream = "[anorm]"

        # texty v obraze (PNG vrstvy vyrobené v Pillow)
        specs = self._overlay_specs(hook=hook, captions=captions, watermark=watermark,
                                    total=total, progress=progress_bar)
        ov_inputs, ov_filters, vstream = self._overlay_filters(vstream, specs, next_input)
        inputs += ov_inputs
        filters += ov_filters
        next_input += len(specs)

        out = Path(out_path) if out_path else self.out_dir / f"{slugify(name or source.stem)}-reel.mp4"
        ensure_dir(out.parent)
        args = [*inputs, "-filter_complex", ";".join(filters),
                "-map", vstream, "-map", astream,
                "-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", str(REEL_FPS), "-g", str(REEL_FPS * 2),
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                # pevná délka místo `-shortest`: audio filtry (loudnorm, amix)
                # i smyčkované PNG vrstvy by jinak délku určovaly za nás
                "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]
        ff.run(args)
        result = ff.probe(out)
        log.info("Reel hotov: %s (%.1fs, %dx%d)", out, result["duration"],
                 result["width"], result["height"])
        return out

    def jumpcut(self, source, noise_db=-32, min_silence=0.45, max_duration=None, **kwargs):
        """Vyhodí hluchá místa — pro mluvené video zkrátí délku i o třetinu."""
        segments = self.speech_segments(source, noise_db=noise_db, min_silence=min_silence)
        clips = [(start, end - start) for start, end in segments if end - start > 0.3]
        if max_duration:
            clips = _truncate_clips(clips, max_duration)
        if not clips:
            raise MediaError("Ve videu jsem nenašel žádnou řeč — zkus jiný práh (noise_db).")
        log.info("Jump cut: %d úseků, celkem %.1fs", len(clips), sum(d for _, d in clips))
        return self.build(source, clips=clips, **kwargs)

    # ------------------------------------------------------------ text v obraze
    def _overlay_specs(self, hook=None, captions=None, watermark=True, total=0.0,
                       progress=False):
        """Seznam vrstev k překrytí: (cesta k PNG, začátek, konec)."""
        specs = []
        if hook:
            text = hook.get("text") if isinstance(hook, dict) else str(hook)
            start = float(hook.get("start", 0.0)) if isinstance(hook, dict) else 0.0
            end = float(hook.get("end", 2.8)) if isinstance(hook, dict) else 2.8
            if text:
                specs.append((self.overlays.text_layer(
                    text, y_ratio=0.22, max_size=96, max_lines=3, accent_bar=True),
                    start, end))
        for cue in captions or []:
            if not cue.get("text"):
                continue
            specs.append((self.overlays.text_layer(
                cue["text"], y_ratio=float(cue.get("y", 0.74)),
                max_size=int(cue.get("size", 62)), max_lines=3,
                box_alpha=int(cue.get("box_alpha", 135))),
                float(cue.get("start", 0.0)), float(cue.get("end", 3.0))))
        if watermark and self.brand.handle:
            specs.append((self.overlays.handle_layer(), None, None))
        if progress and total > 0:
            # jednoduchá aproximace plynulého proužku: 10 kroků
            steps = 10
            for step in range(steps):
                specs.append((self.overlays.progress_layer((step + 1) / steps),
                              total * step / steps, total * (step + 1) / steps))
        return specs

    @staticmethod
    def _overlay_filters(vstream, specs, first_input_index):
        """Z PNG vrstev udělá vstupy ffmpeg a overlay filtry."""
        inputs, filters = [], []
        current = vstream
        for offset, (path, start, end) in enumerate(specs):
            index = first_input_index + offset
            inputs += ["-loop", "1", "-i", str(path)]
            label = f"[vov{offset}]"
            enable = ""
            if start is not None and end is not None:
                enable = f":enable='between(t,{float(start):.2f},{float(end):.2f})'"
            filters.append(f"{current}[{index}:v]overlay=0:0:format=auto{enable}{label}")
            current = label
        return inputs, filters, current

    # ------------------------------------------------------------ obálka
    def cover(self, video, at=None, name=None):
        """Vytáhne snímek jako obálku Reelu (JPG 1080×1920)."""
        info = ff.probe(video)
        timestamp = at if at is not None else min(info["duration"] * 0.25, 3.0)
        out = self.out_dir / f"{slugify(name or Path(video).stem)}-cover.jpg"
        ff.run(["-ss", f"{timestamp:.2f}", "-i", str(video), "-frames:v", "1",
                "-vf", f"{_fit_chain('crop')}", "-q:v", "2", str(out)])
        return out

    def branded_cover(self, video, title, subtitle=None, at=None, name=None):
        """Obálka se snímkem z videa + titulkem ve stylu značky.

        Jako `video` předávej **zdrojové** video, ne hotový Reel — ten už má
        v obraze vypálený handle a na obálce by byl dvakrát.
        """
        from .graphics import GraphicsStudio

        frame = self.cover(video, at=at, name=f"{name or Path(video).stem}-frame")
        studio = GraphicsStudio(self.brand, self.out_dir)
        return studio.reel_cover(title, subtitle, background=str(frame),
                                 name=name or f"{Path(video).stem}-cover")

    # ------------------------------------------------------------ fotky → Reel
    def from_photos(self, photos, seconds_each=2.6, music=None, hook=None,
                    transition=0.4, fit="crop", name=None):
        """Reel z fotek s pomalým nájezdem (Ken Burns) a prolínačkou.

        `fit="crop"` vyplní celý formát (u fotek vypadá líp, ale ořízne okraje),
        `fit="blur"` zachová celý obrázek a doplní rozmazané pozadí — to je
        volba pro grafiku s textem, kde se nesmí nic uříznout.
        """
        photos = [Path(p) for p in photos]
        if not photos:
            raise MediaError("Žádné fotky na vstupu.")
        frames = int(seconds_each * REEL_FPS)
        inputs, filters, labels = [], [], []
        for index, photo in enumerate(photos):
            inputs += ["-loop", "1", "-t", f"{seconds_each:.2f}", "-i", str(photo)]
            zoom_in = index % 2 == 0
            zoom = ("min(1+0.0009*on,1.18)" if zoom_in else "max(1.18-0.0009*on,1)")
            if fit == "blur":
                filters.append(
                    f"[{index}:v]scale={REEL_W * 2}:{REEL_H * 2}:"
                    f"force_original_aspect_ratio=increase,crop={REEL_W * 2}:{REEL_H * 2},"
                    f"gblur=sigma=40,eq=brightness=-0.12,"
                    f"zoompan=z='{zoom}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"s={REEL_W}x{REEL_H}:fps={REEL_FPS},setsar=1,format=yuv420p[bgp{index}]")
                filters.append(
                    f"[{index}:v]scale={REEL_W}:{REEL_H}:"
                    f"force_original_aspect_ratio=decrease,fps={REEL_FPS},setsar=1[fgp{index}]")
                filters.append(
                    f"[bgp{index}][fgp{index}]overlay=(W-w)/2:(H-h)/2:shortest=1,"
                    f"format=yuv420p[p{index}]")
            else:
                filters.append(
                    f"[{index}:v]scale={REEL_W * 2}:{REEL_H * 2}:"
                    f"force_original_aspect_ratio=increase,crop={REEL_W * 2}:{REEL_H * 2},"
                    f"zoompan=z='{zoom}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"s={REEL_W}x{REEL_H}:fps={REEL_FPS},setsar=1,format=yuv420p[p{index}]")
            labels.append(f"[p{index}]")

        if len(labels) == 1:
            filters.append(f"{labels[0]}null[vcat]")
        else:
            current = labels[0]
            offset = seconds_each - transition
            for index in range(1, len(labels)):
                out_label = f"[x{index}]" if index < len(labels) - 1 else "[vcat]"
                filters.append(
                    f"{current}{labels[index]}xfade=transition=fade:"
                    f"duration={transition}:offset={offset:.2f}{out_label}")
                current = out_label
                offset += seconds_each - transition

        total = len(photos) * seconds_each - (len(photos) - 1) * transition
        vstream = "[vcat]"
        next_input = len(photos)
        if music and Path(music).exists():
            inputs += ["-stream_loop", "-1", "-i", str(music)]
            filters.append(f"[{next_input}:a]volume=0.5,atrim=0:{total:.2f},"
                           f"afade=t=out:st={max(0.0, total - 1.2):.2f}:d=1.2[aout]")
            next_input += 1
        else:
            inputs += ["-f", "lavfi", "-t", f"{total:.2f}",
                       "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            filters.append(f"[{next_input}:a]anull[aout]")
            next_input += 1

        specs = self._overlay_specs(hook=hook, watermark=True, total=total)
        ov_inputs, ov_filters, vstream = self._overlay_filters(vstream, specs, next_input)
        inputs += ov_inputs
        filters += ov_filters
        next_input += len(specs)

        out = self.out_dir / f"{slugify(name or 'photo-reel')}-reel.mp4"
        ff.run([*inputs, "-filter_complex", ";".join(filters),
                "-map", vstream, "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                "-r", str(REEL_FPS), "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)])
        log.info("Reel z %d fotek hotov: %s", len(photos), out)
        return out


# ------------------------------------------------------------------ pomocné

def _fit_chain(fit="crop"):
    """Jednoduchý řetězec pro 9:16 (pro -vf, kde je jen jeden stream)."""
    if fit == "blur":
        # v -vf kontextu nemáme pojmenované větve, takže vrátíme ořez
        return (f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
                f"crop={REEL_W}:{REEL_H}")
    return (f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
            f"crop={REEL_W}:{REEL_H}")


def _input_video_chain(index, fit="crop"):
    """Filtry, které z `[index:v]` udělají `[vindex]` ve formátu 9:16."""
    tail = f"fps={REEL_FPS},format=yuv420p,setsar=1"
    if fit == "blur":
        return [
            f"[{index}:v]split[bg{index}][fg{index}]",
            f"[bg{index}]scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
            f"crop={REEL_W}:{REEL_H},gblur=sigma=28,eq=brightness=-0.08[bgb{index}]",
            f"[fg{index}]scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=decrease[fgs{index}]",
            f"[bgb{index}][fgs{index}]overlay=(W-w)/2:(H-h)/2,{tail}[v{index}]",
        ]
    return [f"[{index}:v]scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
            f"crop={REEL_W}:{REEL_H},{tail}[v{index}]"]


def _atempo_chain(speed):
    """atempo umí 0.5–2.0; větší změny se skládají za sebe."""
    remaining = speed
    parts = []
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.4f}")
    return ",".join(parts)


def _snap_to_scene(time_point, scenes, tolerance=0.6):
    if not scenes:
        return time_point
    nearest = min(scenes, key=lambda s: abs(s - time_point))
    return nearest if abs(nearest - time_point) <= tolerance else time_point


def _truncate_clips(clips, budget):
    out, total = [], 0.0
    for start, duration in clips:
        if total >= budget:
            break
        take = min(duration, budget - total)
        if take > 0.3:
            out.append((start, round(take, 2)))
            total += take
    return out


def _wrap(text, width):
    words, lines, current = str(text).split(), [], ""
    for word in words:
        probe = f"{current} {word}".strip()
        if len(probe) <= width or not current:
            current = probe
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def srt_from_cues(cues):
    """Titulky do .srt (když si je chceš otevřít v editoru)."""
    def stamp(seconds):
        millis = int(round((seconds - math.floor(seconds)) * 1000))
        total = int(math.floor(seconds))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d},{millis:03d}"

    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(f"{index}\n{stamp(float(cue['start']))} --> {stamp(float(cue['end']))}\n"
                      f"{cue.get('text', '')}\n")
    return "\n".join(blocks)
