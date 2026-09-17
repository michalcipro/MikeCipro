"""Výběr časů publikace.

Když má značka nastavené série, řídí kalendář ony: každý den v týdnu má
svou sérii a svůj čas. Agent pak neřeší „co dneska", ale „jak dneska udělat
tuhle sérii co nejlíp".

Bez sérií (nebo pro obsah mimo ně) se použije bandita nad `posting_windows`.
"""

from __future__ import annotations

import datetime as dt

from ..util import get_logger, local_tz, parse_iso, to_iso, utcnow

log = get_logger(__name__)


class Scheduler:
    def __init__(self, settings, learner, store):
        self.settings = settings
        self.learner = learner
        self.store = store
        self.tz = local_tz(settings.timezone)

    # ------------------------------------------------------------ obsazenost
    def existing_slots(self):
        """Už naplánované i publikované časy — abychom se do nich netrefili."""
        slots = []
        for raw in self.store.scheduled_times(since=to_iso(utcnow() - dt.timedelta(days=2))):
            parsed = parse_iso(raw)
            if parsed:
                slots.append(parsed)
        for post in self.store.posts(limit=40):
            parsed = parse_iso(post.get("published_at"))
            if parsed and parsed > utcnow() - dt.timedelta(days=2):
                slots.append(parsed)
        return sorted(slots)

    def _is_free(self, slot, taken):
        gap = dt.timedelta(hours=self.settings.min_hours_between_posts)
        same_day = [t for t in taken
                    if t.astimezone(self.tz).date() == slot.astimezone(self.tz).date()]
        if len(same_day) >= self.settings.max_posts_per_day:
            return False
        return all(abs((slot - t).total_seconds()) >= gap.total_seconds() for t in taken)

    # ------------------------------------------------------------ série
    def series_slots(self, days=None, start_from=None):
        """[(čas v UTC, Series)] pro každý volný termín série v horizontu."""
        brand = self.settings.brand
        plan = brand.weekly_plan
        if not plan:
            return []

        days = days or self.settings.queue_lookahead_days
        taken = self.existing_slots()
        now = (start_from or utcnow()).astimezone(self.tz)

        out = []
        for offset in range(days + 1):
            day = (now + dt.timedelta(days=offset)).date()
            series = plan.get(day.weekday())
            if not series:
                continue
            slot = dt.datetime.combine(day, dt.time(series.hour, 0), tzinfo=self.tz)
            if slot <= now + dt.timedelta(minutes=30):
                continue
            if not self._is_free(slot, taken + [s for s, _ in out]):
                log.debug("Termín %s už je obsazený, sérii %s přeskakuji.", slot, series.key)
                continue
            out.append((slot, series))
        return [(to_iso(slot), series) for slot, series in out]

    # ------------------------------------------------------------ volné sloty
    def pick_slots(self, count, start_from=None, days=None):
        """Časy pro obsah mimo série — hodinu vybírá bandita."""
        days = days or self.settings.queue_lookahead_days
        taken = self.existing_slots()
        now = (start_from or utcnow()).astimezone(self.tz)
        candidate_hours = self.settings.brand.posting_windows or [9, 12, 18, 20]

        chosen = []
        day_offset = 0
        while len(chosen) < count and day_offset <= days:
            day = (now + dt.timedelta(days=day_offset)).date()
            per_day = 0
            attempts = 0
            while (per_day < self.settings.max_posts_per_day and len(chosen) < count
                   and attempts < len(candidate_hours) * 2):
                attempts += 1
                hour = int(self.learner.choose("hour", [str(h) for h in candidate_hours]) or
                           candidate_hours[0])
                minute = 5 * ((hash((day.toordinal(), hour, len(chosen))) % 9))
                slot = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=self.tz)
                if slot <= now + dt.timedelta(minutes=30):
                    continue
                if not self._is_free(slot, taken + chosen):
                    continue
                chosen.append(slot)
                taken.append(slot)
                per_day += 1
            day_offset += 1

        chosen.sort()
        return [to_iso(slot) for slot in chosen]

    # ------------------------------------------------------------ kolik chybí
    def slots_needed(self, days=None):
        """Kolik příspěvků do plánu ještě chybí vzhledem k cíli."""
        days = days or self.settings.queue_lookahead_days
        horizon = to_iso(utcnow() + dt.timedelta(days=days))
        pending = [item for item in self.store.queue(
            status=("planned", "produced", "approved"), limit=100)
            if (item.scheduled_for or horizon) <= horizon]

        if self.settings.brand.series:
            target = len(self.series_slots(days)) + len(pending)
            return max(0, len(self.series_slots(days))), target, len(pending)

        target = max(1, round(self.settings.brand.weekly_post_target * days / 7))
        return max(0, target - len(pending)), target, len(pending)

    def next_week_overview(self, days=14):
        """Přehled „co kdy natočit" — pro `igagent kalendar`."""
        rows = []
        for iso_slot, series in self.series_slots(days):
            rows.append({
                "kdy": parse_iso(iso_slot).astimezone(self.tz),
                "serie": series.name,
                "klic": series.key,
                "format": series.format,
                "potreba_natocit": series.needs_user_media,
                "aktualni_moment": series.needs_timely_input,
            })
        return rows
