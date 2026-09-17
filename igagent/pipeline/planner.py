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

    def plan(self, count=None, days=None, inbox=None, notes=None):
        needed, target, pending = self.scheduler.slots_needed(days)
        count = count if count is not None else needed
        if count <= 0:
            log.info("Fronta je plná (%d/%d na %d dní) — nic neplánuji.",
                     pending, target, days or self.settings.queue_lookahead_days)
            return []

        strategy = self.learner.current_profile()
        recent = self._recent_for_prompt()
        media = self.available_media(inbox)

        log.info("Plánuji %d příspěvků (v frontě %d, cíl %d).", count, pending, target)
        plan = self.brain.plan_content(count, strategy=dict(strategy), recent_posts=recent,
                                       calendar_notes=notes, available_media=media)
        log.info("Claude: %s", plan.get("reasoning", "")[:200])

        slots = self.scheduler.pick_slots(len(plan.get("items", [])), days=days)
        created = []
        for index, idea in enumerate(plan.get("items", [])):
            item = QueueItem(
                status="planned",
                format=idea.get("format", "IMAGE"),
                template=idea.get("template", "quote"),
                scheduled_for=slots[index] if index < len(slots) else None,
                title=idea.get("title", "")[:120],
                topic=idea.get("topic", ""),
                pillar=idea.get("pillar", ""),
                hook_style=idea.get("hook_style", ""),
                cta_type=idea.get("cta_type", ""),
                brief={
                    "angle": idea.get("angle"),
                    "key_points": idea.get("key_points", []),
                    "why": idea.get("why"),
                    "needs_user_media": bool(idea.get("needs_user_media")),
                    "planner_reasoning": plan.get("reasoning"),
                },
                source_media=[],
            )
            if idea.get("needs_user_media") and media:
                item.source_media = _guess_media(idea, media)
            created.append(self.store.enqueue(item))

        self.store.log_event("plan", payload={"count": len(created),
                                              "reasoning": plan.get("reasoning")})
        return created

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
