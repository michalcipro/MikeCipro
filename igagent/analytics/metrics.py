"""Metriky a skórování příspěvků.

Co se tu počítá — a hlavně co se nepočítá
-----------------------------------------
Views nejsou cíl. Video, které vidí 30 000 lidí a nepřivede nikoho, je horší
než video pro 800 lidí, po kterém přijde osm nových sledujících. Proto se
neskóruje dosah, ale **konverze dosahu**: vše se dělí počtem zasažených lidí.

Pět složek skóre (váhy v `KPI_WEIGHTS`, jdou přenastavit):

  follow_rate    nové sledování na 1 000 zasažených   ← hlavní ukazatel
  share_rate     sdílení na 1 000 zasažených
  save_rate      uložení na 1 000 zasažených
  hook_rate      zhlédnutí / dosah                     ← udržení na začátku
  watch_through  průměrná doba sledování / délka videa

Každá složka se porovnává s **mediánem tvého vlastního účtu**, takže
100 = průměrný příspěvek. Skóre tím nezestárne, když účet poroste.

Poctivá poznámka k „udržení prvních tří sekund": Instagram Graph API tenhle
údaj nedává. `hook_rate` (zhlédnutí / dosah) je nejbližší dostupná náhrada —
říká, kolik ze zasažených lidí video vůbec spustilo a nechalo běžet. Trend
sleduje dobře, ale není to totéž číslo, jaké vidíš v aplikaci u retence.

Malý dosah = velký šum: 1 sledování ze 40 lidí by jinak vypadalo jako
zázrak. Proto se každá míra stahuje k mediánu podle velikosti dosahu
(empirický Bayes, `PRIOR_REACH`).
"""

from __future__ import annotations

from statistics import median

from ..util import clamp

# Váhy KPI. Součet nemusí být 1 — normalizuje se podle dostupných složek.
KPI_WEIGHTS = {
    "follow_rate": 0.35,
    "share_rate": 0.20,
    "save_rate": 0.20,
    "hook_rate": 0.15,
    "watch_through": 0.10,
}

# Kolik „virtuálního" dosahu má prior. Čím vyšší, tím opatrnější jsou
# závěry z příspěvků s malým dosahem.
PRIOR_REACH = 500.0

# Když účet nemá historii, použijí se tyhle orientační mediány.
COLD_START_RATES = {
    "follow_rate": 3.0,        # 3 nová sledování na 1 000 zasažených
    "share_rate": 8.0,
    "save_rate": 15.0,
    "hook_rate": 1.4,
    "watch_through": 0.45,
}

# Pro přehled a report (ne pro skóre).
INTERACTION_WEIGHTS = {"likes": 1.0, "comments": 2.0, "saves": 3.0, "shares": 4.0}

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

CS_LABELS = {
    "follow_rate": "nová sledování / 1 000 zasažených",
    "share_rate": "sdílení / 1 000 zasažených",
    "save_rate": "uložení / 1 000 zasažených",
    "hook_rate": "zhlédnutí / dosah (náhrada za udržení)",
    "watch_through": "podíl zhlédnuté délky",
}


# ---------------------------------------------------------------- normalizace

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
    """Vážený součet interakcí — jen pro přehled, do skóre nevstupuje."""
    total = 0.0
    for key, weight in INTERACTION_WEIGHTS.items():
        total += (metrics.get(key) or 0) * weight
    return total


def seconds_of(avg_watch_time):
    """Graph API vrací průměrnou dobu sledování v milisekundách."""
    if not avg_watch_time:
        return None
    return avg_watch_time / 1000.0 if avg_watch_time > 1000 else float(avg_watch_time)


# ---------------------------------------------------------------- KPI

def kpi_rates(metrics, duration_seconds=None):
    """Spočítá jednotlivá KPI. Co nejde spočítat, chybí (není to nula)."""
    reach = metrics.get("reach") or 0
    rates = {}
    if reach > 0:
        if metrics.get("follows") is not None:
            rates["follow_rate"] = 1000.0 * metrics["follows"] / reach
        if metrics.get("shares") is not None:
            rates["share_rate"] = 1000.0 * metrics["shares"] / reach
        if metrics.get("saves") is not None:
            rates["save_rate"] = 1000.0 * metrics["saves"] / reach
        if metrics.get("plays"):
            rates["hook_rate"] = metrics["plays"] / reach

    duration = duration_seconds or metrics.get("duration_seconds")
    watched = seconds_of(metrics.get("avg_watch_time"))
    if duration and watched:
        rates["watch_through"] = clamp(watched / duration, 0.0, 1.0)
    return rates


def _bucket(age_hours):
    age = age_hours or 0
    for index, (low, high) in enumerate(AGE_BUCKETS):
        if low <= age < high:
            return index
    return len(AGE_BUCKETS) - 1


