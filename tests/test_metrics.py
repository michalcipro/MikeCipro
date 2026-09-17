"""Skórování: rozhoduje konverze dosahu, ne views."""

import pytest

from igagent.analytics.metrics import (COLD_START_RATES, build_baselines, engagement_rate,
                                       kpi_rates, normalize_media_insights, score_breakdown,
                                       score_post, seconds_of, summarize, watch_through,
                                       weighted_interactions)


def _post(reach=1000, follows=3, shares=8, saves=15, plays=1400, watch_ms=13500,
          duration=30, age=48):
    return {"reach": reach, "follows": follows, "shares": shares, "saves": saves,
            "plays": plays, "avg_watch_time": watch_ms, "duration_seconds": duration,
            "age_hours": age}


def _history(n=6):
    return [_post() for _ in range(n)]


# ---------------------------------------------------------------- normalizace

def test_normalize_maps_api_names():
    raw = {"reach": 1000, "saved": 12, "views": 4000, "ig_reels_avg_watch_time": 5200,
           "follows": 7, "shares": 9}
    out = normalize_media_insights(raw, {"like_count": 50, "comments_count": 4})
    assert out["saves"] == 12
    assert out["plays"] == 4000
    assert out["avg_watch_time"] == 5200
    assert out["follows"] == 7 and out["shares"] == 9
    assert out["likes"] == 50 and out["comments"] == 4


def test_watch_time_units_are_handled():
    assert seconds_of(13500) == 13.5        # milisekundy z API
    assert seconds_of(13.5) == 13.5         # už v sekundách
    assert seconds_of(None) is None


# ---------------------------------------------------------------- KPI

def test_kpi_rates_are_per_thousand_reached():
    rates = kpi_rates(_post(reach=2000, follows=6, shares=10, saves=40, plays=3000,
                            watch_ms=15000, duration=30))
    assert rates["follow_rate"] == 3.0        # 6 / 2000 × 1000
    assert rates["share_rate"] == 5.0
    assert rates["save_rate"] == 20.0
    assert rates["hook_rate"] == 1.5          # 3000 zhlédnutí na 2000 zasažených
    assert rates["watch_through"] == 0.5      # 15 s z 30 s


def test_missing_metrics_are_absent_not_zero():
    rates = kpi_rates({"reach": 1000, "saves": 10})
    assert "save_rate" in rates
    assert "follow_rate" not in rates         # neznámé ≠ nulové
    assert "watch_through" not in rates


def test_score_is_none_without_any_kpi():
    # starý příspěvek, kde API vrátilo jen lajky — na naše KPI ho soudit nelze
    assert score_post({"reach": 5000, "likes": 300, "age_hours": 48},
                      build_baselines(_history())) is None


# ---------------------------------------------------------------- skóre

def test_median_post_scores_100():
    baselines = build_baselines(_history())
    assert score_post(_post(), baselines) == pytest.approx(100.0, abs=0.5)


def test_reach_alone_does_not_win():
    """Jádro věci: velký dosah bez konverze musí prohrát s malým dosahem s konverzí."""
    baselines = build_baselines(_history())
    viral_but_empty = _post(reach=50000, follows=2, shares=4, saves=10, plays=60000,
                            watch_ms=4000)
    small_but_converting = _post(reach=800, follows=8, shares=20, saves=35, plays=1300,
                                 watch_ms=21000)
    assert score_post(small_but_converting, baselines) > score_post(viral_but_empty, baselines)
    assert score_post(viral_but_empty, baselines) < 60


def test_follows_carry_the_most_weight():
    baselines = build_baselines(_history())
    base = score_post(_post(), baselines)
    more_follows = score_post(_post(follows=9), baselines)
    more_saves = score_post(_post(saves=45), baselines)
    assert more_follows > base and more_saves > base
    # trojnásobek sledování musí zvednout skóre víc než trojnásobek uložení
    assert more_follows > more_saves


def test_small_reach_is_shrunk_toward_the_median():
    """Jedno sledování od 40 lidí nesmí vypadat jako zázrak."""
    baselines = build_baselines(_history())
    tiny = _post(reach=40, follows=1, shares=1, saves=2, plays=60, watch_ms=15000)
    huge = _post(reach=40000, follows=1000, shares=1000, saves=2000, plays=60000,
                 watch_ms=15000)
    # stejné míry, jiná spolehlivost → malý vzorek se stáhne blíž ke 100
    assert score_post(tiny, baselines) < score_post(huge, baselines)


def test_score_is_capped():
    baselines = build_baselines(_history())
    absurd = _post(reach=10000, follows=9000, shares=9000, saves=9000, plays=100000,
                   watch_ms=30000)
    assert score_post(absurd, baselines) == 250.0


def test_weights_redistribute_when_duration_is_unknown():
    """U ručně publikovaného videa neznáme délku — ostatní KPI to musí unést."""
    baselines = build_baselines(_history())
    without_duration = _post()
    without_duration.pop("duration_seconds")
    score = score_post(without_duration, baselines)
    assert score is not None
    assert 80 < score < 120


def test_cold_start_uses_reference_rates():
    score = score_post(_post(), None)
    assert score == pytest.approx(100.0, abs=25)   # proti orientačním mediánům
    assert COLD_START_RATES["follow_rate"] == 3.0


# ---------------------------------------------------------------- přehledy

def test_breakdown_explains_the_score():
    baselines = build_baselines(_history())
    detail = score_breakdown(_post(follows=9), baselines)
    rows = {row["kpi"]: row for row in detail["složky"]}
    assert rows["follow_rate"]["index"] > 1.2      # nadprůměr
    assert rows["follow_rate"]["dostupné"] is True
    assert detail["skóre"] > 100


def test_breakdown_marks_unavailable_kpis():
    detail = score_breakdown({"reach": 1000, "saves": 10, "age_hours": 48}, None)
    rows = {row["kpi"]: row for row in detail["složky"]}
    assert rows["follow_rate"]["dostupné"] is False
    assert rows["follow_rate"]["hodnota"] is None


def test_summary_leads_with_conversion_not_views():
    rows = [_post(reach=1000, follows=5), _post(reach=3000, follows=1)]
    out = summarize(rows, followers=2000)
    assert out["nova_sledovani_na_1k"] == pytest.approx(1000 * 6 / 4000, abs=0.01)
    assert out["sdileni_na_1k"] is not None
    assert out["prumerne_dokoukani_pct"] == 45.0
    assert "celkovy_dosah" in out


def test_watch_through_percentage():
    assert watch_through({"avg_watch_time": 7500}, 15) == 50.0
    assert watch_through({}, 15) is None


def test_engagement_rate_is_still_available_for_reports():
    assert engagement_rate({"reach": 1000, "likes": 80, "comments": 20}) == 10.0


def test_weighted_interactions_favor_saves_and_shares():
    assert weighted_interactions({"likes": 10}) == 10
    assert weighted_interactions({"saves": 10}) == 30
    assert weighted_interactions({"shares": 10}) == 40
