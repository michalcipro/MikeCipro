"""Celý cyklus agenta proti napodobeninám — plán → výroba → publikace → učení."""

import datetime as dt

import pytest
from PIL import Image

from igagent.errors import PublishBlocked
from igagent.pipeline import Agent
from igagent.util import parse_iso, to_iso, utcnow
from tests.fakes import FakeBrain, FakeGraphClient


@pytest.fixture
def agent(settings, store):
    settings.qa_images = False
    client = FakeGraphClient()
    agent = Agent(settings, store=store, client=client, brain=FakeBrain())
    agent._fake_client = client
    return agent


def _photo(path, size=(2000, 1500)):
    Image.new("RGB", size, "teal").save(path)
    return str(path)


def test_planner_fills_queue_and_respects_limits(agent, settings):
    items = agent.step_plan(count=6)
    assert len(items) == 6
    assert all(i.status == "planned" for i in items)

    slots = [parse_iso(i.scheduled_for) for i in items if i.scheduled_for]
    assert slots, "položky musí dostat čas publikace"

    by_day = {}
    for slot in slots:
        by_day.setdefault(slot.date(), []).append(slot)
    for day_slots in by_day.values():
        assert len(day_slots) <= settings.max_posts_per_day
        ordered = sorted(day_slots)
        gaps = [(b - a).total_seconds() / 3600 for a, b in zip(ordered, ordered[1:])]
        assert all(gap >= settings.min_hours_between_posts for gap in gaps)


def test_planner_stops_when_queue_is_full(agent):
    agent.step_plan(count=6)
    assert agent.step_plan() == []


def test_producer_builds_image_post(agent, tmp_path):
    items = agent.step_plan(count=3)
    image_item = next(i for i in items if i.format == "IMAGE")
    produced = agent.producer.produce(image_item)

    assert produced.status == "produced"
    files = produced.assets["files"]
    assert len(files) == 1 and Image.open(files[0]).size == (1080, 1350)
    assert produced.caption and produced.hashtags


def test_producer_builds_carousel(agent):
    items = agent.step_plan(count=3)
    carousel = next(i for i in items if i.format == "CAROUSEL")
    produced = agent.producer.produce(carousel)
    assert 2 <= len(produced.assets["files"]) <= 10


def test_reel_without_media_fails_with_a_useful_message(agent):
    items = agent.step_plan(count=3)
    reel = next(i for i in items if i.format == "REEL")
    reel.source_media = []
    produced = agent.producer.produce(reel)
    assert produced.status == "failed"
    assert "inbox" in produced.error


def test_review_mode_blocks_publishing_until_approved(agent, tmp_path):
    items = agent.step_plan(count=3)
    item = agent.producer.produce(next(i for i in items if i.format == "IMAGE"))
    item.scheduled_for = to_iso(utcnow() - dt.timedelta(minutes=5))
    agent.store.update_queue(item)

    assert agent.step_publish() == []          # čeká na schválení

    item.status = "approved"
    agent.store.update_queue(item)
    results = agent.step_publish()
    assert len(results) == 1 and results[0].media_id


def test_autopilot_off_refuses_to_publish(agent):
    agent.settings.autopilot = "off"
    items = agent.step_plan(count=3)
    item = agent.producer.produce(next(i for i in items if i.format == "IMAGE"))
    item.status = "approved"
    agent.store.update_queue(item)
    with pytest.raises(PublishBlocked):
        agent.queue_publisher.publish_item(item)


def test_daily_limit_is_enforced(agent):
    agent.settings.autopilot = "full"
    items = agent.step_plan(count=6)
    published = 0
    for item in items:
        if item.format == "REEL":
            continue
        produced = agent.producer.produce(item)
        if produced.status == "failed":
            continue
        produced.status = "approved"
        produced.scheduled_for = to_iso(utcnow() - dt.timedelta(minutes=1))
        agent.store.update_queue(produced)
        try:
            agent.queue_publisher.publish_item(produced)
            published += 1
        except PublishBlocked:
            break
    assert published == agent.settings.max_posts_per_day


def test_published_post_is_remembered_with_its_features(agent):
    agent.settings.autopilot = "full"
    items = agent.step_plan(count=3)
    item = agent.producer.produce(next(i for i in items if i.format == "IMAGE"))
    item.status = "approved"
    agent.store.update_queue(item)
    result = agent.queue_publisher.publish_item(item)

    post = agent.store.get_post(result.media_id)
    assert post["created_by"] == "agent"
    assert post["topic"] == item.topic
    assert post["hook_style"] == item.hook_style
    assert post["local_hour"] is not None


def test_full_cycle_closes_the_learning_loop(agent):
    """Publikuj → změř → nauč se: strategie musí vzniknout z reálných dat."""
    agent.settings.autopilot = "full"
    client = agent._fake_client

    items = agent.step_plan(count=6)
    for item in items:
        if item.format == "REEL":
            continue
        produced = agent.producer.produce(item)
        if produced.status == "failed":
            continue
        produced.status = "approved"
        produced.scheduled_for = to_iso(utcnow() - dt.timedelta(minutes=1))
        agent.store.update_queue(produced)
        try:
            result = agent.queue_publisher.publish_item(produced)
        except PublishBlocked:
            break
        # simuluj výkon a posuň publikaci do minulosti, ať se dá měřit
        client.simulate_performance(result.media_id, produced.format, 18)
        agent.store.upsert_post({
            "media_id": result.media_id,
            "published_at": to_iso(utcnow() - dt.timedelta(days=2))})

    assert client.published, "něco se muselo publikovat"

    collected = agent.collector.collect_metrics(min_age_hours=1, max_age_days=30)
    assert collected > 0

    profile = agent.step_learn()
    assert profile.sample_size > 0
    assert profile["rankings"]["format"]


def test_run_cycle_survives_a_broken_step(agent):
    def boom():
        raise RuntimeError("API spadlo")

    agent.step_collect = boom
    summary = agent.run_cycle(do_publish=False, do_report=False)
    assert "collect" in summary["errors"]
    assert "learn" in summary["steps"]        # zbytek cyklu běžel dál
