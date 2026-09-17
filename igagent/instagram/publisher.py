"""Vysokoúrovňová publikace: fotka, karusel, Reel, story.

Postup je u všech typů stejný:
    1. médium se zpřístupní na veřejné URL (MediaHost)
    2. vytvoří se container
    3. u videa se počká, až ho Instagram zpracuje
    4. container se publikuje
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..errors import MediaError, PublishBlocked
from ..util import get_logger
from .hosting import build_host

log = get_logger(__name__)

MAX_CAPTION = 2200
MAX_HASHTAGS = 30
MAX_CAROUSEL = 10


@dataclass
class PublishResult:
    media_id: str = None
    permalink: str = None
    container_id: str = None
    format: str = ""
    dry_run: bool = False
    urls: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)


class Publisher:
    def __init__(self, client, settings, host=None, dry_run=False):
        self.client = client
        self.settings = settings
        self.dry_run = dry_run
        self._host = host

    @property
    def host(self):
        """Hosting se staví až při první publikaci.

        Díky tomu nepotřebují příkazy jako `strategy` nebo `queue list`
        nastavený MEDIA_PUBLIC_BASE — ten je potřeba, až když se něco odesílá.
        """
        if self._host is None:
            self._host = build_host(self.settings, dry_run=self.dry_run)
        return self._host

    # ------------------------------------------------------------ pomocné
    @staticmethod
    def prepare_caption(caption, hashtags=None):
        """Složí popisek + hashtagy a ohlídá limity Instagramu."""
        text = (caption or "").strip()
        tags = []
        for tag in hashtags or []:
            tag = str(tag).strip()
            if not tag:
                continue
            tag = tag if tag.startswith("#") else f"#{tag}"
            if tag.lower() not in [t.lower() for t in tags] and tag.lower() not in text.lower():
                tags.append(tag)
        tags = tags[:MAX_HASHTAGS]
        if tags:
            candidate = f"{text}\n\n{' '.join(tags)}"
            # když se hashtagy nevejdou, ořízneme jejich počet, ne text
            while len(candidate) > MAX_CAPTION and tags:
                tags.pop()
                candidate = f"{text}\n\n{' '.join(tags)}" if tags else text
            text = candidate
        if len(text) > MAX_CAPTION:
            log.warning("Popisek má %d znaků, zkracuji na %d.", len(text), MAX_CAPTION)
            text = text[:MAX_CAPTION - 1].rsplit(" ", 1)[0] + "…"
        return text

    def _check_quota(self):
        if self.dry_run:
            return
        try:
            limit = self.client.publishing_limit()
        except Exception as exc:  # noqa: BLE001 - kvóta je jen pojistka
            log.debug("Kvótu publikací nelze zjistit (%s), pokračuji.", exc)
            return
        used, total = limit.get("used"), limit.get("total") or 50
        if used is not None and used >= total:
            raise PublishBlocked(
                f"Vyčerpaný denní limit Instagramu ({used}/{total} příspěvků za 24 h).")
        log.info("Kvóta publikací: %s/%s za 24 h.", used, total)

    def _upload(self, path):
        url = self.host.publish(path)
        return url

    def _finish(self, container_id, fmt, urls, wait=False):
        if self.dry_run:
            log.info("[dry-run] Kontejner by se publikoval (%s).", fmt)
            return PublishResult(container_id=container_id, format=fmt, dry_run=True, urls=urls)
        if wait:
            self.client.wait_for_container(container_id)
        media_id = self.client.publish_container(container_id)
        detail = {}
        try:
            detail = self.client.media_details(media_id)
        except Exception as exc:  # noqa: BLE001 - detail je bonus
            log.debug("Detail média %s nelze načíst: %s", media_id, exc)
        return PublishResult(media_id=media_id, permalink=detail.get("permalink"),
                             container_id=container_id, format=fmt, urls=urls, detail=detail)

    # ------------------------------------------------------------ fotka
    def publish_image(self, path, caption="", hashtags=None, alt_text=None,
                      location_id=None, user_tags=None):
        self._check_quota()
        url = self._upload(path)
        params = {"image_url": url, "caption": self.prepare_caption(caption, hashtags)}
        if alt_text:
            params["alt_text"] = alt_text[:1000]
        if location_id:
            params["location_id"] = location_id
        if user_tags:
            params["user_tags"] = _json_param(user_tags)
        if self.dry_run:
            log.info("[dry-run] IMAGE container: %s", {k: v for k, v in params.items()})
            return PublishResult(format="IMAGE", dry_run=True, urls=[url], detail=params)
        container = self.client.create_container(**params)
        return self._finish(container, "IMAGE", [url])

    # ------------------------------------------------------------ karusel
    def publish_carousel(self, paths, caption="", hashtags=None, alt_texts=None):
        paths = list(paths)
        if not 2 <= len(paths) <= MAX_CAROUSEL:
            raise MediaError(f"Karusel musí mít 2–{MAX_CAROUSEL} položek, dostal jsem {len(paths)}.")
        self._check_quota()
        urls, children = [], []
        for index, path in enumerate(paths):
            url = self._upload(path)
            urls.append(url)
            is_video = Path(path).suffix.lower() in (".mp4", ".mov")
            params = {"is_carousel_item": "true"}
            params["video_url" if is_video else "image_url"] = url
            if is_video:
                params["media_type"] = "VIDEO"
            if alt_texts and index < len(alt_texts) and alt_texts[index]:
                params["alt_text"] = alt_texts[index][:1000]
            if self.dry_run:
                children.append(f"dry-child-{index}")
                continue
            child = self.client.create_container(**params)
            if is_video:
                self.client.wait_for_container(child)
            children.append(child)

        caption_text = self.prepare_caption(caption, hashtags)
        if self.dry_run:
            log.info("[dry-run] CAROUSEL z %d položek.", len(children))
            return PublishResult(format="CAROUSEL", dry_run=True, urls=urls,
                                 detail={"children": children, "caption": caption_text})
        container = self.client.create_container(
            media_type="CAROUSEL", children=",".join(children), caption=caption_text)
        return self._finish(container, "CAROUSEL", urls)

    # ------------------------------------------------------------ reel
    def publish_reel(self, path, caption="", hashtags=None, cover_path=None,
                     share_to_feed=True, audio_name=None, thumb_offset_ms=None,
                     collaborators=None):
        self._check_quota()
        video_url = self._upload(path)
        urls = [video_url]
        params = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": self.prepare_caption(caption, hashtags),
            "share_to_feed": "true" if share_to_feed else "false",
        }
        if cover_path:
            cover_url = self._upload(cover_path)
            urls.append(cover_url)
            params["cover_url"] = cover_url
        elif thumb_offset_ms is not None:
            params["thumb_offset"] = int(thumb_offset_ms)
        if audio_name:
            params["audio_name"] = audio_name
        if collaborators:
            params["collaborators"] = _json_param(list(collaborators))
        if self.dry_run:
            log.info("[dry-run] REELS container: %s", params)
            return PublishResult(format="REEL", dry_run=True, urls=urls, detail=params)
        container = self.client.create_container(**params)
        return self._finish(container, "REEL", urls, wait=True)

    # ------------------------------------------------------------ story
    def publish_story(self, path, is_video=None):
        self._check_quota()
        url = self._upload(path)
        is_video = is_video if is_video is not None else Path(path).suffix.lower() in (".mp4", ".mov")
        params = {"media_type": "STORIES"}
        params["video_url" if is_video else "image_url"] = url
        if self.dry_run:
            log.info("[dry-run] STORIES container: %s", params)
            return PublishResult(format="STORY", dry_run=True, urls=[url], detail=params)
        container = self.client.create_container(**params)
        return self._finish(container, "STORY", [url], wait=is_video)

    # ------------------------------------------------------------ router
    def publish(self, fmt, assets, caption="", hashtags=None, alt_text=None, **kwargs):
        """Publikuje podle formátu; `assets` je seznam cest."""
        assets = [assets] if isinstance(assets, (str, Path)) else list(assets)
        fmt = (fmt or "").upper()
        if fmt == "REEL":
            return self.publish_reel(assets[0], caption, hashtags,
                                     cover_path=kwargs.get("cover_path"), **{
                                         k: v for k, v in kwargs.items() if k != "cover_path"})
        if fmt == "CAROUSEL":
            return self.publish_carousel(assets, caption, hashtags,
                                         alt_texts=kwargs.get("alt_texts"))
        if fmt == "STORY":
            return self.publish_story(assets[0])
        return self.publish_image(assets[0], caption, hashtags, alt_text=alt_text,
                                  location_id=kwargs.get("location_id"),
                                  user_tags=kwargs.get("user_tags"))

    def first_comment(self, media_id, text):
        """Hashtagy nebo odkaz jde schovat do prvního komentáře."""
        if not text:
            return None
        if self.dry_run:
            log.info("[dry-run] První komentář: %s", text[:80])
            return None
        return self.client.post(f"{media_id}/comments", message=text[:MAX_CAPTION])


def _json_param(value):
    import json

    return json.dumps(value, ensure_ascii=False)
