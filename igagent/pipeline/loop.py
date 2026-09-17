"""Autonomní smyčka: měř → uč se → plánuj → vyrob → publikuj → reportuj.

Jeden `run_cycle()` je navržený tak, aby se dal pouštět z cronu klidně
každou hodinu. Každý krok je samostatně chybově odolný — když spadne
plánování, sběr dat a publikace proběhnou dál.
"""

from __future__ import annotations

import datetime as dt
import traceback

from ..analytics import Collector, Learner
from ..brain import Brain
from ..errors import IgAgentError
from ..instagram import GraphClient
from ..store import Store
from ..util import ensure_dir, get_logger, to_iso, utcnow, write_json
from .planner import Planner
from .producer import Producer
from .publisher import QueuePublisher
from .repurpose import Repurposer
from .seed import Seeder

log = get_logger(__name__)


class Agent:
    """Drží pohromadě všechny části a umí je pustit v pořadí."""

    def __init__(self, settings, store=None, client=None, brain=None, dry_run=False):
        self.settings = settings.ensure_dirs()
        self.dry_run = dry_run
        self.store = store or Store(settings.db_path)
        self.client = client or GraphClient(settings)
        self.brain = brain or Brain(settings)
        self.learner = Learner(self.store, settings)
        self.collector = Collector(self.client, self.store, settings)
        self.planner = Planner(self.store, settings, self.brain, self.learner)
        self.producer = Producer(self.store, settings, self.brain, self.learner)
        self.queue_publisher = QueuePublisher(self.store, settings, self.client, dry_run=dry_run)
        self.repurposer = Repurposer(self.store, settings, self.brain, self.learner,
                                     self.planner.scheduler)
        self.seeder = Seeder(self.store, settings, self.planner.scheduler)

    # ------------------------------------------------------------ kroky
    def step_collect(self):
        return self.collector.run()

    def step_learn(self):
        profile = self.learner.run()
        log.info("Strategie aktualizována (vzorek %d). Dělej víc: %s",
                 profile.sample_size,
                 ", ".join(f"{a['vlastnost']}={a['hodnota']}" for a in profile.get("do_more", []))
                 or "zatím nic jistého")
        return profile

    def step_plan(self, count=None, notes=None):
        return self.planner.plan(count=count, notes=notes)

    def step_repurpose(self, count=2, force=False):
        """Po každých N videích sáhne po vítězných námětech a zpracuje je znovu."""
        return self.repurposer.run(count=count, force=force)

    def step_produce(self, horizon_hours=48, limit=3):
        """Vyrábí jen to, co brzy poletí ven — ať se nepálí tokeny do zásoby."""
        due_before = to_iso(utcnow() + dt.timedelta(hours=horizon_hours))
        pending = [item for item in self.store.queue(status="planned", limit=limit * 4)
                   if not item.scheduled_for or item.scheduled_for <= due_before]
        produced = []
        for item in pending[:limit]:
            produced.append(self.producer.produce(item))
        return produced

    def step_publish(self, limit=2):
        return self.queue_publisher.publish_due(limit=limit)

    def step_report(self, days=28, force=False):
        """Týdenní analýza profilu — v neděli, nebo na vyžádání."""
        now = utcnow()
        if not force and now.weekday() != 6:
            return None
        return self.write_report(days=days)

    def write_report(self, days=28):
        rows = self.store.posts_with_latest_metrics(limit=120)
        cutoff = to_iso(utcnow() - dt.timedelta(days=days))
        recent = [r for r in rows if (r.get("published_at") or "") >= cutoff]
        snapshots = self.store.account_snapshots(limit=days)
        from ..analytics.metrics import summarize

        analysis = self.brain.analyze_profile(
            snapshot={"posledni": snapshots[0] if snapshots else {},
                      "historie": snapshots[:14],
                      "souhrn_kpi": summarize(
                          [r for r in recent if r.get("reach")],
                          self.store.latest_followers())},
            posts=[_post_for_report(r) for r in recent],
            strategy=dict(self.learner.current_profile()),
            comments=self.store.recent_comment_texts(limit=60),
            period_days=days)
        path = ensure_dir(self.settings.reports_dir) / f"analyza-{utcnow():%Y-%m-%d}.json"
        write_json(path, analysis)
        markdown = _report_markdown(analysis, days)
        md_path = path.with_suffix(".md")
        md_path.write_text(markdown, encoding="utf-8")
        self.store.log_event("report", payload={"path": str(md_path)})
        log.info("Analýza uložena: %s", md_path)
        return {"json": str(path), "markdown": str(md_path), "analysis": analysis}

    # ------------------------------------------------------------ celý cyklus
    def run_cycle(self, do_plan=True, do_produce=True, do_publish=True, do_report=True):
        """Jeden průchod celou smyčkou. Každý krok selhává samostatně."""
        summary = {"started_at": to_iso(utcnow()), "steps": {}, "errors": {}}

        for name, func in (
            ("collect", self.step_collect),
            ("learn", self.step_learn),
            ("plan", self.step_plan if do_plan else None),
            ("repurpose", self.step_repurpose if do_plan else None),
            ("produce", self.step_produce if do_produce else None),
            ("publish", self.step_publish if do_publish else None),
            ("report", self.step_report if do_report else None),
        ):
            if func is None:
                continue
            try:
                result = func()
                summary["steps"][name] = _describe(result)
            except IgAgentError as exc:
                summary["errors"][name] = str(exc)
                log.error("Krok '%s' selhal: %s", name, exc)
            except Exception as exc:  # noqa: BLE001 - cyklus nesmí spadnout celý
                summary["errors"][name] = f"{type(exc).__name__}: {exc}"
                log.error("Krok '%s' neočekávaně spadl:\n%s", name, traceback.format_exc())

        summary["finished_at"] = to_iso(utcnow())
        self.store.log_event("cycle", payload=summary)
        return summary

    def close(self):
        self.store.close()


