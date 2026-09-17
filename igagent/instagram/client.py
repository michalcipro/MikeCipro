"""Klient Instagram Graph API.

Důležité (a není to naše rozhodnutí — takhle to Meta má):
  * API funguje jen s **profesionálním** účtem (Business/Creator) propojeným
    s Facebook stránkou. Osobní účet přes oficiální API publikovat nelze.
  * Publikace probíhá dvoufázově: vytvoří se „kontejner" z **veřejné URL**
    média a ten se pak publikuje. Instagram si soubor stáhne sám.
  * Denní limit publikací je 50 příspěvků / 24 h na účet.
"""

from __future__ import annotations

import time

import requests

from ..errors import GraphAPIError
from ..util import get_logger, retry

log = get_logger(__name__)

POST_FIELDS = ("id,caption,media_type,media_product_type,media_url,thumbnail_url,"
               "permalink,timestamp,like_comments_disabled,comments_count,like_count,"
               "is_shared_to_feed,children{id,media_type,media_url}")

ACCOUNT_FIELDS = ("id,username,name,biography,website,followers_count,follows_count,"
                  "media_count,profile_picture_url")

# Metriky, které Meta u účtu podporuje. Sbíráme „nejlepší snahou" — když API
# některou v dané verzi nezná, vypadne z dalších pokusů místo tvrdé chyby.
ACCOUNT_METRICS_DAY = ("reach", "profile_views", "accounts_engaged",
                       "total_interactions", "website_clicks")

MEDIA_METRICS = {
    "IMAGE": ("reach", "likes", "comments", "saved", "shares", "total_interactions",
              "profile_visits", "follows"),
    "CAROUSEL_ALBUM": ("reach", "likes", "comments", "saved", "shares",
                       "total_interactions", "profile_visits", "follows"),
    "VIDEO": ("reach", "likes", "comments", "saved", "shares", "total_interactions",
              "views", "ig_reels_avg_watch_time", "ig_reels_video_view_total_time",
              "profile_visits", "follows"),
}


