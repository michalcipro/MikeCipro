PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- denní snímek profilu (followers, dosah, návštěvy profilu…)
CREATE TABLE IF NOT EXISTS account_snapshots (
    day             TEXT PRIMARY KEY,
    followers       INTEGER,
    follows         INTEGER,
    media_count     INTEGER,
    reach           INTEGER,
    profile_views   INTEGER,
    accounts_engaged INTEGER,
    total_interactions INTEGER,
    website_clicks  INTEGER,
    raw             TEXT,
    collected_at    TEXT NOT NULL
);

-- publikované příspěvky + jejich „vlastnosti" (to, co se agent učí)
CREATE TABLE IF NOT EXISTS posts (
    media_id        TEXT PRIMARY KEY,
    queue_id        INTEGER,
    format          TEXT,           -- IMAGE | CAROUSEL | REEL | STORY
    product_type    TEXT,           -- FEED | REELS | STORY | AD
    permalink       TEXT,
    caption         TEXT,
    published_at    TEXT,
    local_hour      INTEGER,
    local_weekday   INTEGER,        -- 0 = pondělí
    topic           TEXT,
    pillar          TEXT,
    hook_style      TEXT,
    cta_type        TEXT,
    template        TEXT,
    caption_len     INTEGER,
    hashtag_count   INTEGER,
    hashtags        TEXT,           -- JSON pole
    children_count  INTEGER,
    created_by      TEXT DEFAULT 'agent',   -- agent | human
    meta            TEXT,           -- JSON
    UNIQUE (media_id)
);

-- časové řady metrik k jednotlivým příspěvkům
CREATE TABLE IF NOT EXISTS post_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id        TEXT NOT NULL REFERENCES posts(media_id) ON DELETE CASCADE,
    collected_at    TEXT NOT NULL,
    age_hours       REAL,
    reach           INTEGER,
    likes           INTEGER,
    comments        INTEGER,
    saves           INTEGER,
    shares          INTEGER,
    total_interactions INTEGER,
    plays           INTEGER,
    avg_watch_time  REAL,
    follows         INTEGER,
    profile_visits  INTEGER,
    score           REAL,
    raw             TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_media ON post_metrics(media_id, collected_at DESC);

-- agregace pro učící smyčku (bandita): jedna řádka = jedna hodnota jedné vlastnosti
CREATE TABLE IF NOT EXISTS feature_stats (
    feature     TEXT NOT NULL,
    value       TEXT NOT NULL,
    n           INTEGER NOT NULL DEFAULT 0,
    score_sum   REAL NOT NULL DEFAULT 0,
    score_sq    REAL NOT NULL DEFAULT 0,
    last_seen   TEXT,
    PRIMARY KEY (feature, value)
);

-- fronta obsahu: od nápadu přes výrobu po publikaci
CREATE TABLE IF NOT EXISTS queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    status          TEXT NOT NULL DEFAULT 'planned',
    -- planned → produced → approved → published | failed | skipped
    format          TEXT NOT NULL,
    scheduled_for   TEXT,
    title           TEXT,
    topic           TEXT,
    pillar          TEXT,
    hook_style      TEXT,
    cta_type        TEXT,
    template        TEXT,
    brief           TEXT,       -- JSON: co má příspěvek říct (od Claude)
    assets          TEXT,       -- JSON: cesty k vyrobeným souborům
    caption         TEXT,
    first_comment   TEXT,
    alt_text        TEXT,
    hashtags        TEXT,       -- JSON pole
    source_media    TEXT,       -- JSON: vstupní fotky/video od uživatele
    media_id        TEXT,       -- vyplní se po publikaci
    error           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status, scheduled_for);

-- komentáře (pro analýzu publika i případné odpovědi)
CREATE TABLE IF NOT EXISTS comments (
    comment_id  TEXT PRIMARY KEY,
    media_id    TEXT,
    username    TEXT,
    text        TEXT,
    created_at  TEXT,
    like_count  INTEGER,
    replied     INTEGER NOT NULL DEFAULT 0,
    reply_text  TEXT
);

-- verzovaný strategický profil (výstup učící smyčky)
CREATE TABLE IF NOT EXISTS strategy (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    sample_size INTEGER,
    profile     TEXT NOT NULL   -- JSON
);

-- audit log všeho, co agent udělal
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    ref         TEXT,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