def _describe(result):
    if result is None:
        return "přeskočeno"
    if isinstance(result, list):
        return f"{len(result)} položek"
    if isinstance(result, dict):
        return {k: (v if isinstance(v, (int, float, str)) else "ok") for k, v in result.items()}
    if hasattr(result, "sample_size"):
        return f"strategie (vzorek {result.sample_size})"
    return str(result)[:200]


def _post_for_report(row):
    """Data jednoho videa pro analytika — KPI napřed, dosah až jako kontext."""
    from ..analytics.metrics import kpi_rates, watch_through

    rates = kpi_rates(row, row.get("duration_seconds"))
    return {
        "serie": row.get("series"),
        "jazyk": row.get("language"),
        "format": row.get("format"),
        "tema": row.get("topic"),
        "hook": row.get("hook_style"),
        "cta": row.get("cta_type"),
        "hodina": row.get("local_hour"),
        "den": row.get("local_weekday"),
        "skore": row.get("score"),
        "nova_sledovani_na_1k": _round(rates.get("follow_rate")),
        "sdileni_na_1k": _round(rates.get("share_rate")),
        "ulozeni_na_1k": _round(rates.get("save_rate")),
        "zhlednuti_k_dosahu": _round(rates.get("hook_rate")),
        "dokoukani_pct": watch_through(row, row.get("duration_seconds")),
        "dosah": row.get("reach"),
        "recyklace_z": row.get("repurposed_from"),
        "popisek_zacatek": (row.get("caption") or "")[:120],
        "odkaz": row.get("permalink"),
    }


def _round(value, decimals=2):
    return None if value is None else round(value, decimals)


def _report_markdown(analysis, days):
    lines = [f"# Analýza profilu — posledních {days} dní", "",
             f"*Vygenerováno {utcnow():%d.%m.%Y %H:%M} UTC · "
             f"jistota závěrů: {analysis.get('confidence', '?')}*", "",
             "## Shrnutí", "", analysis.get("summary", ""), ""]

    if analysis.get("working"):
        lines += ["## Co funguje", ""] + [f"- {x}" for x in analysis["working"]] + [""]
    if analysis.get("not_working"):
        lines += ["## Co nefunguje", ""] + [f"- {x}" for x in analysis["not_working"]] + [""]
    if analysis.get("audience_read"):
        lines += ["## Publikum", "", analysis["audience_read"], ""]
    if analysis.get("experiments"):
        lines += ["## Experimenty na další období", ""]
        for exp in analysis["experiments"]:
            lines += [f"**{exp.get('hypothesis', '')}**",
                      f"- Změna: {exp.get('change', '')}",
                      f"- Měřím: {exp.get('measure', '')}", ""]
    if analysis.get("next_actions"):
        lines += ["## Další kroky", ""] + [f"{i}. {x}" for i, x
                                           in enumerate(analysis["next_actions"], 1)] + [""]
    return "\n".join(lines)
