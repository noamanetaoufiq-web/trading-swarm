"""Headlines (RSS) + economic calendar (ForexFactory JSON + manual events) + blackout-window helper."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import requests


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_headlines(feeds: list, keywords_by_symbol: dict, per_symbol: int = 12, demo: bool = False) -> dict:
    """Returns {symbol: [ {title, source, published}, ... ]} filtered by each instrument's keywords."""
    if demo:
        return {s: [{"title": f"[demo] Placeholder headline relevant to {s}", "source": "demo", "published": ""}] for s in keywords_by_symbol}
    import feedparser
    items = []
    for url in feeds:
        try:
            d = feedparser.parse(url)
            for e in d.entries[:40]:
                items.append({"title": e.get("title", ""), "source": d.feed.get("title", url), "published": e.get("published", "")})
        except Exception:  # noqa: BLE001
            continue
    out = {}
    for sym, kws in keywords_by_symbol.items():
        kws = [k.lower() for k in kws]
        out[sym] = [i for i in items if any(k in i["title"].lower() for k in kws)][:per_symbol]
    return out


def fetch_calendar(url: str, manual_events: list, demo: bool = False, now: datetime | None = None) -> list:
    now = now or datetime.now(timezone.utc)
    events = []
    if demo:
        events.append({"title": "[demo] US Core PCE", "currency": "USD", "impact": "High",
                       "time_utc": now + timedelta(hours=20), "forecast": "", "previous": ""})
    else:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "trading-swarm/1.0"})
            r.raise_for_status()
            for e in r.json():
                events.append({"title": e.get("title", ""), "currency": e.get("country", ""), "impact": e.get("impact", ""),
                               "time_utc": _parse_dt(e["date"]).astimezone(timezone.utc),
                               "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
        except Exception:  # noqa: BLE001
            pass  # manual events below still apply
    for m in manual_events or []:
        events.append({"title": m["title"], "currency": m.get("currency", "USD"), "impact": m.get("impact", "High"),
                       "time_utc": _parse_dt(m["time_utc"]), "forecast": "", "previous": ""})
    return sorted(events, key=lambda e: e["time_utc"])


def upcoming(events: list, now: datetime, hours: int = 48, impacts=("High", "Medium"), currencies=("USD",)) -> list:
    end = now + timedelta(hours=hours)
    return [e for e in events if now - timedelta(hours=1) <= e["time_utc"] <= end
            and e["impact"] in impacts and (not currencies or e["currency"] in currencies)]


def high_impact_in_window(events: list, now: datetime, minutes: int, currencies=("USD",)) -> list:
    w = timedelta(minutes=minutes)
    return [e for e in events if e["impact"] == "High" and abs(e["time_utc"] - now) <= w
            and (not currencies or e["currency"] in currencies)]


def event_to_dict(e: dict) -> dict:
    return {**e, "time_utc": e["time_utc"].isoformat(timespec="minutes")}
