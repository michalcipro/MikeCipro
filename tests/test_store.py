from igagent.store import QueueItem


def test_queue_roundtrip(store):
    item = store.enqueue(QueueItem(format="REEL", topic="návyky", title="Test",
                                   brief={"angle": "a"}, hashtags=["#x"]))
    assert item.id

    loaded = store.get_queue_item(item.id)
    assert loaded.format == "REEL"
    assert loaded.brief == {"angle": "a"}
    assert loaded.hashtags == ["#x"]

    loaded.status = "produced"
    store.update_queue(loaded)
    assert store.get_queue_item(item.id).status == "produced"
    assert [i.id for i in store.queue(status="produced")] == [item.id]


def test_upsert_post_preserves_known_fields(store):
    store.upsert_post({"media_id": "m1", "format": "IMAGE", "topic": "návyky",
                       "published_at": "2026-09-01T10:00:00+00:00"})
    # pozdější sync z API téma nezná — nesmí ho přepsat na NULL
    store.upsert_post({"media_id": "m1", "format": "IMAGE", "caption": "ahoj"})
    post = store.get_post("m1")
    assert post["topic"] == "návyky"
    assert post["caption"] == "ahoj"


def test_feature_stats_accumulate(store):
    store.bump_feature("format", "REEL", 120.0)
    store.bump_feature("format", "REEL", 80.0)
    row = store.feature_stats("format")[0]
    assert row["n"] == 2
    assert row["score_sum"] == 200.0
    assert row["score_sq"] == 120.0 ** 2 + 80.0 ** 2