def build_baselines(rows, min_samples=3):
    """Mediány jednotlivých KPI — zvlášť podle stáří příspěvku.

    Když v „koši" není dost příspěvků, sáhne se po globálním mediánu;
    když nejsou ani ty, po orientačních hodnotách `COLD_START_RATES`.
    """
    per_bucket = {index: {} for index in range(len(AGE_BUCKETS))}
    global_rates = {}
    reaches = []

    for row in rows:
        reach = row.get("reach")
        if not reach:
            continue
        reaches.append(reach)
        rates = kpi_rates(row, row.get("duration_seconds"))
        bucket = _bucket(row.get("age_hours"))
        for key, value in rates.items():
            per_bucket[bucket].setdefault(key, []).append(value)
            global_rates.setdefault(key, []).append(value)

    if not reaches:
        return None

    global_median = {key: median(values) for key, values in global_rates.items()}
    baselines = {}
    for index, data in per_bucket.items():
        baselines[index] = {
            key: (median(values) if len(values) >= min_samples
                  else global_median.get(key, COLD_START_RATES.get(key)))
            for key, values in ({**global_rates, **data}).items()
        }
    baselines["global"] = {**global_median, "n": len(reaches),
                           "median_reach": median(reaches)}
    return baselines


def _shrink(rate, prior, reach):
    """Stáhne míru k prioru podle toho, na jak velkém dosahu vznikla."""
    if rate is None or prior is None:
        return rate
    weight = reach / (reach + PRIOR_REACH) if reach else 0.0
    return weight * rate + (1 - weight) * prior


def score_post(metrics, baselines=None, followers=None, weights=None,
               duration_seconds=None):
    """Skóre 0–250, kde 100 = průměrný příspěvek tohoto účtu.

    `followers` se nepoužívá k výpočtu (míry jsou vztažené k dosahu),
    zůstává kvůli zpětné kompatibilitě volání.
    """
    weights = weights or KPI_WEIGHTS
    reach = metrics.get("reach") or 0
    if not reach:
        return None

    rates = kpi_rates(metrics, duration_seconds)
    if not rates:
        return None

    bucket = (baselines or {}).get(_bucket(metrics.get("age_hours"))) if baselines else None
    reference = {**COLD_START_RATES, **(bucket or {})}

    total_weight = 0.0
    total = 0.0
    used = {}
    for key, weight in weights.items():
        if key not in rates:
            continue
        prior = reference.get(key) or COLD_START_RATES.get(key)
        if not prior:
            continue
        adjusted = _shrink(rates[key], prior, reach)
        index = adjusted / prior
        used[key] = round(index, 3)
        total += weight * index
        total_weight += weight

    if total_weight <= 0:
        return None

    return round(clamp(100.0 * total / total_weight, 0.0, 250.0), 2)


def score_breakdown(metrics, baselines=None, duration_seconds=None, weights=None):
    """Rozpad skóre po složkách — pro `igagent kpi` a pro report."""
    weights = weights or KPI_WEIGHTS
    rates = kpi_rates(metrics, duration_seconds)
    bucket = (baselines or {}).get(_bucket(metrics.get("age_hours"))) if baselines else None
    reference = {**COLD_START_RATES, **(bucket or {})}
    reach = metrics.get("reach") or 0

    rows = []
    for key, weight in weights.items():
        prior = reference.get(key) or COLD_START_RATES.get(key)
        value = rates.get(key)
        rows.append({
            "kpi": key,
            "popis": CS_LABELS.get(key, key),
            "hodnota": None if value is None else round(value, 3),
            "medián_účtu": None if prior is None else round(prior, 3),
            "index": (None if value is None or not prior
                      else round(_shrink(value, prior, reach) / prior, 2)),
            "váha": weight,
            "dostupné": value is not None,
        })
    return {"skóre": score_post(metrics, baselines, duration_seconds=duration_seconds,
                                weights=weights),
            "dosah": reach, "složky": rows}


# ---------------------------------------------------------------- přehledy

def engagement_rate(metrics, followers=None):
    """Klasická míra zapojení — pro report, ne pro učení."""
    base = metrics.get("reach") or followers
    if not base:
        return None
    raw = (metrics.get("likes") or 0) + (metrics.get("comments") or 0) \
        + (metrics.get("saves") or 0) + (metrics.get("shares") or 0)
    return round(100.0 * raw / base, 2)


def watch_through(metrics, duration_seconds=None):
    """Podíl zhlédnuté délky v procentech."""
    duration = duration_seconds or metrics.get("duration_seconds")
    watched = seconds_of(metrics.get("avg_watch_time"))
    if not duration or not watched:
        return None
    return round(clamp(100.0 * watched / duration, 0.0, 100.0), 1)


def summarize(rows, followers=None):
    """Souhrn za období — vědomě vede KPI, ne dosah."""
    if not rows:
        return {}
    reaches = [r.get("reach") or 0 for r in rows]
    scores = [r.get("score") for r in rows if r.get("score") is not None]
    totals = {"follows": 0, "shares": 0, "saves": 0, "reach": 0}
    for row in rows:
        for key in ("follows", "shares", "saves", "reach"):
            totals[key] += row.get(key) or 0

    per_1k = (lambda key: round(1000.0 * totals[key] / totals["reach"], 2)
              if totals["reach"] else None)
    watch = [watch_through(r, r.get("duration_seconds")) for r in rows]
    watch = [w for w in watch if w is not None]

    return {
        "pocet_prispevku": len(rows),
        "nova_sledovani_na_1k": per_1k("follows"),
        "sdileni_na_1k": per_1k("shares"),
        "ulozeni_na_1k": per_1k("saves"),
        "prumerne_dokoukani_pct": round(sum(watch) / len(watch), 1) if watch else None,
        "medi_skore": round(median(scores), 1) if scores else None,
        "celkovy_dosah": totals["reach"],
        "medi_dosah": round(median(reaches), 1) if reaches else 0,
        "sledujici": followers,
    }
