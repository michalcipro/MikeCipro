"""Série, týdenní režim, startovní dávka a recyklace vítězů."""

import datetime as dt

import pytest
import yaml

from igagent.config import Brand, ConfigError, Series
from igagent.pipeline import Agent
from igagent.pipeline.repurpose import MIN_SCORE_TO_REPURPOSE
from igagent.util import parse_iso, to_iso, utcnow
from tests.fakes import FakeBrain, FakeGraphClient

REAL_BRAND = "config/brand.yaml"
REAL_SEED = "config/seed-first-8.yaml"


# ---------------------------------------------------------------- konfigurace

def test_real_brand_kit_loads_with_four_series():
    brand = Brand.load(REAL_BRAND)
    assert set(brand.series) == {"tvrda_pravda", "udelej_to", "co_se_stalo", "z_terenu"}
    plan = brand.weekly_plan
    assert [plan[d].key for d in sorted(plan)] == [
        "tvrda_pravda", "udelej_to", "co_se_stalo", "z_terenu"]      # po, st, pá, ne
    assert all(s.format == "REEL" for s in brand.series.values())
    assert brand.series["co_se_stalo"].needs_timely_input is True


def test_two_series_on_one_day_is_rejected(tmp_path):
    path = tmp_path / "brand.yaml"
    path.write_text(yaml.safe_dump({
        "handle": "x",
        "series": {"a": {"name": "A", "weekday": "po"}, "b": {"name": "B", "weekday": "po"}},
    }), encoding="utf-8")
    with pytest.raises(ConfigError, match="pondělí"):
        Brand.load(path)


def test_weekday_accepts_czech_names_and_numbers():
    assert Series.from_raw("a", {"weekday": "ne"}).weekday == 6
    assert Series.from_raw("b", {"weekday": 2}).weekday == 2
    with pytest.raises(ConfigError, match="neznámý den"):
        Series.from_raw("c", {"weekday": "monday"})


def test_series_context_reaches_the_prompt():
    brand = Brand.load(REAL_BRAND)
    block = brand.prompt_block()
    assert "Tvrdá pravda o výkonu" in block
    assert "10sekundový reset po chybě" in block            # příklady námětů
    assert "nevymýšlej" in block                            # pravidlo pro reakční sérii


# ---------------------------------------------------------------- kalendář

@pytest.fixture
def series_settings(settings):
    settings.brand = Brand.load(REAL_BRAND)
    settings.qa_images = False
    return settings


@pytest.fixture
def agent(series_settings, store):
    client = FakeGraphClient()
    agent = Agent(series_settings, store=store, client=client, brain=FakeBrain())
    agent._fake_client = client
    return agent


def test_calendar_follows_the_weekly_rhythm(agent):
    slots = agent.planner.scheduler.series_slots(days=14)
    assert slots
    tz = agent.planner.scheduler.tz
    for iso_slot, series in slots:
        when = parse_iso(iso_slot).astimezone(tz)
        assert when.weekday() == series.weekday
        assert when.hour == series.hour


def test_planner_fills_each_slot_with_its_own_series(agent):
    items = agent.step_plan()
    assert items
    assert all(item.series for item in items)
    assert all(item.format == "REEL" for item in items)

    tz = agent.planner.scheduler.tz
    for item in items:
        series = agent.settings.brand.series_by_key(item.series)
        assert series is not None
        assert parse_iso(item.scheduled_for).astimezone(tz).weekday() == series.weekday

    # a série se do nabídky dostala i v promptu pro Claude
    plan_call = next(c for c in agent.brain.calls if c[0] == "plan")
    assert set(plan_call[2]) <= set(agent.settings.brand.series)


def test_planner_does_not_double_book_a_slot(agent):
    first = agent.step_plan()
    second = agent.step_plan()
    taken = {item.scheduled_for for item in first}
    assert all(item.scheduled_for not in taken for item in second)


def test_moments_are_passed_to_the_planner(agent, tmp_path):
    moments = agent.settings.data_dir / "moments.txt"
    moments.parent.mkdir(parents=True, exist_ok=True)
    moments.write_text("Sinner po chybě ve 4. gamu\n# komentář se ignoruje\n", encoding="utf-8")
    assert agent.planner.pending_moments() == ["Sinner po chybě ve 4. gamu"]


# ---------------------------------------------------------------- startovní dávka

