"""Napodobeniny Instagramu a Claude — testy nesmí sahat na síť."""

from __future__ import annotations

import datetime as dt
import itertools
import random


class FakeGraphClient:
    """Simuluje Graph API včetně dvoufázové publikace a insightů.

    Dosah generuje podle „skrytých pravidel": Reels a hodina 18 fungují líp.
    Učící smyčka to má objevit sama.
    """

    HIDDEN_RULES = {"format": {"REEL": 2.0, "CAROUSEL": 1.2, "IMAGE": 0.7},
                    "hour": {18: 1.6, 12: 1.0, 9: 0.6}}

    def __init__(self, followers=2000, seed=1):
        self.followers = followers
        self.random = random.Random(seed)
        self.counter = itertools.count(1)
        self.media_store = {}
        self.containers = {}
        self.published = []
        self.comments_store = {}
        self.calls = []

    # ------------------------------------------------------------ účet
    def account(self, fields=None):
        self.calls.append("account")
        return {"id": "17841400000000000", "username": "testbrand",
                "followers_count": self.followers, "follows_count": 300,
                "media_count": len(self.media_store)}

    def account_insights(self, **kwargs):
        return {"reach": 5000, "profile_views": 120, "accounts_engaged": 400,
                "total_interactions": 900}

    def follower_demographics(self, breakdown="city"):
        return {"Praha": 400, "Brno": 150}

    # ------------------------------------------------------------ média
    def media(self, limit=25, max_pages=4, fields=None):  # noqa: A003 - stejné jméno jako v API
        return list(self.media_store.values())[:limit]

    def media_details(self, media_id, fields=None):
        entry = self.media_store.get(media_id, {})
        return {"id": media_id, "permalink": f"https://instagram.com/p/{media_id}",
                "like_count": entry.get("like_count", 0),
                "comments_count": entry.get("comments_count", 0),
                **entry}

    def media_insights(self, media_id, media_type="IMAGE", product_type=None):
        entry = self.media_store.get(media_id, {})
        return {"reach": entry.get("reach", 0), "likes": entry.get("like_count", 0),
                "comments": entry.get("comments_count", 0), "saved": entry.get("saves", 0),
                "shares": entry.get("shares", 0)}

    # ------------------------------------------------------------ publikace
    def create_container(self, **params):
        container_id = f"c{next(self.counter)}"
        self.containers[container_id] = params
        return container_id

    def container_status(self, container_id):
        return {"status_code": "FINISHED"}

    def wait_for_container(self, container_id, timeout=600, interval=5):
        return {"status_code": "FINISHED"}

    def publish_container(self, container_id):
        params = self.containers[container_id]
        media_id = f"m{next(self.counter)}"
        fmt = "REEL" if params.get("media_type") == "REELS" else (
            "CAROUSEL" if params.get("media_type") == "CAROUSEL" else "IMAGE")
        self.media_store[media_id] = {
            "id": media_id, "media_type": "VIDEO" if fmt == "REEL" else "IMAGE",
            "media_product_type": "REELS" if fmt == "REEL" else "FEED",
            "caption": params.get("caption", ""),
            "timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "permalink": f"https://instagram.com/p/{media_id}",
            "_format": fmt,
        }
        self.published.append(media_id)
        return media_id

    def publishing_limit(self):
        return {"used": len(self.published), "total": 50}

    def post(self, path, **data):
        self.calls.append(("post", path, data))
        return {"id": f"x{next(self.counter)}"}

    # ------------------------------------------------------------ komentáře
    def comments(self, media_id, limit=50):
        return self.comments_store.get(media_id, [])

    def reply_to_comment(self, comment_id, message):
        self.calls.append(("reply", comment_id, message))
        return {"id": f"r{next(self.counter)}"}


    def add_manual_post(self, media_id, fmt="IMAGE", caption="ruční příspěvek",
                        hours_ago=48, reach=1000):
        """Příspěvek publikovaný z telefonu — agent o něm zatím neví."""
        when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
        self.media_store[media_id] = {
            "id": media_id,
            "media_type": "VIDEO" if fmt == "REEL" else "IMAGE",
            "media_product_type": "REELS" if fmt == "REEL" else "FEED",
            "caption": caption,
            "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "permalink": f"https://instagram.com/p/{media_id}",
            "reach": reach, "like_count": int(reach * 0.05), "comments_count": 2,
            "saves": int(reach * 0.02), "shares": 3,
        }
        return media_id

    # ------------------------------------------------------------ simulace
    def simulate_performance(self, media_id, fmt, hour):
        """Naplní metriky podle skrytých pravidel + šumu."""
        multiplier = (self.HIDDEN_RULES["format"].get(fmt, 1.0)
                      * self.HIDDEN_RULES["hour"].get(hour, 1.0))
        noise = self.random.uniform(0.75, 1.3)
        reach = int(self.followers * 0.35 * multiplier * noise)
        entry = self.media_store.setdefault(media_id, {})
        entry.update({
            "reach": reach,
            "like_count": int(reach * 0.06 * noise),
            "comments_count": int(reach * 0.004),
            "saves": int(reach * 0.02 * multiplier),
            "shares": int(reach * 0.01 * multiplier),
        })
        return entry


