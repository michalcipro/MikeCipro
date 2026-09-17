"""Plánovač: z dat a nápadů Claude poskládá frontu příspěvků."""

from __future__ import annotations

from pathlib import Path

from ..store import QueueItem
from ..util import get_logger
from .schedule import Scheduler

log = get_logger(__name__)

MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".mp4", ".mov", ".m4v"}


class Planner:
    def __init__(self, store, settings, brain, learner):
        self.store = store
        self.settings = settings
        self.brain = brain
        self.learner = learner
        self.scheduler = Scheduler(settings, learner, store)

    def available_media(self, inbox=None):
        """Soubory, které jsi agentovi nasypal do složky `inbox`."""
        inbox = Path(inbox or self.settings.data_dir / "inbox")
        if not inbox.exists():
            return []
        return sorted(str(p) for p in inbox.iterdir()
                      if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES)

    def plan(self, count=None, days=None, inbox=None, notes=None, moments=None):
        """Naplní frontu. Se sériemi plánuje na jejich pevné termíny."""
        if self.settings.brand.series:
            return self._plan_series(days=days, inbox=inbox, notes=notes, moments=moments)
        return self._plan_free(count=count, days=days, inbox=inbox, notes=notes)

    # ------------------------------------------------------------ série
    def _plan_series(self, days=None, inbox=None, notes=None, moments=None):
        slots = self.scheduler.series_slots(days)
        if not slots:
            log.info("Všechny termíny sérií v horizontu už jsou obsazené.")
            return []

        strategy = self.learner.current_profile()
        media = self.available_media(inbox)
        log.info("Plánuji %d termínů: %s", len(slots),
                 ", ".join(s.key for _, s in slots))

        plan = self.brain.plan_content(
            len(slots), strategy=dict(strategy), recent_posts=self._recent_for_prompt(),
            calendar_notes=notes, available_media=media, slots=slots,
            moments=moments or self.pending_moments())
        log.info("Claude: %s", plan.get("reasoning", "")[:200])

        items = plan.get("items", [])
        created = []
        for index, (when, series) in enumerate(slots):
            if index >= len(items):
                break
            created.append(self._enqueue(items[index], when, series, media))

        self.store.log_event("plan", payload={"count": len(created), "mode": "serie",
                                              "reasoning": plan.get("reasoning")})
        return created

    # ------------------------------------------------------------ bez sérií
    def _plan_free(self, count=None, days=None, inbox=None, notes=None):
        needed, target, pending = self.scheduler.slots_needed(days)
        count = count if count is not None else needed
        if count <= 0:
            log.info("Fronta je plná (%d/%d na %d dní) — nic neplánuji.",
                     pending, target, days or self.settings.queue_lookahead_days)
            return []

        strategy = self.learner.current_profile()
        media = self.available_media(inbox)
        plan = self.brain.plan_content(count, strategy=dict(strategy),
                                       recent_posts=self._recent_for_prompt(),
                                       calendar_notes=notes, available_media=media)
        slots = self.scheduler.pick_slots(len(plan.get("items", [])), days=days)
        created = []
        for index, idea in enumerate(plan.get("items", [])):
            when = slots[index] if index < len(slots) else None
            created.append(self._enqueue(idea, when, None, media))
        self.store.log_event("plan", payload={"count": len(created), "mode": "volny",
                                              "reasoning": plan.get("reasoning")})
        return created

    # ------------------------------------------------------------ zápis
    def _enqueue(self, idea, when, series, media):
        """Nápad od Claude + výchozí hodnoty série → položka fronty."""
        item = QueueItem(
            status="planned",
            format=(series.format if series else idea.get("format", "IMAGE")),
            template=(series.template if series else idea.get("template", "quote")),
            scheduled_for=when,
            series=(series.key if series else idea.get("series", "")),
            language="cs",
            title=idea.get("title", "")[:120],
            topic=idea.get("topic", ""),
            pillar=(series.name if series else idea.get("pillar", "")),
            hook_style=idea.get("hook_style") or (series.hook_style if series else ""),
            cta_type=idea.get("cta_type") or (series.cta_type if series else ""),
            brief={
                "angle": idea.get("angle"),
                "key_points": idea.get("key_points", []),
                "why": idea.get("why"),
                "needs_user_media": bool(idea.get("needs_user_media")
                                         or (series.needs_user_media if series else False)),
                "series_guidance": series.guidance if series else None,
                "target_seconds": series.target_seconds if series else None,
                "jumpcut": series.jumpcut if series else False,
            },
            source_media=[],
        )
        if item.brief["needs_user_media"] and media:
            item.source_media = _guess_media({"format": item.format}, media)
        return self.store.enqueue(item)

    def pending_moments(self):
        """Momenty zadané přes `igagent moment add` — vstup pro reakční sérii."""
        path = Path(self.settings.data_dir) / "moments.txt"
        if not path.exists():
            return []
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]

    def _recent_for_prompt(self, limit=15):
        rows = self.store.posts_with_latest_metrics(limit=limit)
        return [{"format": r.get("format"), "caption": r.get("caption"),
                 "topic": r.get("topic"), "score": r.get("score")} for r in rows]


def _guess_media(idea, media):
    """Hrubé přiřazení souborů k nápadu — podle formátu a názvu."""
    video = [m for m in media if Path(m).suffix.lower() in (".mp4", ".mov", ".m4v")]
    photos = [m for m in media if m not in video]
    if idea.get("format") == "REEL":
        return video[:1] or photos[:5]
    if idea.get("format") == "CAROUSEL":
        return photos[:8]
    return photos[:1]
