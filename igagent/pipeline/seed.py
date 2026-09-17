"""Nasazení startovní dávky námětů do fronty.

Náměty ze souboru (výchozí `config/seed-first-8.yaml`) se rozdělí do
nejbližších volných termínů své série — takže se samy rozloží do
týdenního režimu a nemusíš je časovat ručně.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..errors import ConfigError
from ..store import QueueItem
from ..util import get_logger

log = get_logger(__name__)

DEFAULT_SEED = Path("config/seed-first-8.yaml")


class Seeder:
    def __init__(self, store, settings, scheduler):
        self.store = store
        self.settings = settings
        self.scheduler = scheduler

    def load(self, path=None):
        path = Path(path or DEFAULT_SEED)
        if not path.exists():
            raise ConfigError(f"Soubor s náměty nenalezen: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(raw, list):
            raise ConfigError(f"{path} musí obsahovat seznam námětů.")
        return raw

    def run(self, path=None, days=60, skip_existing=True):
        ideas = self.load(path)
        brand = self.settings.brand

        existing = {item.title for item in self.store.queue(limit=500)}
        existing |= {(p.get("caption") or "").split("\n")[0] for p in self.store.posts(limit=200)}

        # volné termíny pro každou sérii zvlášť
        slots_by_series = {}
        for iso_slot, series in self.scheduler.series_slots(days=days):
            slots_by_series.setdefault(series.key, []).append(iso_slot)

        created, skipped = [], []
        for idea in ideas:
            title = str(idea.get("title", "")).strip()
            series_key = idea.get("series", "")
            series = brand.series_by_key(series_key)
            if series is None:
                raise ConfigError(
                    f"Námět „{title}\" odkazuje na sérii '{series_key}', "
                    f"která v brand kitu není. Dostupné: {sorted(brand.series)}")
            if skip_existing and title in existing:
                skipped.append(title)
                continue

            queue = slots_by_series.get(series_key) or []
            when = queue.pop(0) if queue else None
            if when is None:
                log.warning("Pro sérii %s došly volné termíny — „%s\" zůstane bez času.",
                            series_key, title)

            item = QueueItem(
                status="planned",
                format=series.format,
                template=series.template,
                scheduled_for=when,
                series=series_key,
                language=idea.get("language", "cs"),
                title=title[:120],
                topic=idea.get("topic", "") or series.key,
                pillar=series.name,
                hook_style=idea.get("hook_style") or series.hook_style,
                cta_type=idea.get("cta_type") or series.cta_type,
                brief={
                    "angle": (idea.get("angle") or "").strip(),
                    "key_points": idea.get("key_points", []),
                    "why": "startovní dávka námětů",
                    "needs_user_media": series.needs_user_media,
                    "series_guidance": series.guidance,
                    "target_seconds": series.target_seconds,
                    "jumpcut": series.jumpcut,
                },
            )
            created.append(self.store.enqueue(item))

        self.store.log_event("seed", payload={"created": len(created),
                                              "skipped": len(skipped)})
        if skipped:
            log.info("Přeskočeno (už ve frontě): %s", ", ".join(skipped))
        return created, skipped
