"""Konfigurace agenta: proměnné prostředí (.env) + brand kit (YAML)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .errors import ConfigError

ROOT = Path(__file__).resolve().parent.parent

# Autopilot režimy:
#   off    — agent jen měří a navrhuje, nepublikuje (ani nevyrábí, pokud neřekneš)
#   review — agent vyrobí a naplánuje, ale publikuje až po `igagent queue approve`
#   full   — agent publikuje sám podle plánu (v rámci denních limitů)
AUTOPILOT_MODES = ("off", "review", "full")


WEEKDAYS_CS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
WEEKDAY_KEYS = {"po": 0, "ut": 1, "st": 2, "ct": 3, "pa": 4, "so": 5, "ne": 6,
                "pondeli": 0, "utery": 1, "streda": 2, "ctvrtek": 3, "patek": 4,
                "sobota": 5, "nedele": 6}


@dataclass
class Series:
    """Opakovatelná série — páteř profilu.

    Místo vymýšlení nové identity každý den má každý den v týdnu svou sérii
    s pevným formátem, typem hooku a výchozí šablonou. Agent pak neřeší
    „co dnes", ale „jak dneska udělat tuhle sérii co nejlíp".
    """

    key: str = ""
    name: str = ""
    promise: str = ""              # co divák dostane, jednou větou
    weekday: int = 0               # 0 = pondělí
    hour: int = 18
    format: str = "REEL"
    template: str = "video"
    hook_style: str = "kontrarian"
    cta_type: str = "uloz"
    jumpcut: bool = True           # talking head → vyhodit ticho
    needs_user_media: bool = True  # série stojí na tvém natočeném videu
    needs_timely_input: bool = False   # reaguje na aktuální moment
    target_seconds: int = 35
    examples: list = field(default_factory=list)
    guidance: str = ""

    @classmethod
    def from_raw(cls, key, raw):
        data = dict(raw or {})
        data["key"] = key
        weekday = data.get("weekday", 0)
        if isinstance(weekday, str):
            normalized = weekday.strip().lower()
            if normalized not in WEEKDAY_KEYS:
                raise ConfigError(
                    f"Série '{key}': neznámý den '{weekday}'. "
                    f"Použij číslo 0–6 nebo jeden z {sorted(set(WEEKDAY_KEYS))}.")
            data["weekday"] = WEEKDAY_KEYS[normalized]
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"Série '{key}': neznámé klíče {sorted(unknown)}")
        series = cls(**data)
        if not 0 <= series.weekday <= 6:
            raise ConfigError(f"Série '{key}': weekday musí být 0–6.")
        return series

    @property
    def weekday_name(self):
        return WEEKDAYS_CS[self.weekday]

    def prompt_block(self):
        lines = [f"### {self.name}  (klíč: {self.key}, {self.weekday_name} {self.hour}:00)",
                 f"Slib divákovi: {self.promise}",
                 f"Formát: {self.format} · hook: {self.hook_style} · CTA: {self.cta_type}"]
        if self.guidance:
            lines.append(f"Jak ji dělat: {self.guidance}")
        if self.examples:
            lines.append("Příklady námětů, které do série patří:")
            lines.extend(f"  - {e}" for e in self.examples)
        if self.needs_timely_input:
            lines.append("POZOR: tahle série reaguje na konkrétní aktuální moment. "
                         "Bez zadaného momentu od majitele námět nevymýšlej — "
                         "místo toho popiš, jaký typ momentu hledat.")
        return "\n".join(lines)


@dataclass
class Brand:
    """Brand kit — vše, co agent potřebuje, aby zněl a vypadal jako ty."""

    name: str = "MikeCipro"
    handle: str = "mikecipro"
    language: str = "cs"
    tone: str = "přímý, lidský, konkrétní, bez korporátní vaty"
    audience: str = "lidé, kteří chtějí praktické rady"
    topics: list = field(default_factory=list)
    pillars: list = field(default_factory=list)
    cta: list = field(default_factory=list)
    colors: dict = field(default_factory=lambda: {
        "bg": "#0E1116", "bg_alt": "#161B22", "fg": "#F5F7FA",
        "muted": "#9BA6B2", "accent": "#FF5A36", "accent_alt": "#FFC53D",
    })
    fonts: dict = field(default_factory=dict)
    logo_path: str = ""
    watermark_text: str = ""
    hashtags_core: list = field(default_factory=list)
    hashtags_pool: list = field(default_factory=list)
    banned_words: list = field(default_factory=list)
    posting_windows: list = field(default_factory=lambda: [8, 12, 18, 20])
    weekly_post_target: int = 5
    series: dict = field(default_factory=dict)
    stories: list = field(default_factory=list)
    repurpose_every: int = 10       # po kolika videích sáhnout po vítězích
    english_every: int = 5          # každý N-tý recyklovaný námět anglicky
    format_mix: dict = field(default_factory=lambda: {"REEL": 0.5, "CAROUSEL": 0.3, "IMAGE": 0.2})
    notes: str = ""

    @classmethod
    def load(cls, path):
        p = Path(path)
        if not p.exists():
            raise ConfigError(
                f"Brand kit nenalezen: {p}. Zkopíruj config/brand.example.yaml na {p} a uprav."
            )
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise ConfigError(f"Neznámé klíče v brand kitu {p}: {sorted(unknown)}")
        raw["series"] = {key: Series.from_raw(key, value)
                         for key, value in (raw.get("series") or {}).items()}
        brand = cls(**raw)
        brand._check_series()
        return brand

    def _check_series(self):
        """Dvě série na stejný den by si přebíraly termín."""
        seen = {}
        for series in self.series.values():
            clash = seen.get(series.weekday)
            if clash:
                raise ConfigError(
                    f"Série '{series.key}' i '{clash}' mají {series.weekday_name} — "
                    "každý den může mít jen jednu sérii.")
            seen[series.weekday] = series.key

    def to_dict(self):
        data = asdict(self)
        data["series"] = {k: asdict(v) if hasattr(v, "key") else v
                          for k, v in (self.series or {}).items()}
        return data

    def series_for_weekday(self, weekday):
        return next((s for s in self.series.values() if s.weekday == weekday), None)

    def series_by_key(self, key):
        return self.series.get(key)

    @property
    def weekly_plan(self):
        """{0: Series, 2: Series, …} — co se kterého dne natáčí."""
        return {s.weekday: s for s in self.series.values()}

    def prompt_block(self):
        """Kompaktní textový popis značky pro systémový prompt Claude."""
        lines = [
            f"Značka: {self.name} (@{self.handle})",
            f"Jazyk obsahu: {self.language}",
            f"Tón: {self.tone}",
            f"Publikum: {self.audience}",
        ]
        if self.series:
            lines.append("")
            lines.append("=== OPAKOVATELNÉ SÉRIE (páteř profilu) ===")
            lines.append("Každý příspěvek patří do jedné z nich. Nevymýšlej nové rubriky.")
            for series in sorted(self.series.values(), key=lambda s: s.weekday):
                lines.append("")
                lines.append(series.prompt_block())
            lines.append("")
        if self.pillars:
            lines.append("Obsahové pilíře: " + "; ".join(str(p) for p in self.pillars))
        if self.topics:
            lines.append("Témata: " + ", ".join(str(t) for t in self.topics))
        if self.cta:
            lines.append("Typické CTA: " + " | ".join(str(c) for c in self.cta))
        if self.banned_words:
            lines.append("Nikdy nepoužívej slova/fráze: " + ", ".join(self.banned_words))
        if self.notes:
            lines.append("Poznámky: " + self.notes)
        return "\n".join(lines)


@dataclass
class Settings:
    # --- Instagram / Meta ---
    ig_user_id: str = ""
    ig_access_token: str = ""
    fb_app_id: str = ""
    fb_app_secret: str = ""
    graph_version: str = "v23.0"
    graph_base: str = "https://graph.facebook.com"

    # --- Claude ---
    anthropic_api_key: str = ""
    model: str = "claude-opus-5"
    model_fast: str = "claude-sonnet-5"
    effort: str = "high"

    # --- hosting médií (Graph API si média stahuje z veřejné URL) ---
    media_host: str = "local"          # local | s3
    media_public_base: str = ""        # např. https://cdn.tvujweb.cz/ig
    s3_bucket: str = ""
    s3_prefix: str = "ig"
    s3_region: str = "auto"
    s3_endpoint: str = ""

    # --- cesty ---
    data_dir: Path = ROOT / "data"
    work_dir: Path = ROOT / "work"
    out_dir: Path = ROOT / "out"
    reports_dir: Path = ROOT / "reports"
    brand_path: Path = ROOT / "config" / "brand.yaml"
    db_path: Path = ROOT / "data" / "igagent.sqlite3"
    log_path: Path = ROOT / "data" / "igagent.log"

    # --- chování ---
    timezone: str = "Europe/Prague"
    autopilot: str = "review"
    max_posts_per_day: int = 2
    min_hours_between_posts: int = 5
    metrics_collect_after_hours: int = 24
    metrics_refresh_days: int = 14
    queue_lookahead_days: int = 7
    explore_rate: float = 0.25          # podíl „průzkumných" rozhodnutí bandity
    qa_images: bool = True              # vizuální kontrola grafiky přes Claude před publikací
    log_level: str = "INFO"

    brand: Brand = field(default_factory=Brand)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, env_file=None, overrides=None):
        load_dotenv(env_file or ROOT / ".env", override=False)
        env = os.environ

        def path_of(key, default):
            raw = env.get(key)
            return Path(raw).expanduser() if raw else default

        settings = cls(
            ig_user_id=env.get("IG_USER_ID", ""),
            ig_access_token=env.get("IG_ACCESS_TOKEN", ""),
            fb_app_id=env.get("FB_APP_ID", ""),
            fb_app_secret=env.get("FB_APP_SECRET", ""),
            graph_version=env.get("GRAPH_API_VERSION", "v23.0"),
            graph_base=env.get("GRAPH_API_BASE", "https://graph.facebook.com"),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
            model=env.get("CLAUDE_MODEL", "claude-opus-5"),
            model_fast=env.get("CLAUDE_MODEL_FAST", "claude-sonnet-5"),
            effort=env.get("CLAUDE_EFFORT", "high"),
            media_host=env.get("MEDIA_HOST", "local"),
            media_public_base=env.get("MEDIA_PUBLIC_BASE", "").rstrip("/"),
            s3_bucket=env.get("S3_BUCKET", ""),
            s3_prefix=env.get("S3_PREFIX", "ig"),
            s3_region=env.get("S3_REGION", "auto"),
            s3_endpoint=env.get("S3_ENDPOINT", ""),
            timezone=env.get("TIMEZONE", "Europe/Prague"),
            autopilot=env.get("AUTOPILOT", "review").lower(),
            max_posts_per_day=int(env.get("MAX_POSTS_PER_DAY", "2")),
            min_hours_between_posts=int(env.get("MIN_HOURS_BETWEEN_POSTS", "5")),
            metrics_collect_after_hours=int(env.get("METRICS_COLLECT_AFTER_HOURS", "24")),
            metrics_refresh_days=int(env.get("METRICS_REFRESH_DAYS", "14")),
            queue_lookahead_days=int(env.get("QUEUE_LOOKAHEAD_DAYS", "7")),
            explore_rate=float(env.get("EXPLORE_RATE", "0.25")),
            qa_images=env.get("QA_IMAGES", "1").lower() in ("1", "true", "yes", "ano"),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )
        settings.data_dir = path_of("DATA_DIR", settings.data_dir)
        settings.work_dir = path_of("WORK_DIR", settings.work_dir)
        settings.out_dir = path_of("OUT_DIR", settings.out_dir)
        settings.reports_dir = path_of("REPORTS_DIR", settings.reports_dir)
        settings.brand_path = path_of("BRAND_PATH", settings.brand_path)
        settings.db_path = path_of("DB_PATH", settings.data_dir / "igagent.sqlite3")
        settings.log_path = path_of("LOG_PATH", settings.data_dir / "igagent.log")

        for key, value in (overrides or {}).items():
            setattr(settings, key, value)

        if settings.autopilot not in AUTOPILOT_MODES:
            raise ConfigError(
                f"AUTOPILOT musí být jeden z {AUTOPILOT_MODES}, dostal jsem '{settings.autopilot}'")

        if settings.brand_path.exists():
            settings.brand = Brand.load(settings.brand_path)
        return settings

    # ------------------------------------------------------------------
    def ensure_dirs(self):
        for d in (self.data_dir, self.work_dir, self.out_dir, self.reports_dir):
            Path(d).mkdir(parents=True, exist_ok=True)
        return self

    @property
    def graph_url(self):
        return f"{self.graph_base.rstrip('/')}/{self.graph_version}"

    def require_instagram(self):
        missing = [k for k, v in (("IG_USER_ID", self.ig_user_id),
                                  ("IG_ACCESS_TOKEN", self.ig_access_token)) if not v]
        if missing:
            raise ConfigError(
                "Chybí " + ", ".join(missing) + " v .env. Postup získání je v README "
                "(sekce „Nastavení přístupu k Instagramu“).")
        return self

    def require_claude(self):
        if not self.anthropic_api_key and not os.getenv("ANTHROPIC_AUTH_TOKEN"):
            raise ConfigError("Chybí ANTHROPIC_API_KEY v .env (nebo přihlášení přes `ant auth login`).")
        return self

    def require_media_host(self):
        if self.media_host == "local" and not self.media_public_base:
            raise ConfigError(
                "Instagram Graph API si médium stahuje z veřejné URL. Nastav MEDIA_PUBLIC_BASE "
                "(veřejná adresa složky OUT_DIR) nebo přepni MEDIA_HOST=s3 a vyplň S3_*.")
        if self.media_host == "s3" and not self.s3_bucket:
            raise ConfigError("MEDIA_HOST=s3 vyžaduje S3_BUCKET.")
        return self

    def redacted(self):
        data = {k: v for k, v in self.__dict__.items() if k != "brand"}
        for secret in ("ig_access_token", "anthropic_api_key", "fb_app_secret"):
            if data.get(secret):
                data[secret] = f"…{str(data[secret])[-4:]} ({len(str(data[secret]))} znaků)"
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in data.items()}