def test_seed_spreads_the_first_eight_across_their_series(agent):
    created, skipped = agent.seeder.run(path=REAL_SEED)
    assert len(created) == 8
    assert not skipped

    tz = agent.planner.scheduler.tz
    by_series = {}
    for item in created:
        by_series.setdefault(item.series, []).append(item)
        series = agent.settings.brand.series_by_key(item.series)
        assert parse_iso(item.scheduled_for).astimezone(tz).weekday() == series.weekday
        assert item.brief["key_points"]
        assert item.brief["needs_user_media"] is True      # talking head chce tvoje video

    assert set(by_series) == {"tvrda_pravda", "udelej_to", "z_terenu"}
    # reakční série se dopředu neplánuje — potřebuje aktuální moment
    assert "co_se_stalo" not in by_series
    # termíny uvnitř série jdou po sobě, ne na jeden den
    for items in by_series.values():
        times = sorted(parse_iso(i.scheduled_for) for i in items)
        assert len({t.date() for t in times}) == len(times)


def test_seed_is_idempotent(agent):
    agent.seeder.run(path=REAL_SEED)
    created, skipped = agent.seeder.run(path=REAL_SEED)
    assert created == []
    assert len(skipped) == 8


def test_seed_rejects_unknown_series(agent, tmp_path):
    path = tmp_path / "seed.yaml"
    path.write_text(yaml.safe_dump([{"title": "X", "series": "neexistuje"}]), encoding="utf-8")
    with pytest.raises(ConfigError, match="neexistuje"):
        agent.seeder.run(path=path)


# ---------------------------------------------------------------- recyklace

def _publish_history(agent, count=12, winner_index=0):
    """Nasimuluje `count` publikovaných videí s realistickým rozptylem výkonu.

    `winner_index` je jasný vítěz; ostatní dostanou postupně klesající
    konverzi, takže část z nich překročí práh pro recyklaci a část ne —
    stejně jako ve skutečnosti.
    """
    for index in range(count):
        media_id = f"hist{index}"
        # gradient: první polovina nadprůměrná, druhá podprůměrná
        factor = 1.8 - 1.2 * (index / max(count - 1, 1))
        if index == winner_index:
            factor = 4.0
        agent.store.upsert_post({
            "media_id": media_id, "format": "REEL", "series": "tvrda_pravda",
            "topic": "sebevědomí", "caption": f"Video {index}",
            "language": "cs", "duration_seconds": 35.0,
            "published_at": to_iso(utcnow() - dt.timedelta(days=count - index)),
            "local_hour": 18, "local_weekday": 0})
        agent.store.add_metrics(media_id, {
            "reach": 2000, "follows": max(1, int(3 * factor)),
            "shares": max(1, int(8 * factor)), "saves": max(1, int(15 * factor)),
            "plays": 2800, "avg_watch_time": int(12000 * min(factor, 1.8)),
            "duration_seconds": 35.0, "age_hours": 72.0})
    agent.learner.run()


def test_repurpose_waits_for_the_threshold(agent):
    _publish_history(agent, count=4)
    assert agent.repurposer.is_due() is False
    assert agent.step_repurpose() == []


def test_repurpose_triggers_after_ten_videos(agent):
    _publish_history(agent, count=12)
    assert agent.repurposer.is_due() is True

    created = agent.step_repurpose(count=2)
    assert len(created) == 2
    assert all(item.repurposed_from for item in created)
    assert all(item.variant for item in created)
    # vybírá se vítěz, ne poslední video
    assert created[0].repurposed_from == "hist0"


def test_repurpose_picks_winners_by_score_not_reach(agent):
    _publish_history(agent, count=12, winner_index=5)
    winners = agent.repurposer.candidates()
    assert winners[0]["media_id"] == "hist5"
    assert winners[0]["score"] >= MIN_SCORE_TO_REPURPOSE


def test_repurpose_does_not_reuse_the_same_source_twice(agent):
    _publish_history(agent, count=12)
    first = agent.step_repurpose(count=2, force=True)
    used = {item.repurposed_from for item in first}
    second = agent.step_repurpose(count=2, force=True)
    assert all(item.repurposed_from not in used for item in second)


def test_every_fifth_round_produces_an_english_variant(agent):
    _publish_history(agent, count=40)
    english_rounds = []
    for _ in range(5):
        created = agent.step_repurpose(count=1, force=True)
        if not created:
            break
        english_rounds.append(created[0].language)
    assert "en" in english_rounds, f"anglická verze nepřišla: {english_rounds}"


def test_weak_posts_are_not_repurposed(agent):
    for index in range(12):
        media_id = f"weak{index}"
        agent.store.upsert_post({
            "media_id": media_id, "format": "REEL", "series": "tvrda_pravda",
            "published_at": to_iso(utcnow() - dt.timedelta(days=12 - index)),
            "duration_seconds": 35.0})
        agent.store.add_metrics(media_id, {
            "reach": 50000, "follows": 1, "shares": 1, "saves": 2,   # obrovský dosah…
            "plays": 60000, "avg_watch_time": 3000,                   # …a žádná konverze
            "duration_seconds": 35.0, "age_hours": 72.0})
    agent.learner.run()
    assert agent.repurposer.candidates() == []
    assert agent.step_repurpose(force=True) == []
