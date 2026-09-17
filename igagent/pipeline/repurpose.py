"""Recyklace vítězných námětů.

Dobrá myšlenka se neopouští po jednom videu. Po každých N publikovaných
videích (`repurpose_every` v brand kitu) vezme agent dva nejlepší náměty
a naplánuje jejich novou verzi — jiný úhel, jiný formát, hlubší rozbor
nebo samostatné anglické video.

Co se počítá jako „nejlepší": skóre podle konverze (nová sledování, sdílení,
uložení, dokoukání), ne podle views. Námět, který už jednou recyklovaný byl,
se znovu nebere.
"""

from __future__ import annotations

from ..store import QueueItem
from ..util import get_logger

log = get_logger(__name__)

MIN_SCORE_TO_REPURPOSE = 115.0     # pod průměrem účtu nemá smysl opakovat


class Repurposer:
    def __init__(self, store, settings, brain, learner, scheduler):
        self.store = store
        self.settings = settings
        self.brain = brain
        self.learner = learner
        self.scheduler = scheduler

    # ------------------------------------------------------------ stav
    def published_since_last(self):
        """Kolik videí vyšlo od poslední recyklace."""
        events = [e for e in self.store.recent_events(limit=400) if e["kind"] == "repurpose"]
        posts = self.store.posts(limit=400)
        if not events:
            return len(posts)
        last_ts = events[0]["ts"]
        return len([p for p in posts if (p.get("published_at") or "") > last_ts])

    def repurpose_rounds_done(self):
        return len([e for e in self.store.recent_events(limit=400)
                    if e["kind"] == "repurpose"])

    def is_due(self):
        every = self.settings.brand.repurpose_every or 10
        return self.published_since_last() >= every

    def candidates(self, limit=8):
        """Nejlepší dosud nerecyklované náměty."""
        rows = self.store.posts_with_latest_metrics(limit=200)
        already = {p.get("repurposed_from") for p in self.store.posts(limit=400)
                   if p.get("repurposed_from")}
        already |= {item.repurposed_from for item in self.store.queue(limit=200)
                    if item.repurposed_from}

        usable = [r for r in rows
                  if r.get("score") is not None
                  and r["score"] >= MIN_SCORE_TO_REPURPOSE
                  and r["media_id"] not in already]
        usable.sort(key=lambda r: r["score"], reverse=True)
        return usable[:limit]

    # ------------------------------------------------------------ běh
    def run(self, count=2, force=False, days=None):
        if not force and not self.is_due():
            every = self.settings.brand.repurpose_every or 10
            log.info("Recyklace zatím nemá smysl (%d/%d videí od minule).",
                     self.published_since_last(), every)
            return []

        winners = self.candidates()
        if not winners:
            log.info("Žádný námět zatím nepřekonal práh %.0f — není co recyklovat.",
                     MIN_SCORE_TO_REPURPOSE)
            return []

        # každé N-té kolo obsahuje anglickou verzi
        english_every = self.settings.brand.english_every or 5
        english = english_every > 0 and (self.repurpose_rounds_done() + 1) % english_every == 0

        proposal = self.brain.repurpose(
            winners, count=count, strategy=dict(self.learner.current_profile()),
            english=english, recent_posts=self.store.posts(limit=10))

        slots = self.scheduler.series_slots(days) or []
        created = []
        for index, variant in enumerate(proposal.get("variants", [])[:count]):
            source = next((w for w in winners
                           if w["media_id"] == variant.get("source_media_id")), None)
            if source is None:
                log.warning("Claude odkázal na neznámé médium %s, přeskakuji.",
                            variant.get("source_media_id"))
                continue
            when = slots[index][0] if index < len(slots) else None
            series_key = variant.get("series") or source.get("series") or ""
            series = self.settings.brand.series_by_key(series_key)

            item = QueueItem(
                status="planned",
                format=variant.get("format") or source.get("format") or "REEL",
                template=(series.template if series else "video"),
                scheduled_for=when,
                series=series_key,
                language=variant.get("language", "cs"),
                repurposed_from=source["media_id"],
                variant=variant.get("variant"),
                title=variant.get("title", "")[:120],
                topic=source.get("topic", ""),
                pillar=(series.name if series else source.get("pillar", "")),
                hook_style=variant.get("hook_style") or source.get("hook_style", ""),
                cta_type=source.get("cta_type", ""),
                brief={
                    "angle": variant.get("angle"),
                    "key_points": variant.get("key_points", []),
                    "why": variant.get("why"),
                    "needs_user_media": True,
                    "repurpose_of": {
                        "media_id": source["media_id"],
                        "permalink": source.get("permalink"),
                        "score": source.get("score"),
                        "caption": (source.get("caption") or "")[:300],
                    },
                    "series_guidance": series.guidance if series else None,
                    "target_seconds": series.target_seconds if series else None,
                    "jumpcut": series.jumpcut if series else False,
                },
            )
            created.append(self.store.enqueue(item))
            log.info("Recykluji #%s: %s → %s (%s, %s)", created[-1].id,
                     source["media_id"], item.title, item.variant, item.language)

        self.store.log_event("repurpose", payload={
            "count": len(created),
            "english": english,
            "sources": [c.repurposed_from for c in created]})
        return created
