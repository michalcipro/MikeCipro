"""Příkazová řádka agenta.

    igagent doctor               — zkontroluje, jestli je vše nastavené
    igagent run                  — jeden autonomní cyklus (na tohle pusť cron)
    igagent collect|learn        — jen sběr dat / jen přepočet strategie
    igagent strategy             — co se agent zatím naučil
    igagent plan  [-n 5]         — navrhne obsah a naplní frontu
    igagent queue list|show|approve|reject|attach|reschedule
    igagent produce [--id N]     — vyrobí média a texty
    igagent publish [--id N]     — publikuje (--dry-run nic neodešle)
    igagent analyze              — analýza profilu do reports/
    igagent reel  VIDEO          — ruční střih Reelu
    igagent photos FOTKY…        — fotky → příspěvek nebo karusel
    igagent graphic …            — jednotlivá grafika
    igagent comments draft       — návrhy odpovědí na komentáře
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timezone as dt_timezone
from pathlib import Path

from . import __version__
from .config import Settings
from .errors import IgAgentError
from .util import get_logger, setup_logging

log = get_logger("igagent.cli")


# ====================================================================== pomocné

def _agent(args, dry_run=False):
    from .pipeline import Agent

    settings = _settings(args)
    return Agent(settings, dry_run=dry_run or getattr(args, "dry_run", False))


def _settings(args):
    overrides = {}
    if getattr(args, "autopilot", None):
        overrides["autopilot"] = args.autopilot
    settings = Settings.load(overrides=overrides).ensure_dirs()
    setup_logging(getattr(args, "log_level", None) or settings.log_level, settings.log_path)
    return settings


def _local(iso_value, settings):
    """Čas v databázi je UTC; člověku ho ukazujeme v jeho pásmu."""
    if not iso_value:
        return "—"
    from .util import local_tz, parse_iso

    parsed = parse_iso(iso_value)
    if not parsed:
        return str(iso_value)[:16]
    return f"{parsed.astimezone(local_tz(settings.timezone)):%a %d.%m. %H:%M}"


def _print(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


# ====================================================================== příkazy

def cmd_doctor(args):
    settings = _settings(args)
    report = {"verze": __version__, "konfigurace": settings.redacted(), "kontroly": {}}
    checks = report["kontroly"]

    # 1. brand kit
    checks["brand_kit"] = ("OK: " + str(settings.brand_path)) if settings.brand_path.exists() \
        else f"CHYBÍ: zkopíruj config/brand.example.yaml → {settings.brand_path}"

    # 2. fonty
    from .media import fonts as fontlib

    bold = fontlib.resolve("bold", settings.brand.fonts)
    checks["font"] = (f"OK: {bold} (čeština: "
                      f"{'ano' if fontlib.supports_czech(bold) else 'NE'})") if bold \
        else "CHYBÍ: nainstaluj DejaVu nebo vlož TTF do assets/fonts/"

    # 3. ffmpeg
    from .media import ffmpeg as ff

    try:
        info = ff.available()
        checks["ffmpeg"] = (f"OK: {info['version']}" if info["ok"] else "CHYBA")
        checks["ffmpeg_detaily"] = {k: info.get(k) for k in
                                    ("binary", "ffprobe", "drawtext", "zoompan", "xfade")}
    except IgAgentError as exc:
        checks["ffmpeg"] = f"CHYBÍ: {exc}"

    # 4. Instagram
    try:
        settings.require_instagram()
        from .instagram import GraphClient

        client = GraphClient(settings)
        account = client.account()
        checks["instagram"] = (f"OK: @{account.get('username')} — "
                               f"{account.get('followers_count')} sledujících, "
                               f"{account.get('media_count')} příspěvků")
        try:
            checks["publikacni_limit"] = client.publishing_limit()
        except IgAgentError as exc:
            checks["publikacni_limit"] = f"nezjištěno: {exc}"
    except Exception as exc:  # noqa: BLE001 - doctor nikdy nespadne
        checks["instagram"] = f"CHYBA: {exc}"

    # 5. hosting médií
    try:
        settings.require_media_host()
        checks["hosting_medii"] = f"OK: {settings.media_host} → {settings.media_public_base or '(předpodepsané URL)'}"
    except IgAgentError as exc:
        checks["hosting_medii"] = f"CHYBA: {exc}"

    # 6. Claude
    try:
        settings.require_claude()
        checks["claude"] = f"OK: klíč nastaven, model {settings.model}"
    except IgAgentError as exc:
        checks["claude"] = f"CHYBA: {exc}"

    # 7. databáze
    from .store import Store

    store = Store(settings.db_path)
    checks["databaze"] = {
        "cesta": str(settings.db_path),
        "prispevku": len(store.posts(limit=1000)),
        "ve_fronte": len(store.queue(limit=500)),
        "strategie": bool(store.latest_strategy()),
    }
    store.close()

    _print(report)
    problems = [k for k, v in checks.items() if isinstance(v, str) and
                (v.startswith("CHYB") or v.startswith("CHYBÍ"))]
    if problems:
        print(f"\n⚠️  Nedořešeno: {', '.join(problems)}", file=sys.stderr)
        return 1
    print("\n✅ Vše připravené.")
    return 0


def cmd_run(args):
    agent = _agent(args)
    try:
        summary = agent.run_cycle(do_plan=not args.no_plan, do_produce=not args.no_produce,
                                  do_publish=not args.no_publish, do_report=not args.no_report)
        _print(summary)
        return 1 if summary.get("errors") else 0
    finally:
        agent.close()


def cmd_collect(args):
    agent = _agent(args)
    try:
        _print(agent.step_collect())
        return 0
    finally:
        agent.close()


def cmd_learn(args):
    agent = _agent(args)
    try:
        profile = agent.step_learn()
        _print(dict(profile))
        return 0
    finally:
        agent.close()


def cmd_strategy(args):
    agent = _agent(args)
    try:
        profile = agent.learner.current_profile()
        if args.raw:
            _print(dict(profile))
            return 0
        print(_format_strategy(profile))
        return 0
    finally:
        agent.close()


def cmd_plan(args):
    agent = _agent(args)
    try:
        items = agent.planner.plan(count=args.count, days=args.days, notes=args.notes)
        for item in items:
            print(f"#{item.id:<4} {_local(item.scheduled_for, agent.settings):<18} "
                  f"{item.pillar or item.format:<30} {item.title}")
        if not items:
            print("Fronta je plná — nic nového neplánuji.")
        return 0
    finally:
        agent.close()


def cmd_queue(args):
    agent = _agent(args)
    store = agent.store
    try:
        if args.queue_command == "list":
            items = store.queue(status=args.status.split(",") if args.status else None, limit=200)
            if not items:
                print("Fronta je prázdná.")
                return 0
            for item in items:
                flag = {"planned": "·", "produced": "○", "approved": "●",
                        "published": "✓", "failed": "✗", "skipped": "-"}.get(item.status, "?")
                marks = f"{item.format}"
                if item.language and item.language != "cs":
                    marks += f"/{item.language}"
                if item.variant:
                    marks += f" ↻{item.variant}"
                print(f"{flag} #{item.id:<4} {item.status:<10} "
                      f"{_local(item.scheduled_for, agent.settings):<18} "
                      f"{(item.series or '—')[:14]:<15} {marks:<16} {item.title}")
                if item.error:
                    print(f"      ↳ {item.error}")
            return 0

        if args.queue_command == "show":
            item = store.get_queue_item(args.id)
            if not item:
                print(f"#{args.id} neexistuje.", file=sys.stderr)
                return 1
            _print(item.__dict__)
            return 0

        item = store.get_queue_item(args.id)
        if not item:
            print(f"#{args.id} neexistuje.", file=sys.stderr)
            return 1

        if args.queue_command == "approve":
            item.status = "approved"
            store.update_queue(item)
            print(f"#{item.id} schváleno k publikaci ({item.scheduled_for or 'bez času'}).")
        elif args.queue_command == "reject":
            item.status = "skipped"
            store.update_queue(item)
            print(f"#{item.id} vyřazeno.")
        elif args.queue_command == "attach":
            files = [str(Path(f).expanduser().resolve()) for f in args.files]
            missing = [f for f in files if not Path(f).exists()]
            if missing:
                print(f"Tyhle soubory neexistují: {missing}", file=sys.stderr)
                return 1
            item.source_media = (item.source_media or []) + files
            item.status = "planned"
            store.update_queue(item)
            print(f"#{item.id}: připojeno {len(files)} souborů.")
        elif args.queue_command == "reschedule":
            item.scheduled_for = args.when
            store.update_queue(item)
            print(f"#{item.id} přeplánováno na {args.when}.")
        return 0
    finally:
        agent.close()


def cmd_produce(args):
    agent = _agent(args)
    try:
        if args.id:
            item = agent.store.get_queue_item(args.id)
            if not item:
                print(f"#{args.id} neexistuje.", file=sys.stderr)
                return 1
            items = [agent.producer.produce(item, qa=not args.no_qa)]
        else:
            items = agent.step_produce(limit=args.limit)
        for item in items:
            files = (item.assets or {}).get("files", [])
            print(f"#{item.id} → {item.status}: {len(files)} souborů")
            for f in files:
                print(f"    {f}")
            if item.caption:
                print(f"    popisek: {item.caption[:120]}…")
            if item.error:
                print(f"    chyba: {item.error}")
        return 0
    finally:
        agent.close()


def cmd_publish(args):
    agent = _agent(args, dry_run=args.dry_run)
    try:
        if args.id:
            item = agent.store.get_queue_item(args.id)
            if not item:
                print(f"#{args.id} neexistuje.", file=sys.stderr)
                return 1
            result = agent.queue_publisher.publish_item(item, force=args.force)
            _print({"media_id": result.media_id, "permalink": result.permalink,
                    "dry_run": result.dry_run})
        else:
            results = agent.queue_publisher.publish_due(limit=args.limit)
            _print([{"media_id": r.media_id, "permalink": r.permalink,
                     "dry_run": r.dry_run} for r in results])
        return 0
    finally:
        agent.close()


def cmd_analyze(args):
    agent = _agent(args)
    try:
        result = agent.write_report(days=args.days)
        print(Path(result["markdown"]).read_text(encoding="utf-8"))
        print(f"\n(uloženo: {result['markdown']})")
        return 0
    finally:
        agent.close()


def cmd_reel(args):
    settings = _settings(args)
    from .media.video import ReelStudio

    studio = ReelStudio(settings.brand, settings.work_dir, settings.out_dir)
    hook = {"text": args.hook, "end": 2.6} if args.hook else None
    if args.jumpcut:
        out = studio.jumpcut(args.video, hook=hook, music=args.music, fit=args.fit,
                             max_duration=args.seconds, name=args.name)
    else:
        out = studio.build(args.video, target_duration=args.seconds, fit=args.fit,
                           hook=hook, music=args.music, speed=args.speed, name=args.name)
    # obálku stavíme ze zdrojového videa, ne z hotového Reelu (ten už má handle)
    cover_name = args.name or Path(args.video).stem
    cover = studio.branded_cover(args.video, args.cover_title or (args.hook or ""),
                                 name=cover_name) \
        if args.cover_title or args.hook else studio.cover(out, name=cover_name)
    print(f"Reel:   {out}")
    print(f"Obálka: {cover}")
    return 0


def cmd_photos(args):
    settings = _settings(args)
    from .media.photos import PhotoStudio

    studio = PhotoStudio(settings.brand, settings.out_dir)
    if args.reel:
        from .media.video import ReelStudio

        reels = ReelStudio(settings.brand, settings.work_dir, settings.out_dir)
        out = reels.from_photos(args.photos, seconds_each=args.seconds_each,
                                music=args.music, fit=args.fit_video,
                                hook={"text": args.hook, "end": 2.6} if args.hook else None)
        print(f"Reel z fotek: {out}")
        return 0
    paths = studio.prepare_many(args.photos, ratio=args.ratio, fit=args.fit)
    for path in paths:
        print(path)
    return 0


def cmd_graphic(args):
    settings = _settings(args)
    from .media.graphics import GraphicsStudio

    studio = GraphicsStudio(settings.brand, settings.out_dir)
    if args.kind == "quote":
        path = studio.quote(args.text, kicker=args.kicker, name=args.name)
    elif args.kind == "tips":
        path = studio.tip_list(args.text, args.items or [], kicker=args.kicker, name=args.name)
    elif args.kind == "stat":
        path = studio.stat(args.value, args.text, args.context, name=args.name)
    else:
        path = studio.cover(args.text, args.subtitle, args.kicker, name=args.name,
                            background=args.background)
    print(path)
    return 0


def cmd_comments(args):
    agent = _agent(args)
    try:
        if args.comments_command == "collect":
            print(f"Uloženo komentářů: {agent.collector.collect_comments()}")
            return 0
        pending = agent.store.unreplied_comments(limit=args.limit)
        if not pending:
            print("Žádné nové komentáře.")
            return 0
        drafts = agent.brain.draft_comment_replies(pending,
                                                   strategy=dict(agent.learner.current_profile()))
        for reply in drafts.get("replies", []):
            marker = {"odpovedet": "→", "ignorovat": "·", "eskalovat": "!"}.get(
                reply.get("action"), "?")
            source = next((c for c in pending if c["comment_id"] == reply["comment_id"]), {})
            print(f"{marker} @{source.get('username', '?')}: {source.get('text', '')[:80]}")
            print(f"    návrh: {reply.get('reply')}")
            print(f"    ({reply.get('action')} — {reply.get('reason')})\n")
        print("Odpovědi se neodesílají automaticky. Pošli konkrétní: "
              "`igagent comments send <comment_id> \"text\"`")
        return 0
    finally:
        agent.close()


def cmd_comment_send(args):
    agent = _agent(args)
    try:
        agent.client.reply_to_comment(args.comment_id, args.text)
        agent.store.mark_replied(args.comment_id, args.text)
        print("Odesláno.")
        return 0
    finally:
        agent.close()


def cmd_token(args):
    settings = _settings(args)
    from .instagram import GraphClient

    client = GraphClient(settings)
    if args.token_command == "info":
        _print(client.debug_token())
        return 0
    if not (settings.fb_app_id and settings.fb_app_secret):
        print("Pro výměnu tokenu doplň FB_APP_ID a FB_APP_SECRET do .env.", file=sys.stderr)
        return 1
    result = client.exchange_long_lived_token(settings.fb_app_id, settings.fb_app_secret,
                                              args.short_token)
    print("Dlouhodobý token (platnost ~60 dní) — přepiš ho v .env jako IG_ACCESS_TOKEN:\n")
    print(result.get("access_token", ""))
    return 0


def cmd_seed(args):
    agent = _agent(args)
    try:
        created, skipped = agent.seeder.run(path=args.file, days=args.days)
        for item in created:
            print(f"#{item.id:<4} {_local(item.scheduled_for, agent.settings):<18} "
                  f"{item.pillar:<30} {item.title}")
        if skipped:
            print(f"\nPřeskočeno (už ve frontě): {len(skipped)}")
        if created:
            missing = [i for i in created if i.brief.get("needs_user_media")]
            if missing:
                print(f"\n{len(missing)} námětů čeká na tvoje video. Až natočíš:")
                print(f"    igagent queue attach {created[0].id} ~/video.mp4")
                print("    igagent produce")
        return 0
    finally:
        agent.close()


def cmd_repurpose(args):
    agent = _agent(args)
    try:
        if args.status:
            every = agent.settings.brand.repurpose_every or 10
            since = agent.repurposer.published_since_last()
            print(f"Od poslední recyklace vyšlo {since} videí (práh {every}).")
            print(f"Kol recyklace zatím: {agent.repurposer.repurpose_rounds_done()}")
            winners = agent.repurposer.candidates()
            if winners:
                print("\nNejlepší dosud nerecyklované náměty:")
                for post in winners[:5]:
                    print(f"  skóre {post['score']:>6.0f}  {post['media_id']:<20} "
                          f"{(post.get('caption') or '')[:60]}")
            else:
                print("\nZatím žádný námět nepřekonal práh pro recyklaci.")
            return 0

        created = agent.step_repurpose(count=args.count, force=args.force)
        if not created:
            print("Nic k recyklaci. Stav zjistíš přes `igagent repurpose --status`.")
            return 0
        for item in created:
            print(f"#{item.id:<4} {item.variant:<12} {item.language}  {item.title}")
            print(f"      z: {item.repurposed_from}  ·  {item.brief.get('angle', '')[:80]}")
        return 0
    finally:
        agent.close()


def cmd_kpi(args):
    """Tabulka KPI po jednotlivých videích — to, co se má sledovat místo views."""
    from .analytics.metrics import build_baselines, score_breakdown, summarize

    agent = _agent(args)
    try:
        rows = agent.store.posts_with_latest_metrics(limit=args.limit)
        measured = [r for r in rows if r.get("reach")]
        if not measured:
            print("Zatím nejsou změřená data. Spusť `igagent collect`.")
            return 0

        baselines = build_baselines(measured)
        print(f"{'datum':<11}{'série':<16}{'dosah':>7}{'follow/1k':>11}"
              f"{'sdíl/1k':>9}{'ulož/1k':>9}{'dokouk.':>9}{'skóre':>8}")
        print("-" * 80)
        for row in measured[:args.limit]:
            detail = score_breakdown(row, baselines, row.get("duration_seconds"))
            values = {c["kpi"]: c["hodnota"] for c in detail["složky"]}
            print(f"{(row.get('published_at') or '')[:10]:<11}"
                  f"{(row.get('series') or '—')[:15]:<16}"
                  f"{row.get('reach') or 0:>7}"
                  f"{_num(values.get('follow_rate')):>11}"
                  f"{_num(values.get('share_rate')):>9}"
                  f"{_num(values.get('save_rate')):>9}"
                  f"{_pct(values.get('watch_through')):>9}"
                  f"{_num(detail['skóre'], 0):>8}")

        print()
        summary = summarize(measured, agent.store.latest_followers())
        for key, value in summary.items():
            print(f"  {key:<26} {value}")
        print()
        print("  Pozn.: „udržení prvních tří sekund“ Graph API nedává.")
        print("  Nejbližší dostupná náhrada je poměr zhlédnutí k dosahu — do skóre")
        print("  vstupuje, ale není to totéž číslo jako retence v aplikaci.")
        return 0
    finally:
        agent.close()


def cmd_kalendar(args):
    agent = _agent(args)
    try:
        rows = agent.planner.scheduler.next_week_overview(days=args.days)
        if not rows:
            print("Žádné volné termíny sérií — fronta je na tohle období plná.")
            return 0
        queue = {item.scheduled_for: item for item in agent.store.queue(limit=200)}
        print(f"{'kdy':<20}{'série':<32}{'stav'}")
        print("-" * 72)
        for row in rows:
            iso = row["kdy"].astimezone(dt_timezone.utc).isoformat()
            item = queue.get(iso)
            state = f"#{item.id} {item.status}" if item else "volné"
            flag = " ← potřebuje aktuální moment" if row["aktualni_moment"] else ""
            print(f"{row['kdy']:%a %d.%m. %H:%M}      {row['serie']:<32}{state}{flag}")
        moments = agent.planner.pending_moments()
        print(f"\nZadané momenty k rozboru: {len(moments)}")
        for moment in moments[:5]:
            print(f"  · {moment}")
        return 0
    finally:
        agent.close()


def cmd_moment(args):
    """Momenty pro sérii, která reaguje na aktuální dění."""
    settings = _settings(args)
    path = Path(settings.data_dir) / "moments.txt"
    path.parent.mkdir(parents=True, exist_ok=True)

    if args.moment_command == "add":
        with path.open("a", encoding="utf-8") as handle:
            handle.write(args.text.strip() + "\n")
        print("Přidáno. Agent to použije při nejbližším plánování série "
              "„Co se právě stalo“.")
        return 0

    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()] if path.exists() else []
    if args.moment_command == "list":
        if not lines:
            print("Žádné momenty. Přidej: igagent moment add 'popis momentu'")
        for index, line in enumerate(lines, 1):
            print(f"{index}. {line}")
        return 0

    if args.moment_command == "clear":
        path.write_text("", encoding="utf-8")
        print("Seznam momentů vyprázdněn.")
        return 0
    return 0


def _num(value, decimals=1):
    return "—" if value is None else f"{value:.{decimals}f}"


def _pct(value):
    return "—" if value is None else f"{100 * value:.0f}%"


def _format_strategy(profile):
    lines = [f"Strategie · vzorek {profile.get('sample_size', 0)} měřených příspěvků"]
    if profile.get("warning"):
        lines.append(f"⚠️  {profile['warning']}")
    lines.append("")
    if profile.get("best_hours"):
        lines.append(f"Nejlepší hodiny:  {', '.join(f'{h}:00' for h in profile['best_hours'])}")
    if profile.get("best_weekdays"):
        lines.append(f"Nejlepší dny:     {', '.join(profile['best_weekdays'])}")
    if profile.get("format_mix"):
        mix = ", ".join(f"{k} {v:.0%}" for k, v in profile["format_mix"].items())
        lines.append(f"Poměr formátů:    {mix}")
    caption = (profile.get("caption_length") or {}).get("nejlepsi")
    if caption:
        lines.append(f"Délka popisku:    {caption}")
    for label, key in (("Dělej víc", "do_more"), ("Dělej míň", "do_less")):
        rows = profile.get(key) or []
        if rows:
            lines.append(f"\n{label}:")
            for row in rows:
                lines.append(f"  · {row['vlastnost']} = {row['hodnota']}  "
                             f"(odhad {row['odhad']}, vzorek {row['vzorek']})")
    rankings = profile.get("rankings") or {}
    for feature in ("format", "topic", "hook_style"):
        rows = rankings.get(feature) or []
        if rows:
            lines.append(f"\n{feature}:")
            for row in rows[:5]:
                lines.append(f"  {row['value']:<18} odhad {row['estimate']:>6}  "
                             f"(n={row['n']}, priorita {row['priority']})")
    return "\n".join(lines)


# ====================================================================== parser

def build_parser():
    parser = argparse.ArgumentParser(
        prog="igagent", description="Autonomní agent pro Instagram",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--version", action="version", version=f"igagent {__version__}")
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    parser.add_argument("--autopilot", choices=("off", "review", "full"),
                        help="Přebije nastavení z .env pro tento běh")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Zkontroluje nastavení").set_defaults(func=cmd_doctor)

    run = sub.add_parser("run", help="Jeden autonomní cyklus")
    run.add_argument("--no-plan", action="store_true")
    run.add_argument("--no-produce", action="store_true")
    run.add_argument("--no-publish", action="store_true")
    run.add_argument("--no-report", action="store_true")
    run.add_argument("--dry-run", action="store_true", help="Nic se neodešle na Instagram")
    run.set_defaults(func=cmd_run)

    sub.add_parser("collect", help="Stáhne data z Instagramu").set_defaults(func=cmd_collect)
    sub.add_parser("learn", help="Přepočítá strategii").set_defaults(func=cmd_learn)

    strategy = sub.add_parser("strategy", help="Co se agent naučil")
    strategy.add_argument("--raw", action="store_true", help="Vypíše celý JSON")
    strategy.set_defaults(func=cmd_strategy)

    plan = sub.add_parser("plan", help="Naplánuje obsah")
    plan.add_argument("-n", "--count", type=int, default=None)
    plan.add_argument("--notes", default=None, help="Kontext pro období (akce, svátky…)")
    plan.add_argument("--days", type=int, default=None, help="Horizont plánování")
    plan.set_defaults(func=cmd_plan)

    queue = sub.add_parser("queue", help="Práce s frontou")
    qsub = queue.add_subparsers(dest="queue_command", required=True)
    qlist = qsub.add_parser("list")
    qlist.add_argument("--status", default=None, help="např. planned,produced")
    qsub.add_parser("show").add_argument("id", type=int)
    qsub.add_parser("approve").add_argument("id", type=int)
    qsub.add_parser("reject").add_argument("id", type=int)
    attach = qsub.add_parser("attach", help="Připojí fotky/video k položce")
    attach.add_argument("id", type=int)
    attach.add_argument("files", nargs="+")
    resched = qsub.add_parser("reschedule")
    resched.add_argument("id", type=int)
    resched.add_argument("when", help="ISO čas, např. 2026-09-20T18:00:00+02:00")
    queue.set_defaults(func=cmd_queue)

    produce = sub.add_parser("produce", help="Vyrobí média a texty")
    produce.add_argument("--id", type=int, default=None)
    produce.add_argument("--limit", type=int, default=3)
    produce.add_argument("--no-qa", action="store_true", help="Přeskočí vizuální kontrolu")
    produce.set_defaults(func=cmd_produce)

    publish = sub.add_parser("publish", help="Publikuje na Instagram")
    publish.add_argument("--id", type=int, default=None)
    publish.add_argument("--limit", type=int, default=2)
    publish.add_argument("--dry-run", action="store_true")
    publish.add_argument("--force", action="store_true", help="Obejde pojistky (opatrně)")
    publish.set_defaults(func=cmd_publish)

    seed = sub.add_parser("seed", help="Nasadí startovní dávku námětů do fronty")
    seed.add_argument("--file", default="config/seed-first-8.yaml")
    seed.add_argument("--days", type=int, default=60, help="Do kolika dní hledat termíny")
    seed.set_defaults(func=cmd_seed)

    repurpose = sub.add_parser("repurpose", help="Znovu zpracuje vítězné náměty")
    repurpose.add_argument("-n", "--count", type=int, default=2)
    repurpose.add_argument("--force", action="store_true", help="Nečekej na práh")
    repurpose.add_argument("--status", action="store_true", help="Jen ukaž stav")
    repurpose.set_defaults(func=cmd_repurpose)

    kpi = sub.add_parser("kpi", help="Tabulka KPI po videích (místo views)")
    kpi.add_argument("--limit", type=int, default=30)
    kpi.set_defaults(func=cmd_kpi)

    kalendar = sub.add_parser("kalendar", help="Co kdy natočit podle sérií")
    kalendar.add_argument("--days", type=int, default=14)
    kalendar.set_defaults(func=cmd_kalendar)

    moment = sub.add_parser("moment", help="Momenty pro sérii „Co se právě stalo“")
    msub = moment.add_subparsers(dest="moment_command", required=True)
    add_moment = msub.add_parser("add")
    add_moment.add_argument("text")
    msub.add_parser("list")
    msub.add_parser("clear")
    moment.set_defaults(func=cmd_moment)

    analyze = sub.add_parser("analyze", help="Analýza profilu")
    analyze.add_argument("--days", type=int, default=28)
    analyze.set_defaults(func=cmd_analyze)

    reel = sub.add_parser("reel", help="Střih Reelu z videa")
    reel.add_argument("video")
    reel.add_argument("--seconds", type=float, default=28.0, help="Cílová délka")
    reel.add_argument("--hook", default=None, help="Text do prvních vteřin")
    reel.add_argument("--cover-title", default=None)
    reel.add_argument("--music", default=None, help="Cesta k hudbě (jen s právy!)")
    reel.add_argument("--fit", choices=("crop", "blur"), default="crop")
    reel.add_argument("--speed", type=float, default=1.0)
    reel.add_argument("--jumpcut", action="store_true", help="Vyhodí ticho")
    reel.add_argument("--name", default=None)
    reel.set_defaults(func=cmd_reel)

    photos = sub.add_parser("photos", help="Fotky → příspěvek / karusel / Reel")
    photos.add_argument("photos", nargs="+")
    photos.add_argument("--ratio", choices=("portrait", "square", "landscape", "story"),
                        default="portrait")
    photos.add_argument("--fit", choices=("crop", "pad"), default="crop")
    photos.add_argument("--reel", action="store_true", help="Udělá z fotek Reel")
    photos.add_argument("--seconds-each", type=float, default=2.6)
    photos.add_argument("--music", default=None)
    photos.add_argument("--hook", default=None)
    photos.add_argument("--fit-video", choices=("crop", "blur"), default="crop",
                        help="Jen s --reel: crop vyplní formát, blur nic neuřízne")
    photos.set_defaults(func=cmd_photos)

    graphic = sub.add_parser("graphic", help="Jednotlivá grafika")
    graphic.add_argument("kind", choices=("quote", "tips", "stat", "cover"))
    graphic.add_argument("text")
    graphic.add_argument("--items", nargs="*", default=None)
    graphic.add_argument("--kicker", default=None)
    graphic.add_argument("--subtitle", default=None)
    graphic.add_argument("--value", default=None, help="Pro `stat`: velké číslo")
    graphic.add_argument("--context", default=None)
    graphic.add_argument("--background", default=None)
    graphic.add_argument("--name", default=None)
    graphic.set_defaults(func=cmd_graphic)

    comments = sub.add_parser("comments", help="Komentáře")
    csub = comments.add_subparsers(dest="comments_command", required=True)
    csub.add_parser("collect")
    draft = csub.add_parser("draft")
    draft.add_argument("--limit", type=int, default=20)
    send = csub.add_parser("send")
    send.add_argument("comment_id")
    send.add_argument("text")
    send.set_defaults(func=cmd_comment_send)
    comments.set_defaults(func=cmd_comments)

    token = sub.add_parser("token", help="Správa přístupového tokenu")
    tsub = token.add_subparsers(dest="token_command", required=True)
    tsub.add_parser("info")
    exchange = tsub.add_parser("exchange", help="Krátkodobý → dlouhodobý token")
    exchange.add_argument("--short-token", default=None)
    token.set_defaults(func=cmd_token)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    # podpříkaz `comments send` má vlastní funkci
    if getattr(args, "comments_command", None) == "send":
        args.func = cmd_comment_send
    try:
        return args.func(args) or 0
    except IgAgentError as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nPřerušeno.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
