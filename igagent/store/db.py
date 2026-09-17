"""SQLite perzistence — jediné místo, kde agent drží paměť."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..util import get_logger, utcnow, to_iso

log = get_logger(__name__)
SCHEMA = Path(__file__).with_name("schema.sql")

QUEUE_STATUSES = ("planned", "produced", "approved", "published", "failed", "skipped")


def _json(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dump(value):
    return None if value is None else json.dumps(value, ensure_ascii=False)


@dataclass
class QueueItem:
    id: int = None
    status: str = "planned"
    format: str = "IMAGE"
    scheduled_for: str = None
    title: str = ""
    topic: str = ""
    pillar: str = ""
    hook_style: str = ""
    cta_type: str = ""
    template: str = ""
    brief: dict = None
    assets: dict = None
    caption: str = ""
    first_comment: str = ""
    alt_text: str = ""
    hashtags: list = None
    source_media: list = None
    media_id: str = None
    error: str = None
    attempts: int = 0
    created_at: str = None
    updated_at: str = None

    @classmethod
    def from_row(cls, row):
        data = dict(row)
        data["brief"] = _json(data.get("brief"), {})
        data["assets"] = _json(data.get("assets"), {})
        data["hashtags"] = _json(data.get("hashtags"), [])
        data["source_media"] = _json(data.get("source_media"), [])
        return cls(**data)

    def to_row(self):
        data = dict(self.__dict__)
        for key in ("brief", "assets", "hashtags", "source_media"):
            data[key] = _dump(data.get(key))
        return data

    @property
    def features(self):
        """Vlastnosti, které se učící smyčka snaží optimalizovat."""
        return {
            "format": self.format,
            "topic": self.topic,
            "pillar": self.pillar,
            "hook_style": self.hook_style,
            "cta_type": self.cta_type,
            "template": self.template,
        }


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        self.conn.commit()

    # ------------------------------------------------------------ obecné
    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def log_event(self, kind, ref=None, payload=None):
        self.conn.execute(
            "INSERT INTO events (ts, kind, ref, payload) VALUES (?,?,?,?)",
            (to_iso(utcnow()), kind, str(ref) if ref is not None else None, _dump(payload)))
        self.conn.commit()

    def recent_events(self, limit=50):
        rows = self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ účet
    def save_account_snapshot(self, day, data):
        self.conn.execute(
            """INSERT INTO account_snapshots
               (day, followers, follows, media_count, reach, profile_views,
                accounts_engaged, total_interactions, website_clicks, raw, collected_at)
               VALUES (:day,:followers,:follows,:media_count,:reach,:profile_views,
                       :accounts_engaged,:total_interactions,:website_clicks,:raw,:collected_at)
               ON CONFLICT(day) DO UPDATE SET
                 followers=excluded.followers, follows=excluded.follows,
                 media_count=excluded.media_count, reach=excluded.reach,
                 profile_views=excluded.profile_views,
                 accounts_engaged=excluded.accounts_engaged,
                 total_interactions=excluded.total_interactions,
                 website_clicks=excluded.website_clicks,
                 raw=excluded.raw, collected_at=excluded.collected_at""",
            {"day": day, "raw": _dump(data.get("raw")), "collected_at": to_iso(utcnow()),
             **{k: data.get(k) for k in ("followers", "follows", "media_count", "reach",
                                         "profile_views", "accounts_engaged",
                                         "total_interactions", "website_clicks")}})
        self.conn.commit()

    def account_snapshots(self, limit=90):
        rows = self.conn.execute(
            "SELECT * FROM account_snapshots ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def latest_followers(self):
        row = self.conn.execute(
            "SELECT followers FROM account_snapshots WHERE followers IS NOT NULL "
            "ORDER BY day DESC LIMIT 1").fetchone()
        return row["followers"] if row else None

    # ------------------------------------------------------------ příspěvky
    def upsert_post(self, post):
        cols = ("media_id", "queue_id", "format", "product_type", "permalink", "caption",
                "published_at", "local_hour", "local_weekday", "topic", "pillar",
                "hook_style", "cta_type", "template", "caption_len", "hashtag_count",
                "hashtags", "children_count", "created_by", "meta")
        data = {c: post.get(c) for c in cols}
        data["hashtags"] = _dump(post.get("hashtags"))
        data["meta"] = _dump(post.get("meta"))
        placeholders = ",".join(f":{c}" for c in cols)
        updates = ",".join(
            f"{c}=COALESCE(excluded.{c}, posts.{c})" for c in cols if c != "media_id")
        self.conn.execute(
            f"INSERT INTO posts ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(media_id) DO UPDATE SET {updates}", data)
        self.conn.commit()

    def get_post(self, media_id):
        row = self.conn.execute("SELECT * FROM posts WHERE media_id=?", (media_id,)).fetchone()
        return dict(row) if row else None

    def posts(self, limit=200, since=None):
        sql = "SELECT * FROM posts"
        params = []
        if since:
            sql += " WHERE published_at >= ?"
            params.append(since)
        sql += " ORDER BY published_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def posts_needing_metrics(self, min_age_hours=24, max_age_days=14):
        """Příspěvky, které si zaslouží (další) změření."""
        rows = self.conn.execute(
            """SELECT p.*, (SELECT MAX(collected_at) FROM post_metrics m
                            WHERE m.media_id = p.media_id) AS last_metric
               FROM posts p
               WHERE p.published_at IS NOT NULL
                 AND julianday('now') - julianday(p.published_at) BETWEEN ? AND ?
               ORDER BY p.published_at DESC""",
            (min_age_hours / 24.0, max_age_days)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ metriky
    def add_metrics(self, media_id, metrics):
        cols = ("media_id", "collected_at", "age_hours", "reach", "likes", "comments",
                "saves", "shares", "total_interactions", "plays", "avg_watch_time",
                "follows", "profile_visits", "score", "raw")
        data = {c: metrics.get(c) for c in cols}
        data["media_id"] = media_id
        data["collected_at"] = metrics.get("collected_at") or to_iso(utcnow())
        data["raw"] = _dump(metrics.get("raw"))
        self.conn.execute(
            f"INSERT INTO post_metrics ({','.join(cols)}) "
            f"VALUES ({','.join(f':{c}' for c in cols)})", data)
        self.conn.commit()

    def latest_metrics(self, media_id):
        row = self.conn.execute(
            "SELECT * FROM post_metrics WHERE media_id=? ORDER BY collected_at DESC LIMIT 1",
            (media_id,)).fetchone()
        return dict(row) if row else None

    def posts_with_latest_metrics(self, limit=300):
        rows = self.conn.execute(
            """SELECT p.*, m.reach, m.likes, m.comments, m.saves, m.shares,
                      m.total_interactions, m.plays, m.avg_watch_time, m.follows,
                      m.score, m.collected_at AS metrics_at, m.age_hours
               FROM posts p
               LEFT JOIN post_metrics m ON m.id = (
                    SELECT id FROM post_metrics x WHERE x.media_id = p.media_id
                    ORDER BY x.collected_at DESC LIMIT 1)
               WHERE p.published_at IS NOT NULL
               ORDER BY p.published_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_metric_score(self, metric_id, score):
        self.conn.execute("UPDATE post_metrics SET score=? WHERE id=?", (score, metric_id))
        self.conn.commit()

    # ------------------------------------------------------------ učení
    def reset_feature_stats(self):
        self.conn.execute("DELETE FROM feature_stats")
        self.conn.commit()

    def bump_feature(self, feature, value, score):
        if value in (None, ""):
            return
        self.conn.execute(
            """INSERT INTO feature_stats (feature, value, n, score_sum, score_sq, last_seen)
               VALUES (?,?,1,?,?,?)
               ON CONFLICT(feature, value) DO UPDATE SET
                 n = n + 1, score_sum = score_sum + excluded.score_sum,
                 score_sq = score_sq + excluded.score_sq, last_seen = excluded.last_seen""",
            (feature, str(value), float(score), float(score) ** 2, to_iso(utcnow())))
        self.conn.commit()

    def feature_stats(self, feature=None):
        sql = "SELECT * FROM feature_stats"
        params = []
        if feature:
            sql += " WHERE feature=?"
            params.append(feature)
        sql += " ORDER BY feature, n DESC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def save_strategy(self, profile, sample_size):
        cur = self.conn.execute(
            "INSERT INTO strategy (created_at, sample_size, profile) VALUES (?,?,?)",
            (to_iso(utcnow()), sample_size, _dump(profile)))
        self.conn.commit()
        return cur.lastrowid

    def latest_strategy(self):
        row = self.conn.execute(
            "SELECT * FROM strategy ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        data = dict(row)
        data["profile"] = _json(data["profile"], {})
        return data

    # ------------------------------------------------------------ fronta
    def enqueue(self, item: QueueItem):
        now = to_iso(utcnow())
        item.created_at = item.created_at or now
        item.updated_at = now
        row = item.to_row()
        row.pop("id", None)
        cols = list(row)
        cur = self.conn.execute(
            f"INSERT INTO queue ({','.join(cols)}) VALUES ({','.join(f':{c}' for c in cols)})", row)
        self.conn.commit()
        item.id = cur.lastrowid
        return item

    def update_queue(self, item: QueueItem):
        item.updated_at = to_iso(utcnow())
        row = item.to_row()
        cols = [c for c in row if c != "id"]
        self.conn.execute(
            f"UPDATE queue SET {','.join(f'{c}=:{c}' for c in cols)} WHERE id=:id", row)
        self.conn.commit()
        return item

    def get_queue_item(self, item_id):
        row = self.conn.execute("SELECT * FROM queue WHERE id=?", (item_id,)).fetchone()
        return QueueItem.from_row(row) if row else None

    def queue(self, status=None, limit=100, due_before=None):
        sql = "SELECT * FROM queue WHERE 1=1"
        params = []
        if status:
            statuses = [status] if isinstance(status, str) else list(status)
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        if due_before:
            sql += " AND (scheduled_for IS NULL OR scheduled_for <= ?)"
            params.append(due_before)
        sql += " ORDER BY COALESCE(scheduled_for, created_at) ASC LIMIT ?"
        params.append(limit)
        return [QueueItem.from_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def scheduled_times(self, since=None):
        sql = "SELECT scheduled_for FROM queue WHERE scheduled_for IS NOT NULL AND status != 'skipped'"
        params = []
        if since:
            sql += " AND scheduled_for >= ?"
            params.append(since)
        return [r["scheduled_for"] for r in self.conn.execute(sql, params).fetchall()]

    def published_count_since(self, iso_ts):
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM posts WHERE published_at >= ?", (iso_ts,)).fetchone()
        return row["c"]

    # ------------------------------------------------------------ komentáře
    def upsert_comment(self, comment):
        self.conn.execute(
            """INSERT INTO comments (comment_id, media_id, username, text, created_at, like_count)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(comment_id) DO UPDATE SET
                 like_count=excluded.like_count, text=excluded.text""",
            (comment.get("id"), comment.get("media_id"), comment.get("username"),
             comment.get("text"), comment.get("timestamp"), comment.get("like_count")))
        self.conn.commit()

    def unreplied_comments(self, limit=50):
        rows = self.conn.execute(
            "SELECT * FROM comments WHERE replied=0 ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def mark_replied(self, comment_id, reply_text):
        self.conn.execute("UPDATE comments SET replied=1, reply_text=? WHERE comment_id=?",
                          (reply_text, comment_id))
        self.conn.commit()

    def recent_comment_texts(self, limit=200):
        rows = self.conn.execute(
            "SELECT text FROM comments ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [r["text"] for r in rows if r["text"]]
