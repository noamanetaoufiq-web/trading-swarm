"""Crowd / on-chain data: Fear&Greed, Binance futures funding + OI + long/short ratio, optional Whale Alert."""
from __future__ import annotations
import os
import requests

TIMEOUT = 10


def _get(url, params=None):
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fear_greed():
    d = _get("https://api.alternative.me/fng/", {"limit": 7})["data"]
    return {"latest": int(d[0]["value"]), "label": d[0]["value_classification"],
            "last_7d": [int(x["value"]) for x in d]}


def binance_futures(symbol: str):
    base = "https://fapi.binance.com"
    out = {}
    fr = _get(f"{base}/fapi/v1/fundingRate", {"symbol": symbol, "limit": 10})
    rates = [float(x["fundingRate"]) * 100 for x in fr]  # in %
    out["funding_rate_pct_latest"] = round(rates[-1], 5)
    out["funding_rate_pct_avg10"] = round(sum(rates) / len(rates), 5)
    oi = _get(f"{base}/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 24})
    out["open_interest_usd_latest"] = round(float(oi[-1]["sumOpenInterestValue"]), 0)
    out["open_interest_24h_change_pct"] = round((float(oi[-1]["sumOpenInterestValue"]) / float(oi[0]["sumOpenInterestValue"]) - 1) * 100, 2)
    ls = _get(f"{base}/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "1h", "limit": 1})
    out["global_long_short_ratio"] = round(float(ls[-1]["longShortRatio"]), 3)
    return out


def whale_alerts(currency="btc", min_usd=5_000_000):
    key = os.getenv("WHALE_ALERT_KEY")
    if not key:
        return {"available": False, "note": "set WHALE_ALERT_KEY to enable whale transfers"}
    import time
    d = _get("https://api.whale-alert.io/v1/transactions",
             {"api_key": key, "min_value": min_usd, "currency": currency, "start": int(time.time()) - 3600})
    tx = d.get("transactions", [])
    to_ex = sum(t["amount_usd"] for t in tx if t.get("to", {}).get("owner_type") == "exchange")
    from_ex = sum(t["amount_usd"] for t in tx if t.get("from", {}).get("owner_type") == "exchange")
    return {"available": True, "count_1h": len(tx), "to_exchanges_usd": round(to_ex), "from_exchanges_usd": round(from_ex)}


DEMO_CRYPTO = {
    "fear_greed": {"latest": 30, "label": "Fear", "last_7d": [30, 33, 38, 41, 44, 40, 36]},
    "futures": {"funding_rate_pct_latest": 0.0042, "funding_rate_pct_avg10": 0.0061, "open_interest_usd_latest": 3.1e10,
                "open_interest_24h_change_pct": -2.4, "global_long_short_ratio": 1.12},
    "whales": {"available": False, "note": "demo"},
}


def social_snapshot(inst, macro: dict, demo: bool = False) -> dict:
    if inst.asset_class == "crypto":
        if demo:
            return {"asset_class": "crypto", **DEMO_CRYPTO}
        snap = {"asset_class": "crypto"}
        for name, fn in (("fear_greed", fear_greed), ("futures", lambda: binance_futures(inst.binance_symbol)), ("whales", whale_alerts)):
            try:
                snap[name] = fn()
            except Exception as e:  # noqa: BLE001
                snap[name] = {"error": f"{type(e).__name__}: {e}"}
        return snap
    vix = (macro or {}).get("VIX", {})
    return {"asset_class": inst.asset_class, "vix": vix,
            "note": "No on-chain data for this asset class; VIX used as a crowd-fear proxy. Funding/OI/whales not applicable."}
