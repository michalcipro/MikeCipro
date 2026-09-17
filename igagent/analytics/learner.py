"""Učící smyčka.

Princip: každý publikovaný příspěvek má sadu vlastností (formát, téma, typ
hooku, CTA, šablona, hodina, den v týdnu). Po změření dostane skóre. Agent
si u každé hodnoty vlastnosti drží průměr a rozptyl a rozhoduje se podle
horní meze spolehlivostního intervalu (UCB):

    priorita = odhad + c · směrodatná chyba

Odhad je „zesmrštěný" k průměru účtu podle počtu pozorování (Bayesovský
shrinkage), takže jeden náhodně virální příspěvek neurčí strategii na měsíc.
Člen se směrodatnou chybou zajistí, že se občas zkusí i málo ověřená varianta —
bez toho by se agent zasekl na tom, co zkusil první.
"""

from __future__ import annotations

import math
import random

from ..util import get_logger, to_iso, utcnow
from .metrics import build_baselines, score_post

log = get_logger(__name__)

PRIOR_MEAN = 100.0        # skóre průměrného příspěvku
PRIOR_WEIGHT = 3.0        # kolik „virtuálních" pozorování má prior
UCB_C = 1.2               # ochota zkoušet nejisté varianty
MIN_SAMPLE_FOR_ADVICE = 8
MIN_N_FOR_RANKING = 2     # kolik pozorování musí hodnota mít, než ji doporučíme

# Pozor na rozdíl: `choose()` smí sáhnout i po hodnotě s jediným pozorováním
# (to je průzkum), ale doporučení ve strategii — „tvoje nejlepší hodina je…" —
# se z jednoho příspěvku dělat nesmí. Proto ranking filtrujeme.

LEARNED_FEATURES = ("series", "format", "topic", "pillar", "hook_style", "cta_type",
                    "template", "hour", "weekday", "language")

WEEKDAYS_CS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]


class StrategyProfile(dict):
    """Výstup učení — jde rovnou do promptu Claude i do plánovače."""

    @property
    def sample_size(self):
        return self.get("sample_size", 0)

    @property
    def is_confident(self):
        return self.sample_size >= MIN_SAMPLE_FOR_ADVICE

    def top(self, feature, default=None):
        ranking = (self.get("rankings") or {}).get(feature) or []
        return ranking[0]["value"] if ranking else default

    def best_hours(self, count=3):
        ranking = (self.get("rankings") or {}).get("hour") or []
        return [int(r["value"]) for r in ranking[:count] if str(r["value"]).isdigit()]