class GraphClient:
    """Tenká, ale odolná vrstva nad Graph API."""

    def __init__(self, settings, session=None):
        self.settings = settings
        self.base = settings.graph_url
        self.ig_user_id = settings.ig_user_id
        self.token = settings.ig_access_token
        self.session = session or requests.Session()

    # ------------------------------------------------------------ transport
    def _request(self, method, path, params=None, data=None, timeout=60):
        url = path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"
        params = dict(params or {})
        params.setdefault("access_token", self.token)
        response = self.session.request(method, url, params=params, data=data, timeout=timeout)
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        if response.status_code >= 400 or "error" in payload:
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise GraphAPIError(
                err.get("message") or f"HTTP {response.status_code} z {url}",
                status=response.status_code,
                code=err.get("code"),
                subcode=err.get("error_subcode"),
                fbtrace_id=err.get("fbtrace_id"),
                payload=payload)
        return payload

    def _call(self, method, path, **kwargs):
        @retry(times=5, base_delay=2.0, exceptions=(GraphAPIError, requests.RequestException),
               should_retry=lambda e: isinstance(e, requests.RequestException) or e.is_transient,
               logger=log)
        def _inner():
            return self._request(method, path, **kwargs)

        return _inner()

    def get(self, path, **params):
        return self._call("GET", path, params=params)

    def post(self, path, **data):
        token = data.pop("access_token", self.token)
        return self._call("POST", path, params={"access_token": token}, data=data)

    def paginate(self, path, limit=100, max_pages=20, **params):
        """Prochází stránkované kolekce a vrací plochý seznam položek."""
        params = dict(params)
        params["limit"] = limit
        page = self.get(path, **params)
        pages = 0
        while True:
            for item in page.get("data", []):
                yield item
            pages += 1
            next_url = (page.get("paging") or {}).get("next")
            if not next_url or pages >= max_pages:
                return
            page = self._call("GET", next_url)

    # ------------------------------------------------------------ účet
    def account(self, fields=ACCOUNT_FIELDS):
        return self.get(self.ig_user_id, fields=fields)

    def account_insights(self, metrics=ACCOUNT_METRICS_DAY, period="day", since=None, until=None,
                         metric_type="total_value"):
        """Insighty účtu. Nepodporované metriky postupně vyřazuje místo pádu."""
        remaining = list(metrics)
        collected = {}
        while remaining:
            params = {"metric": ",".join(remaining), "period": period}
            if metric_type:
                params["metric_type"] = metric_type
            if since:
                params["since"] = since
            if until:
                params["until"] = until
            try:
                payload = self.get(f"{self.ig_user_id}/insights", **params)
            except GraphAPIError as exc:
                dropped = _metric_from_error(str(exc), remaining)
                if not dropped:
                    log.warning("Insighty účtu se nepodařilo načíst: %s", exc)
                    break
                log.info("Metrika '%s' není v %s podporovaná, vynechávám ji.",
                         dropped, self.settings.graph_version)
                remaining.remove(dropped)
                continue
            for entry in payload.get("data", []):
                collected[entry.get("name")] = _insight_value(entry)
            break
        return collected

    def follower_demographics(self, breakdown="city"):
        """Demografie sledujících (vyžaduje 100+ sledujících)."""
        try:
            payload = self.get(f"{self.ig_user_id}/insights",
                               metric="follower_demographics", period="lifetime",
                               metric_type="total_value", breakdown=breakdown)
        except GraphAPIError as exc:
            log.info("Demografie není dostupná (%s)", exc)
            return {}
        out = {}
        for entry in payload.get("data", []):
            results = ((entry.get("total_value") or {}).get("breakdowns") or [{}])[0]
            for row in results.get("results", []):
                key = "|".join(row.get("dimension_values", []))
                out[key] = row.get("value")
        return out

    # ------------------------------------------------------------ média
    def media(self, limit=25, max_pages=4, fields=POST_FIELDS):
        return list(self.paginate(f"{self.ig_user_id}/media", limit=limit,
                                  max_pages=max_pages, fields=fields))

    def media_details(self, media_id, fields=POST_FIELDS):
        return self.get(media_id, fields=fields)

    def media_insights(self, media_id, media_type="IMAGE", product_type=None):
        metrics = list(MEDIA_METRICS.get(media_type.upper(), MEDIA_METRICS["IMAGE"]))
        if (product_type or "").upper() == "STORY":
            metrics = ["reach", "replies", "shares", "total_interactions", "navigation"]
        collected = {}
        while metrics:
            try:
                payload = self.get(f"{media_id}/insights", metric=",".join(metrics))
            except GraphAPIError as exc:
                dropped = _metric_from_error(str(exc), metrics)
                if not dropped:
                    log.warning("Insighty média %s nelze načíst: %s", media_id, exc)
                    break
                metrics.remove(dropped)
                continue
            for entry in payload.get("data", []):
                collected[entry.get("name")] = _insight_value(entry)
            break
        return collected

    # ------------------------------------------------------------ publikace
    def create_container(self, **params):
        """Vytvoří media container. Vrací jeho ID."""
        payload = self.post(f"{self.ig_user_id}/media", **params)
        container_id = payload.get("id")
        if not container_id:
            raise GraphAPIError(f"Graph API nevrátilo ID kontejneru: {payload}")
        return container_id

    def container_status(self, container_id):
        return self.get(container_id, fields="status_code,status")

    def wait_for_container(self, container_id, timeout=600, interval=5):
        """Čeká, než Instagram médium stáhne a zpracuje (u videa to trvá)."""
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self.container_status(container_id)
            status = (last.get("status_code") or "").upper()
            if status == "FINISHED":
                return last
            if status in ("ERROR", "EXPIRED"):
                raise GraphAPIError(
                    f"Zpracování média selhalo ({status}): {last.get('status')}", payload=last)
            log.debug("Kontejner %s ve stavu %s, čekám…", container_id, status or "?")
            time.sleep(interval)
        raise GraphAPIError(
            f"Kontejner {container_id} se nezpracoval do {timeout}s (poslední stav: {last})")

    def publish_container(self, container_id):
        payload = self.post(f"{self.ig_user_id}/media_publish", creation_id=container_id)
        media_id = payload.get("id")
        if not media_id:
            raise GraphAPIError(f"Publikace nevrátila ID média: {payload}")
        return media_id

    def publishing_limit(self):
        """Kolik z 50 příspěvků za 24 h je vyčerpáno."""
        payload = self.get(f"{self.ig_user_id}/content_publishing_limit",
                           fields="config,quota_usage")
        data = (payload.get("data") or [{}])[0]
        return {"used": data.get("quota_usage"),
                "total": (data.get("config") or {}).get("quota_total", 50)}

    # ------------------------------------------------------------ komentáře
    def comments(self, media_id, limit=50):
        return list(self.paginate(f"{media_id}/comments", limit=limit, max_pages=3,
                                  fields="id,text,username,timestamp,like_count"))

    def reply_to_comment(self, comment_id, message):
        return self.post(f"{comment_id}/replies", message=message)

    def hide_comment(self, comment_id, hide=True):
        return self.post(comment_id, hide="true" if hide else "false")

    # ------------------------------------------------------------ token
    def debug_token(self):
        return self.get("debug_token", input_token=self.token)

    def exchange_long_lived_token(self, app_id, app_secret, short_token=None):
        """Krátkodobý token (≈1 h) → dlouhodobý (≈60 dní)."""
        payload = self._request("GET", "oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token or self.token,
            "access_token": None,
        })
        return payload


def _insight_value(entry):
    """Graph API vrací hodnotu buď ve `values`, nebo v `total_value`."""
    if "total_value" in entry and entry["total_value"] is not None:
        value = entry["total_value"]
        return value.get("value") if isinstance(value, dict) else value
    values = entry.get("values") or []
    if not values:
        return None
    if len(values) == 1:
        return values[0].get("value")
    return sum(v.get("value") or 0 for v in values)


def _metric_from_error(message, candidates):
    """Z chybové hlášky Meta vytáhne, která metrika je nepodporovaná."""
    lowered = message.lower()
    for metric in candidates:
        if metric.lower() in lowered:
            return metric
    return None
