"""Sběr dat — včetně toho, že se agent učí i z ručně publikovaných příspěvků."""

from igagent.analytics import Collector
from tests.fakes import FakeGraphClient


def _collector(store, settings, client=None):
    return Collector(client or FakeGraphClient(), store, settings)


def test_account_snapshot_is_stored(store, settings):
    collector = _collector(store, settings)
    snapshot = collector.collect_account()
    assert snapshot["followers"] == 2000
    assert store.latest_followers() == 2000


def test_manual_posts_are_imported_and_marked_as_human(store, settings):
    client = FakeGraphClient()
    client.add_manual_post("hand1", fmt="REEL", caption="Z telefonu #navyky #test")
    collector = _collector(store, settings, client)

    collector.sync_media()
    post = store.get_post("hand1")
    assert post["created_by"] == "human"
    assert post["format"] == "REEL"
    assert post["hashtag_count"] == 2
    assert post["local_hour"] is not None


def test_sync_does_not_downgrade_agent_posts(store, settings):
    client = FakeGraphClient()
    client.add_manual_post("agent1", caption="Vyrobil agent")
    store.upsert_post({"media_id": "agent1", "format": "IMAGE", "topic": "návyky",
                       "created_by": "agent", "published_at": "2026-09-01T10:00:00+00:00"})

    _collector(store, settings, client).sync_media()
    post = store.get_post("agent1")
    assert post["created_by"] == "agent"       # sync nesmí přepsat autorství
    assert post["topic"] == "návyky"           # ani naučené vlastnosti


def test_metrics_are_collected_for_old_enough_posts(store, settings):
    client = FakeGraphClient()
    client.add_manual_post("old", hours_ago=48, reach=1500)
    client.add_manual_post("fresh", hours_ago=1, reach=100)
    collector = _collector(store, settings, client)
    collector.sync_media()

    assert collector.collect_metrics(min_age_hours=24, max_age_days=30) == 1
    assert store.latest_metrics("old")["reach"] == 1500
    assert store.latest_metrics("fresh") is None


def test_metrics_are_not_recollected_immediately(store, settings):
    client = FakeGraphClient()
    client.add_manual_post("p1", hours_ago=48)
    collector = _collector(store, settings, client)
    collector.sync_media()

    assert collector.collect_metrics(min_age_hours=24) == 1
    assert collector.collect_metrics(min_age_hours=24) == 0     # ještě je čerstvé


def test_full_collect_run(store, settings):
    client = FakeGraphClient()
    client.add_manual_post("a", hours_ago=72)
    result = _collector(store, settings, client).run()
    assert result["media"] == 1
    assert result["metrics"] == 1
    assert "account" in result