class Learner:
    def __init__(self, store, settings, rng=None):
        self.store = store
        self.settings = settings
        self.random = rng or random.Random()

    # ------------------------------------------------------------ přepočet
    def rescore(self):
        """Přepočítá skóre všech měřených příspěvků proti aktuálním mediánům."""
        rows = self.store.posts_with_latest_metrics(limit=500)
        measured = [r for r in rows if r.get("reach") is not None]
        baselines = build_baselines(measured)
        followers = self.store.latest_followers()

        scored, unscored = 0, []
        for row in measured:
            score = score_post(row, baselines, followers,
                               duration_seconds=row.get("duration_seconds"))
            if score is None:
                unscored.append(row["media_id"])
                continue
            row["score"] = score
            latest = self.store.latest_metrics(row["media_id"])
            if latest:
                self.store.set_metric_score(latest["id"], score)
            scored += 1
        log.info("Přepočítáno skóre u %d příspěvků (vzorek %s).", scored,
                 (baselines or {}).get("global", {}).get("n", 0))
        if unscored:
            # ticho by tu bylo zavádějící — tyhle příspěvky do učení nevstupují
            log.info("%d příspěvků nejde ohodnotit (chybí konverzní metriky, "
                     "typicky starší nebo ručně publikované): %s",
                     len(unscored), ", ".join(unscored[:5])
                     + ("…" if len(unscored) > 5 else ""))
        return measured

    def rebuild_feature_stats(self, rows=None):
        """Znovu postaví agregace vlastností z nul (idempotentní)."""
        rows = rows if rows is not None else self.rescore()
        self.store.reset_feature_stats()
        counted = 0
        for row in rows:
            score = row.get("score")
            if score is None:
                continue
            for feature in LEARNED_FEATURES:
                value = _feature_value(row, feature)
                if value not in (None, "", "None"):
                    self.store.bump_feature(feature, value, score)
            counted += 1
        log.info("Agregace vlastností postavena z %d příspěvků.", counted)
        return counted

    # ------------------------------------------------------------ odhady
    def estimates(self, feature):
        """[(hodnota, odhad, priorita, n)] seřazené podle odhadu."""
        out = []
        for row in self.store.feature_stats(feature):
            n = row["n"] or 0
            if not n:
                continue
            mean = row["score_sum"] / n
            variance = max(0.0, row["score_sq"] / n - mean ** 2)
            shrunk = (row["score_sum"] + PRIOR_MEAN * PRIOR_WEIGHT) / (n + PRIOR_WEIGHT)
            std_err = math.sqrt(variance / n) if n > 1 else 45.0
            out.append({
                "value": row["value"],
                "estimate": round(shrunk, 1),
                "raw_mean": round(mean, 1),
                "priority": round(shrunk + UCB_C * std_err, 1),
                "n": n,
            })
        out.sort(key=lambda item: item["estimate"], reverse=True)
        return out

    def choose(self, feature, options, explore_rate=None):
        """Vybere hodnotu vlastnosti: většinou to nejlepší, občas průzkum.

        Nevyzkoušené možnosti mají přednost — dokud agent nezkusí všechno
        aspoň jednou, nemá co porovnávat.
        """
        options = [o for o in options if o]
        if not options:
            return None
        explore_rate = self.settings.explore_rate if explore_rate is None else explore_rate
        known = {item["value"]: item for item in self.estimates(feature)}

        untried = [o for o in options if str(o) not in known]
        if untried:
            return self.random.choice(untried)
        if self.random.random() < explore_rate:
            return self.random.choice(options)
        return max(options, key=lambda o: known[str(o)]["priority"])

    def weighted_format_mix(self, fallback_mix):
        """Poměr formátů: naučený výkon smíchaný s tím, co si přeje brand kit."""
        estimates = {item["value"]: item for item in self.estimates("format")}
        mix = {}
        for fmt, wish in (fallback_mix or {}).items():
            item = estimates.get(fmt)
            if not item:
                mix[fmt] = wish
                continue
            # odhad 100 = neutrální; 150 → váha ×1,5
            mix[fmt] = max(0.05, wish * (item["estimate"] / PRIOR_MEAN))
        total = sum(mix.values()) or 1.0
        return {k: round(v / total, 3) for k, v in mix.items()}

    # ------------------------------------------------------------ profil
    def build_profile(self, rows=None):
        rows = rows if rows is not None else self.store.posts_with_latest_metrics(limit=500)
        measured = [r for r in rows if r.get("score") is not None]
        rankings = {feature: self.estimates(feature) for feature in LEARNED_FEATURES}

        followers = self.store.latest_followers()
        snapshots = self.store.account_snapshots(limit=30)
        growth = None
        if len(snapshots) >= 2 and snapshots[0].get("followers") and snapshots[-1].get("followers"):
            growth = snapshots[0]["followers"] - snapshots[-1]["followers"]

        profile = StrategyProfile({
            "generated_at": to_iso(utcnow()),
            "sample_size": len(measured),
            "followers": followers,
            "follower_change_period": growth,
            "rankings": rankings,
            "best_hours": [int(r["value"]) for r in _reliable(rankings["hour"])[:4]
                           if str(r["value"]).isdigit()],
            "best_weekdays": [_weekday_name(r["value"])
                              for r in _reliable(rankings["weekday"])[:3]],
            "format_mix": self.weighted_format_mix(self.settings.brand.format_mix),
            "caption_length": _caption_length_insight(measured),
            "do_more": _advice(rankings, above=True),
            "do_less": _advice(rankings, above=False),
            "warning": (None if len(measured) >= MIN_SAMPLE_FOR_ADVICE else
                        f"Vzorek je malý ({len(measured)} měřených příspěvků). "
                        "Doporučení ber jako hypotézy, ne jako závěry."),
        })
        self.store.save_strategy(dict(profile), len(measured))
        return profile

    def current_profile(self):
        latest = self.store.latest_strategy()
        return StrategyProfile(latest["profile"]) if latest else StrategyProfile(
            {"sample_size": 0, "rankings": {}, "warning": "Zatím žádná data."})

    def run(self):
        """Celý cyklus učení: skóre → agregace → profil."""
        rows = self.rescore()
        self.rebuild_feature_stats(rows)
        return self.build_profile(rows)


# ------------------------------------------------------------------ pomocné

def _reliable(ranking, min_n=MIN_N_FOR_RANKING):
    """Jen hodnoty s dost pozorováními. Když žádná není, vrátí prázdno —
    to je poctivější než doporučit něco na základě jediného příspěvku."""
    return [row for row in ranking if row["n"] >= min_n]


def _feature_value(row, feature):
    if feature == "hour":
        return None if row.get("local_hour") is None else str(row["local_hour"])
    if feature == "weekday":
        return None if row.get("local_weekday") is None else str(row["local_weekday"])
    value = row.get(feature)
    return None if value in (None, "") else str(value)


def _weekday_name(value):
    try:
        return WEEKDAYS_CS[int(value)]
    except (ValueError, IndexError, TypeError):
        return str(value)


def _caption_length_insight(rows):
    """Souvisí délka popisku s výkonem? Vrací nejlepší pásmo."""
    buckets = {"krátký (<300)": [], "střední (300–800)": [], "dlouhý (>800)": []}
    for row in rows:
        length = row.get("caption_len") or len(row.get("caption") or "")
        score = row.get("score")
        if score is None:
            continue
        key = ("krátký (<300)" if length < 300
               else "střední (300–800)" if length <= 800 else "dlouhý (>800)")
        buckets[key].append(score)
    summary = {k: {"n": len(v), "prumer": round(sum(v) / len(v), 1)}
               for k, v in buckets.items() if v}
    best = max(summary.items(), key=lambda kv: kv[1]["prumer"], default=(None, None))
    return {"pasma": summary, "nejlepsi": best[0]}


def _advice(rankings, above=True, limit=5):
    """Krátké shrnutí „dělej víc / míň" — čitelné pro člověka i pro Claude."""
    out = []
    for feature, items in rankings.items():
        usable = [i for i in items if i["n"] >= 2]
        if len(usable) < 2:
            continue
        pick = usable[0] if above else usable[-1]
        if above and pick["estimate"] <= PRIOR_MEAN * 1.08:
            continue
        if not above and pick["estimate"] >= PRIOR_MEAN * 0.92:
            continue
        label = _weekday_name(pick["value"]) if feature == "weekday" else pick["value"]
        out.append({
            "vlastnost": feature,
            "hodnota": label,
            "odhad": pick["estimate"],
            "vzorek": pick["n"],
        })
    out.sort(key=lambda item: item["odhad"], reverse=above)
    return out[:limit]
