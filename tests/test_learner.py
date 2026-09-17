"""Ověřuje, že se agent opravdu učí — ne jen že kód proběhne."""

import datetime as dt
import random

from igagent.analytics.learner import Learner
from igagent.util import to_iso, utcnow


def _seed_history(store, n=60, seed=3):
    """Vyrobí historii, kde REEL a hodina 18 tajně fungují nejlíp."""
    rng = random.Random(seed)
    formats = ["REEL", "CAROUSEL", "IMAGE"]
    hours = [9, 12, 18]
    truth_format = {"REEL": 2.0, "CAROUSEL": 1.2, "IMAGE": 0.7}
    truth_hour = {9: 0.6, 12: 1.0, 18: 1.6}

    for index in range(n):
        fmt = formats[index % 3]
        hour = hours[(index // 3) % 3]
        published = utcnow() - dt.timedelta(days=n - index)
        reach = int(1000 * truth_format[fmt] * truth_hour[hour] * rng.uniform(0.8, 1.25))
        store.upsert_post({
            "media_id": f"m{index}", "format": fmt, "topic": "návyky",
            "hook_style": "cislo", "cta_type": "uloz", "template": "tip_list",
            "published_at": to_iso(published), "local_hour": hour,
            "local_weekday": published.weekday(), "caption_len": 400,
        })
        store.add_metrics(f"m{index}", {
            "reach": reach, "likes": int(reach * 0.05), "comments": 3,
            "saves": int(reach * 0.02), "shares": int(reach * 0.01),
            "age_hours": 48.0})
    store.save_account_snapshot(utcnow().date().isoformat(), {"followers": 3000})


def test_learner_finds_the_hidden_winners(store, settings):
    _seed_history(store)
    learner = Learner(store, settings, rng=random.Random(1))
    profile = learner.run()

    assert profile.sample_size == 60
    assert profile.is_confident
    # skryté pravidlo: REEL > CAROUSEL > IMAGE
    formats = [row["value"] for row in profile["rankings"]["format"]]
    assert formats == ["REEL", "CAROUSEL", "IMAGE"]
    # skryté pravidlo: 18 h je nejlepší
    assert profile["best_hours"][0] == 18
    # a promítne se to i do poměru formátů
    assert profile["format_mix"]["REEL"] > profile["format_mix"]["IMAGE"]


def test_shrinkage_protects_against_one_viral_post(store, settings):
    _seed_history(store, n=30)
    # jeden extrémní výstřel u jinak nejhoršího formátu
    store.upsert_post({"media_id": "viral", "format": "IMAGE", "topic": "náhoda",
                       "published_at": to_iso(utcnow() - dt.timedelta(days=1)),
                       "local_hour": 3, "local_weekday": 1})
    store.add_metrics("viral", {"reach": 500000, "likes": 50000, "saves": 20000,
                                "age_hours": 30.0})
    learner = Learner(store, settings, rng=random.Random(1))
    profile = learner.run()

    hour_rows = {row["value"]: row for row in profile["rankings"]["hour"]}
    lucky = hour_rows["3"]
    assert lucky["n"] == 1
    # jedno pozorování se stáhne k průměru — nesmí přebít hodiny s historií
    assert lucky["estimate"] < lucky["raw_mean"]
    assert profile["best_hours"][0] != 3


def test_choose_tries_every_option_before_exploiting(store, settings):
    _seed_history(store, n=9)
    learner = Learner(store, settings, rng=random.Random(5))
    learner.rebuild_feature_stats()

    picked = {learner.choose("format", ["REEL", "CAROUSEL", "IMAGE", "STORY"])
              for _ in range(20)}
    # STORY nikdy nebylo zkoušeno → musí dostat přednost
    assert "STORY" in picked


def test_choose_prefers_winner_when_everything_is_tried(store, settings):
    _seed_history(store, n=60)
    settings.explore_rate = 0.0
    learner = Learner(store, settings, rng=random.Random(7))
    learner.rebuild_feature_stats()

    picks = [learner.choose("format", ["REEL", "CAROUSEL", "IMAGE"]) for _ in range(10)]
    assert set(picks) == {"REEL"}


def test_empty_history_is_not_a_crash(store, settings):
    learner = Learner(store, settings)
    profile = learner.run()
    assert profile.sample_size == 0
    assert not profile.is_confident
    assert profile["warning"]
    assert learner.choose("format", ["REEL", "IMAGE"]) in ("REEL", "IMAGE")
