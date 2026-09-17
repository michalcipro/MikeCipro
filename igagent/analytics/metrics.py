"""Metriky a skórování příspěvků.

Skóre je relativní k vlastnímu účtu, ne k nějakému absolutnímu benchmarku:
100 = medián účtu, 200 = dvakrát lepší než medián, 50 = poloviční.
Díky tomu skóre nezestárne, když účet poroste — a nedá se ošidit tím,
že přibude sledujících.

Váhy interakcí odrážejí, co algoritmus Instagramu odměňuje nejvíc:
sdílení a uložení váží víc než lajk.
"""

from __future__ import annotations

from statistics import median

from ..util import clamp

INTERACTION_WEIGHTS = {"likes": 1.0, "comments": 2.0, "saves": 3.0, "shares": 4.0}

# mapování názvů z Graph API na naše sloupce
INSIGHT_ALIASES = {
    "reach": "reach",
    "impressions": "impressions",
    "likes": "likes",
    "comments": "comments",
    "saved": "saves",
    "saves": "saves",
    "shares": "shares",
    "total_interactions": "total_interactions",
    "views": "plays",
    "plays": "plays",
    "video_views": "plays",
    "ig_reels_avg_watch_time": "avg_watch_time",
    "ig_reels_video_view_total_time": "total_watch_time",
    "profile_visits": "profile_visits",
    "profile_activity": "profile_activity",
    "follows": "follows",
    "replies": "comments",
    "navigation": "navigation",
}

AGE_BUCKETS = ((0, 36), (36, 168), (168, 10 ** 6))   # <36 h, do 7 dní, starší


def normalize_media_insights(raw, fallback=None):
    """Sjednotí názvy metrik z API a doplní lajky/komentáře z detailu média."""
    out = {}
    for key, value in (raw or {}).items():
        target = INSIGHT_ALIASES.get(key)
        if target and isinstance(value, (int, float)):
            out[target] = value
    fallback = fallback or {}
    out.setdefault("likes", fallback.get("like_count"))
    out.setdefault("comments", fallback.get("comments_count"))
    return {k: v for k, v in out.items() if v is not None}


def weighted_interactions(metrics):
    """Vážený součet interakcí — saves a shares váží nejvíc."""
    total = 0.0
    for key, weight in INTERACTION_WEIGHTS.items():
        total += (metrics.get(key) or 0) * weight
    return total


def _bucket(age_hours):
    age = age_hours or 0
    for index, (low, high) in enumerate(AGE_BUCKETS):
        if low <= age < high:
            return index
    return len(AGE_BUCKETS) - 1


def build_baselines(rows):
    """Mediány dosahu a interakcí podle stáří příspěvku.

    Pro každý „věkový koš" spočítá medián zvlášť; když v koši nejsou aspoň
    tři příspěvky, použije se globální medián. Bez dat se vrací None a skóre
    se počítá z poměru k počtu sledujících.
    """
    per_bucket = {index: {"reach": [], "weighted": []} for index in range(len(AGE_BUCKETS))}
    all_reach, all_weighted = [], []
    for row in rows:
        reach = row.get("reach")
        if not reach:
            continue
        weighted = weighted_interactions(row)
        index = _bucket(row.get("age_hours"))
        per_bucket[index]["reach"].append(reach)
        per_bucket[index]["weighted"].append(weighted)
        all_reach.append(reach)
        all_weighted.append(weighted)

    if not all_reach:
        return None

    global_reach = median(all_reach)
    global_weighted = median(all_weighted) if all_weighted else 0.0
    baselines = {}
    for index, data in per_bucket.items():
        baselines[index] = {
            "reach": median(data["reach"]) if len(data["reach"]) >= 3 else global_reach,
            "weighted": (median(data["weighted"]) if len(data["weighted"]) >= 3
                         else global_weighted),
        }
    baselines["global"] = {"reach": global_reach, "weighted": global_weighted, "n": len(all_reach)}
    return baselines


def score_post(metrics, baselines=None, followers=None):
    """Skóre 0–250, kde 100 = průměrný příspěvek tohoto účtu."""
    reach = metrics.get("reach") or 0
    weighted = weighted_interactions(metrics)

    if baselines:
        base = baselines[_bucket(metrics.get("age_hours"))]
        reach_index = reach / base["reach"] if base["reach"] else 1.0
        eng_index = weighted / base["weighted"] if base["weighted"] else 1.0
    elif followers:
        # bez historie: 30% dosah mezi sledujícími a 5% zapojení bereme jako průměr
        reach_index = (reach / followers) / 0.30 if followers else 1.0
        eng_index = (weighted / max(reach, 1)) / 0.05
    else:
        return None

    raw = 100.0 * (0.55 * reach_index + 0.45 * eng_index)
    return round(clamp(raw, 0.0, 250.0), 2)


def engagement_rate(metrics, followers=None):
    """Klasická míra zapojení — pro report, ne pro učení."""
    base = metrics.get("reach") or followers
    if not base:
        return None
    raw = (metrics.get("likes") or 0) + (metrics.get("comments") or 0) \
        + (metrics.get("saves") or 0) + (metrics.get("shares") or 0)
    return round(100.0 * raw / base, 2)


def watch_through(metrics, duration_seconds=None):
    """Podíl zhlédnuté délky Reelu (když máme průměrný čas sledování)."""
    avg = metrics.get("avg_watch_time")
    if not avg or not duration_seconds:
        return None
    # Graph API vrací avg_watch_time v milisekundách
    seconds = avg / 1000.0 if avg > 1000 else avg
    return round(clamp(100.0 * seconds / duration_seconds, 0.0, 100.0), 1)


def summarize(rows, followers=None):
    """Pár čísel za období — pro report a pro prompt analytika."""
    if not rows:
        return {}
    reaches = [r.get("reach") or 0 for r in rows]
    scores = [r.get("score") for r in rows if r.get("score") is not None]
    return {
        "pocet_prispevku": len(rows),
        "medi_dosah": round(median(reaches), 1) if reaches else 0,
        "prumerny_dosah": round(sum(reaches) / len(reaches), 1) if reaches else 0,
        "celkove_interakce": int(sum(weighted_interactions(r) for r in rows)),
        "medi_skore": round(median(scores), 1) if scores else None,
        "sledujici": followers,
    }
