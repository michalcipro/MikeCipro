from igagent.analytics.metrics import (build_baselines, engagement_rate,
                                       normalize_media_insights, score_post,
                                       watch_through, weighted_interactions)


def test_normalize_maps_api_names():
    raw = {"reach": 1000, "saved": 12, "views": 4000, "ig_reels_avg_watch_time": 5200}
    out = normalize_media_insights(raw, {"like_count": 50, "comments_count": 4})
    assert out["saves"] == 12
    assert out["plays"] == 4000
    assert out["avg_watch_time"] == 5200
    assert out["likes"] == 50 and out["comments"] == 4


def test_weights_favor_saves_and_shares():
    assert weighted_interactions({"likes": 10}) == 10
    assert weighted_interactions({"saves": 10}) == 30
    assert weighted_interactions({"shares": 10}) == 40


def test_median_post_scores_100():
    rows = [{"reach": r, "likes": r // 20, "age_hours": 48} for r in (500, 1000, 2000)]
    baselines = build_baselines(rows)
    assert score_post(rows[1], baselines) == 100.0
    assert score_post(rows[2], baselines) > 150
    assert score_post(rows[0], baselines) < 60


def test_score_without_history_uses_followers():
    score = score_post({"reach": 300, "likes": 15, "age_hours": 24}, None, followers=1000)
    assert score is not None and score > 0


def test_score_is_capped():
    rows = [{"reach": 100, "likes": 1, "age_hours": 48}] * 3
    baselines = build_baselines(rows)
    assert score_post({"reach": 10 ** 7, "likes": 10 ** 6, "age_hours": 48}, baselines) == 250.0


def test_watch_through_handles_milliseconds():
    assert watch_through({"avg_watch_time": 7500}, 15) == 50.0
    assert watch_through({"avg_watch_time": 7.5}, 15) == 50.0
    assert watch_through({}, 15) is None


def test_engagement_rate():
    assert engagement_rate({"reach": 1000, "likes": 80, "comments": 20}) == 10.0
