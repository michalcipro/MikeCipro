"""Výroba: z položky fronty udělá hotové soubory + texty."""

from __future__ import annotations

from pathlib import Path

from ..errors import BrainError, MediaError
from .inbox import Inbox
from ..media.graphics import GraphicsStudio
from ..media.photos import PhotoStudio
from ..media.video import ReelStudio
from ..util import get_logger, slugify

log = get_logger(__name__)

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".avi", ".mkv")


class Producer:
    def __init__(self, store, settings, brain, learner):
        self.store = store
        self.settings = settings
        self.brain = brain
        self.learner = learner
        self.graphics = GraphicsStudio(settings.brand, settings.out_dir)
        self.photos = PhotoStudio(settings.brand, settings.out_dir)
        self.reels = ReelStudio(settings.brand, settings.work_dir, settings.out_dir)
        self.inbox = Inbox(settings, store)

    # ------------------------------------------------------------ vstup
    def produce(self, item, qa=None, archive_source=True):
        """Vyrobí média a texty pro jednu položku. Vrací aktualizovanou položku."""
        qa = self.settings.qa_images if qa is None else qa
        strategy = dict(self.learner.current_profile())
        log.info("Vyrábím #%s [%s/%s] %s", item.id, item.format, item.template, item.title)

        try:
            if item.format == "REEL":
                item = self._produce_reel(item, strategy)
            elif item.format == "CAROUSEL":
                item = self._produce_carousel(item, strategy)
            else:
                item = self._produce_image(item, strategy)
        except (MediaError, BrainError) as exc:
            item.status = "failed"
            item.error = str(exc)
            item.attempts += 1
            self.store.update_queue(item)
            self.store.log_event("produce_failed", item.id, {"error": str(exc)})
            log.error("Výroba #%s selhala: %s", item.id, exc)
            return item

        if qa:
            self._run_qa(item)

        item.status = "approved" if self.settings.autopilot == "full" else "produced"
        item.error = None
        self.store.update_queue(item)
        if item.status != "failed" and archive_source:
            # zdroj je zpracovaný — ať se nenabízí dalšímu námětu
            self.inbox.archive(item.source_media or [])
        self.store.log_event("produced", item.id,
                             {"format": item.format, "assets": item.assets})
        return item

    # ------------------------------------------------------------ obrázek
    def _produce_image(self, item, strategy):
        source = _first_photo(item.source_media)
        content = self.brain.write_post(item, strategy=strategy, reference_image=source)
        graphic = content.get("graphic", {})
        name = f"q{item.id}-{slugify(item.title or item.topic)}"

        if item.template == "photo" and source:
            path = self.photos.prepare(source, ratio="portrait", name=name)
        elif item.template == "stat":
            path = self.graphics.stat(graphic.get("stat_value") or "?",
                                      graphic.get("stat_label") or item.title,
                                      graphic.get("subtitle"), name=name)
        elif item.template == "tip_list":
            path = self.graphics.tip_list(graphic.get("title") or item.title,
                                          graphic.get("body_lines") or [],
                                          kicker=graphic.get("kicker"), name=name)
        elif item.template == "cover":
            path = self.graphics.cover(graphic.get("title") or item.title,
                                       graphic.get("subtitle"),
                                       graphic.get("kicker"), name=name,
                                       background=source)
        else:
            path = self.graphics.quote(graphic.get("title") or item.title,
                                       kicker=graphic.get("kicker"), name=name)

        return self._attach(item, content, [path])

    # ------------------------------------------------------------ karusel
    def _produce_carousel(self, item, strategy):
        photos = [p for p in (item.source_media or []) if _is_photo(p)]
        content = self.brain.write_post(item, strategy=strategy,
                                        reference_image=photos[0] if photos else None)
        graphic = content.get("graphic", {})
        name = f"q{item.id}-{slugify(item.title or item.topic)}"

        if item.template == "photo" and len(photos) >= 2:
            paths = self.photos.prepare_many(photos[:9], ratio="portrait")
            cover = self.graphics.cover(graphic.get("title") or item.title,
                                        graphic.get("subtitle"), graphic.get("kicker"),
                                        name=f"{name}-cover", background=photos[0])
            paths = [cover, *paths]
        else:
            slides = content.get("slides") or [
                {"heading": line, "body": ""} for line in (graphic.get("body_lines") or [])]
            if not slides:
                raise MediaError("Claude nevrátil žádné slidy pro karusel.")
            paths = self.graphics.carousel({
                "cover": {"title": graphic.get("title") or item.title,
                          "subtitle": graphic.get("subtitle"),
                          "kicker": graphic.get("kicker"),
                          "background": photos[0] if photos else None},
                "slides": slides[:8],
                "outro": {"headline": graphic.get("outro_headline") or "Ulož si to",
                          "cta": graphic.get("outro_cta")
                          or f"Víc na @{self.settings.brand.handle}"},
            }, name=name)

        return self._attach(item, content, paths[:10])

    # ------------------------------------------------------------ reel
    def _produce_reel(self, item, strategy):
        videos = [p for p in (item.source_media or []) if _is_video(p)]
        photos = [p for p in (item.source_media or []) if _is_photo(p)]
        name = f"q{item.id}-{slugify(item.title or item.topic)}"

        if videos:
            source = videos[0]
            info = self.reels.probe(source)
            script = self.brain.write_reel(item, video_info=info, strategy=strategy)
            captions = [{"text": beat["text"], "start": beat["start"], "end": beat["end"]}
                        for beat in script.get("beats", [])]
            reel = self.reels.build(
                source,
                target_duration=float(script.get("target_seconds") or 28),
                hook={"text": script.get("hook_text", ""), "end": 2.6},
                captions=captions,
                music=_music_path(self.settings),
                name=name)
            # snímek bereme ze zdroje — hotový Reel už má v obraze handle
            cover = self.reels.branded_cover(source, script.get("cover_title") or item.title,
                                             name=name)
        elif photos:
            script = self.brain.write_reel(item, strategy=strategy)
            reel = self.reels.from_photos(photos[:8], music=_music_path(self.settings),
                                          hook={"text": script.get("hook_text", ""), "end": 2.6},
                                          name=name)
            cover = self.graphics.reel_cover(script.get("cover_title") or item.title,
                                             background=photos[0], name=f"{name}-cover")
        else:
            raise MediaError(
                f"#{item.id} je Reel, ale nemá zdrojové video ani fotky. "
                f"Nasyp soubory do {self.settings.data_dir / 'inbox'} a přiřaď je "
                f"příkazem `igagent queue attach {item.id} <soubor>`.")

        content = {
            "caption": script.get("caption", ""),
            "hashtags": script.get("hashtags", []),
            "first_comment": "",
            "alt_text": script.get("cover_title", ""),
            "hook": script.get("hook_text", ""),
        }
        item = self._attach(item, content, [reel])
        item.assets = {**(item.assets or {}), "cover": str(cover), "script": script,
                       "duration_seconds": round(self.reels.probe(reel)["duration"], 2)}
        return item

    # ------------------------------------------------------------ společné
    def _attach(self, item, content, paths):
        item.caption = content.get("caption", "")
        item.hashtags = content.get("hashtags", [])
        item.first_comment = content.get("first_comment", "")
        item.alt_text = content.get("alt_text", "")
        item.assets = {"files": [str(p) for p in paths],
                       "hook": content.get("hook", ""),
                       "slides": content.get("slides", [])}
        return item

    def _run_qa(self, item):
        """Nechá Claude podívat se na vyrobenou grafiku."""
        files = [f for f in (item.assets or {}).get("files", []) if _is_photo(f)]
        if not files:
            return
        checks = []
        for path in files[:3]:
            try:
                verdict = self.brain.qa_image(path, expected_text=item.caption[:300])
            except BrainError as exc:
                log.debug("QA obrázku selhalo: %s", exc)
                continue
            checks.append({"file": path, **verdict})
            if verdict.get("verdict") != "publikovat":
                log.warning("QA u #%s hlásí problém (%s): %s", item.id,
                            verdict.get("verdict"), "; ".join(verdict.get("problems", [])))
        if checks:
            item.assets = {**(item.assets or {}), "qa": checks}
            worst = min(c.get("score", 10) for c in checks)
            if worst <= 4 or any(c.get("verdict") == "zahodit" for c in checks):
                item.status = "failed"
                item.error = "QA: grafika neprošla kontrolou čitelnosti."


def _is_video(path):
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


def _is_photo(path):
    return Path(path).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".heic")


def _first_photo(paths):
    return next((p for p in (paths or []) if _is_photo(p) and Path(p).exists()), None)


def _music_path(settings):
    """Hudba se bere ze složky `data/music` — a jen ta, ke které máš práva."""
    music_dir = Path(settings.data_dir) / "music"
    if not music_dir.exists():
        return None
    tracks = sorted(p for p in music_dir.iterdir()
                    if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".aac"))
    return str(tracks[0]) if tracks else None
