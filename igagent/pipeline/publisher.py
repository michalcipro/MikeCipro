"""Publikace položek z fronty + zápis do paměti agenta."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ..errors import GraphAPIError, PublishBlocked
from ..instagram import Publisher
from ..util import get_logger, local_tz, parse_iso, to_iso, utcnow

log = get_logger(__name__)


class QueuePublisher:
    def __init__(self, store, settings, client, dry_run=False):
        self.store = store
        self.settings = settings
        self.client = client
        self.dry_run = dry_run
        self.publisher = Publisher(client, settings, dry_run=dry_run)
        self.tz = local_tz(settings.timezone)

    # ------------------------------------------------------------ pojistky
    def _guard(self, item):
        if self.settings.autopilot == "off" and not self.dry_run:
            raise PublishBlocked(
                "AUTOPILOT=off — agent nepublikuje. Přepni na `review` nebo `full`, "
                "nebo publikuj ručně: `igagent publish --id N --force`.")
        if item.status not in ("approved", "produced"):
            raise PublishBlocked(f"#{item.id} má stav '{item.status}', nelze publikovat.")
        if item.status == "produced" and self.settings.autopilot != "full":
            raise PublishBlocked(
                f"#{item.id} čeká na schválení: `igagent queue approve {item.id}`.")

        since = to_iso(utcnow() - dt.timedelta(hours=24))
        published_today = self.store.published_count_since(since)
        if published_today >= self.settings.max_posts_per_day:
            raise PublishBlocked(
                f"Denní limit agenta vyčerpán ({published_today}/"
                f"{self.settings.max_posts_per_day} za 24 h).")

        files = (item.assets or {}).get("files") or []
        if not files:
            raise PublishBlocked(f"#{item.id} nemá vyrobená média — spusť `igagent produce`.")
        missing = [f for f in files if not Path(f).exists()]
        if missing:
            raise PublishBlocked(f"Chybí soubory: {missing}")

    # ------------------------------------------------------------ publikace
    def publish_item(self, item, force=False):
        if not force:
            self._guard(item)
        files = (item.assets or {}).get("files") or []
        cover = (item.assets or {}).get("cover")

        try:
            result = self.publisher.publish(
                item.format, files, caption=item.caption, hashtags=item.hashtags,
                alt_text=item.alt_text,
                cover_path=cover if item.format == "REEL" else None)
        except (GraphAPIError, PublishBlocked) as exc:
            item.status = "failed"
            item.error = str(exc)
            item.attempts += 1
            self.store.update_queue(item)
            self.store.log_event("publish_failed", item.id, {"error": str(exc)})
            log.error("Publikace #%s selhala: %s", item.id, exc)
            raise

        if result.dry_run:
            log.info("[dry-run] #%s by se publikoval (%s, %d souborů).",
                     item.id, item.format, len(files))
            return result

        item.status = "published"
        item.media_id = result.media_id
        item.error = None
        self.store.update_queue(item)

        if item.first_comment:
            try:
                self.publisher.first_comment(result.media_id, item.first_comment)
            except GraphAPIError as exc:
                log.warning("První komentář se nepovedl: %s", exc)

        self._remember(item, result)
        log.info("Publikováno #%s → %s", item.id, result.permalink or result.media_id)
        return result

    def _remember(self, item, result):
        """Zapíše příspěvek i s jeho vlastnostmi — z toho se pak agent učí."""
        now = utcnow().astimezone(self.tz)
        self.store.upsert_post({
            "media_id": result.media_id,
            "queue_id": item.id,
            "format": item.format,
            "product_type": "REELS" if item.format == "REEL" else "FEED",
            "permalink": result.permalink,
            "caption": item.caption,
            "published_at": to_iso(utcnow()),
            "local_hour": now.hour,
            "local_weekday": now.weekday(),
            "topic": item.topic,
            "pillar": item.pillar,
            "hook_style": item.hook_style,
            "cta_type": item.cta_type,
            "template": item.template,
            "caption_len": len(item.caption or ""),
            "hashtag_count": len(item.hashtags or []),
            "hashtags": item.hashtags,
            "children_count": len((item.assets or {}).get("files") or []),
            "created_by": "agent",
            "meta": {"queue_id": item.id, "assets": (item.assets or {}).get("files")},
        })
        self.store.log_event("published", item.id,
                             {"media_id": result.media_id, "permalink": result.permalink})

    # ------------------------------------------------------------ dávka
    def publish_due(self, grace_minutes=20, limit=3):
        """Publikuje vše, co má naplánovaný čas v minulosti (s malou tolerancí)."""
        due_before = to_iso(utcnow() + dt.timedelta(minutes=grace_minutes))
        statuses = ("approved", "produced") if self.settings.autopilot == "full" else ("approved",)
        candidates = self.store.queue(status=statuses, due_before=due_before, limit=limit * 3)

        results = []
        for item in candidates[:limit]:
            scheduled = parse_iso(item.scheduled_for) if item.scheduled_for else None
            if scheduled and scheduled > utcnow() + dt.timedelta(minutes=grace_minutes):
                continue
            try:
                results.append(self.publish_item(item))
            except PublishBlocked as exc:
                log.info("Přeskakuji #%s: %s", item.id, exc)
            except GraphAPIError:
                continue
        return results
