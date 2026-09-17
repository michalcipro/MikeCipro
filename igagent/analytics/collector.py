"""Sběr dat z Instagramu do lokální databáze.

Sbíráme i příspěvky, které jsi publikoval ručně z telefonu — agent se učí
z celého profilu, ne jen z toho, co vyrobil sám.
"""

from __future__ import annotations

import datetime as dt
import re

from ..errors import GraphAPIError
from ..util import get_logger, local_tz, parse_iso, to_iso, utcnow
from .metrics import normalize_media_insights

log = get_logger(__name__)

HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)

FORMAT_BY_TYPE = {"IMAGE": "IMAGE", "CAROUSEL_ALBUM": "CAROUSEL", "VIDEO": "REEL"}


class Collector:
    def __init__(self, client, store, settings):
        self.client = client
        self.store = store
        self.settings = settings
        self.tz = local_tz(settings.timezone)

    # ------------------------------------------------------------ účet
    def collect_account(self):
        account = self.client.account()
        insights = self.client.account_insights()
        day = utcnow().astimezone(self.tz).date().isoformat()
        snapshot = {
            "followers": account.get("followers_count"),
            "follows": account.get("follows_count"),
            "media_count": account.get("media_count"),
            "reach": insights.get("reach"),
            "profile_views": insights.get("profile_views"),
            "accounts_engaged": insights.get("accounts_engaged"),
            "total_interactions": insights.get("total_interactions"),
            "website_clicks": insights.get("website_clicks"),
            "raw": {"account": account, "insights": insights},
        }
        self.store.save_account_snapshot(day, snapshot)
        log.info("Profil: %s sledujících, dosah za den %s.",
                 snapshot["followers"], snapshot["reach"])
        return snapshot

    # ------------------------------------------------------------ příspěvky
    def sync_media(self, limit=25, max_pages=4):
        """Stáhne seznam příspěvků a doplní do DB ty, které ještě nemáme."""
        media = self.client.media(limit=limit, max_pages=max_pages)
        added = 0
        for entry in media:
            existing = self.store.get_post(entry["id"])
            record = self._to_post_record(entry, existing)
            self.store.upsert_post(record)
            if not existing:
                added += 1
        log.info("Synchronizováno %d příspěvků (%d nových).", len(media), added)
        return media

    def _to_post_record(self, entry, existing=None):
        published = parse_iso(entry.get("timestamp"))
        local = published.astimezone(self.tz) if published else None
        caption = entry.get("caption") or ""
        hashtags = HASHTAG_RE.findall(caption)
        media_type = (entry.get("media_type") or "").upper()
        product = (entry.get("media_product_type") or "").upper()
        fmt = "REEL" if product == "REELS" else FORMAT_BY_TYPE.get(media_type, media_type)

        record = {
            "media_id": entry["id"],
            "format": fmt,
            "product_type": product or None,
            "permalink": entry.get("permalink"),
            "caption": caption,
            "published_at": to_iso(published),
            "local_hour": local.hour if local else None,
            "local_weekday": local.weekday() if local else None,
            "caption_len": len(caption),
            "hashtag_count": len(hashtags),
            "hashtags": hashtags,
            "children_count": len((entry.get("children") or {}).get("data", [])) or None,
            "meta": {"media_url": entry.get("media_url"),
                     "thumbnail_url": entry.get("thumbnail_url")},
        }
        if not existing:
            # příspěvky, které v DB nemáme, jsme zjevně nepublikovali my
            record["created_by"] = "human"
        return record

    # ------------------------------------------------------------ metriky
    def collect_metrics(self, min_age_hours=None, max_age_days=None):
        """Změří příspěvky, které jsou dost staré na to, aby to mělo smysl."""
        min_age = min_age_hours or self.settings.metrics_collect_after_hours
        max_age = max_age_days or self.settings.metrics_refresh_days
        targets = self.store.posts_needing_metrics(min_age, max_age)
        now = utcnow()
        collected = 0

        for post in targets:
            if _measured_recently(post, now):
                continue
            media_type = {"REEL": "VIDEO", "CAROUSEL": "CAROUSEL_ALBUM"}.get(
                post.get("format"), "IMAGE")
            try:
                raw = self.client.media_insights(post["media_id"], media_type,
                                                 post.get("product_type"))
                detail = self.client.media_details(
                    post["media_id"], fields="like_count,comments_count")
            except GraphAPIError as exc:
                log.warning("Metriky pro %s nedostupné: %s", post["media_id"], exc)
                continue

            metrics = normalize_media_insights(raw, detail)
            published = parse_iso(post.get("published_at"))
            metrics["age_hours"] = round((now - published).total_seconds() / 3600, 2) if published else None
            metrics["collected_at"] = to_iso(now)
            metrics["raw"] = raw
            self.store.add_metrics(post["media_id"], metrics)
            collected += 1

        log.info("Změřeno %d příspěvků.", collected)
        return collected

    # ------------------------------------------------------------ komentáře
    def collect_comments(self, media_limit=12):
        posts = self.store.posts(limit=media_limit)
        total = 0
        for post in posts:
            try:
                comments = self.client.comments(post["media_id"])
            except GraphAPIError as exc:
                log.debug("Komentáře k %s nelze načíst: %s", post["media_id"], exc)
                continue
            for comment in comments:
                comment["media_id"] = post["media_id"]
                self.store.upsert_comment(comment)
                total += 1
        log.info("Uloženo %d komentářů.", total)
        return total

    # ------------------------------------------------------------ vše najednou
    def run(self, with_comments=True):
        result = {}
        result["account"] = self.collect_account()
        result["media"] = len(self.sync_media())
        result["metrics"] = self.collect_metrics()
        if with_comments:
            result["comments"] = self.collect_comments()
        self.store.log_event("collect", payload={k: (v if not isinstance(v, dict) else "ok")
                                                 for k, v in result.items()})
        return result


def _measured_recently(post, now):
    """Čerstvé příspěvky měříme častěji, staré stačí jednou za pár dní."""
    last = post.get("last_metric")
    if not last:
        return False
    age_hours = (now - parse_iso(last)).total_seconds() / 3600
    published = parse_iso(post.get("published_at"))
    post_age_days = (now - published).days if published else 99
    threshold = 12 if post_age_days <= 2 else (24 if post_age_days <= 7 else 72)
    return age_hours < threshold


def days_ago_iso(days):
    return to_iso(utcnow() - dt.timedelta(days=days))