class FakeBrain:
    """Vrací deterministické odpovědi ve stejném tvaru jako schémata."""

    def __init__(self, formats=("REEL", "CAROUSEL", "IMAGE")):
        self.formats = list(formats)
        self.calls = []

    def plan_content(self, count, strategy=None, recent_posts=None, calendar_notes=None,
                     available_media=None, slots=None, moments=None):
        self.calls.append(("plan", count, [s.key for _, s in (slots or [])]))
        items = []
        for index in range(count):
            series = slots[index][1] if slots and index < len(slots) else None
            fmt = series.format if series else self.formats[index % len(self.formats)]
            items.append({
                "title": f"Nápad {index + 1}",
                "series": series.key if series else "",
                "format": fmt,
                "template": "tip_list" if fmt != "REEL" else "video",
                "topic": "návyky",
                "pillar": "Použitelné návody",
                "hook_style": "cislo",
                "cta_type": "uloz",
                "angle": "Tři věci, co fungují",
                "key_points": ["bod jedna", "bod dva", "bod tři"],
                "needs_user_media": fmt == "REEL",
                "why": "test",
            })
        return {"reasoning": "test plán", "items": items}

    def repurpose(self, winners, count=2, strategy=None, english=False, recent_posts=None):
        self.calls.append(("repurpose", len(winners), english))
        variants = []
        for index, winner in enumerate(winners[:count]):
            variants.append({
                "source_media_id": winner["media_id"],
                "title": f"Znovu: {winner.get('topic') or 'námět'} {index + 1}",
                "series": winner.get("series") or "tvrda_pravda",
                "variant": "anglicky" if (english and index == 0) else "jiny_uhel",
                "language": "en" if (english and index == 0) else "cs",
                "format": "REEL",
                "hook_style": "kontrarian",
                "angle": "jiný pohled na stejnou myšlenku",
                "key_points": ["bod"],
                "why": "fungovalo to",
            })
        return {"variants": variants}

    def write_post(self, item, strategy=None, reference_image=None):
        self.calls.append(("write_post", item.id))
        return {
            "hook": "Tři věci, co fungují",
            "caption": "Tři věci, co fungují\n\nTohle mi zabralo rok zjistit.",
            "hashtags": ["#navyky", "#produktivita"],
            "first_comment": "",
            "alt_text": "Grafika se třemi tipy",
            "graphic": {"kicker": "Návod", "title": "Tři věci, co fungují",
                        "subtitle": "Bez motivace", "body_lines": ["Zmenši to", "Navaž to",
                                                                   "Změř to"],
                        "stat_value": "3", "stat_label": "věci",
                        "outro_headline": "Ulož si to", "outro_cta": "@testbrand"},
            "slides": [{"heading": "Zmenši to", "body": "Začni malým krokem."},
                       {"heading": "Navaž to", "body": "Přilep na existující rutinu."}],
        }

    def write_reel(self, item, video_info=None, strategy=None, transcript=None):
        self.calls.append(("write_reel", item.id))
        return {
            "hook_text": "Tohle si ulož",
            "cover_title": "Tři návyky",
            "beats": [{"text": "První věc", "start": 3.0, "end": 6.0}],
            "caption": "Tři návyky, co fungují.",
            "hashtags": ["#navyky"],
            "target_seconds": 12,
            "notes": "",
        }

    def analyze_profile(self, snapshot, posts, strategy=None, comments=None, period_days=28):
        self.calls.append(("analyze", len(posts)))
        return {
            "summary": "Profil roste pomalu, Reels táhnou.",
            "working": ["Reels"], "not_working": ["statické obrázky"],
            "audience_read": "Publikum reaguje na návody.",
            "experiments": [{"hypothesis": "Reels v 18:00 mají větší dosah",
                             "change": "posunout publikaci", "measure": "dosah za 48 h"}],
            "next_actions": ["Víc Reels"], "confidence": "střední",
        }

    def qa_image(self, image_path, expected_text=None):
        return {"readable": True, "text_overflow": False, "on_brand": True, "score": 8,
                "problems": [], "verdict": "publikovat"}

    def draft_comment_replies(self, comments, strategy=None):
        return {"replies": [{"comment_id": c["comment_id"], "reply": "Díky!",
                             "action": "odpovedet", "reason": "chvála"} for c in comments]}

