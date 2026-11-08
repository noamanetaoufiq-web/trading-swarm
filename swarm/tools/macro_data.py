"""Macro snapshot: DXY, US10Y, VIX, equities, gold, oil (yfinance) + simple regime hint."""
from __future__ import annotations
import numpy as np

TICKERS = {"DXY": "DX-Y.NYB", "US10Y": "^TNX", "VIX": "^VIX", "SPX": "^GSPC",
           "NDX": "^NDX", "GOLD": "GC=F", "OIL": "CL=F"}

DEMO = {
    "DXY": (102.1, 0.2, 0.8, 1.9, "above"), "US10Y": (4.95, 0.02, 0.10, 0.25, "above"),
    "VIX": (22.5, 1.1, 3.0, 5.0, "above"), "SPX": (7541.4, -0.51, -1.3, -2.2, "below"),
    "NDX": (28470.0, -1.4, -3.1, -4.8, "below"), "GOLD": (3944.0, 0.6, -2.0, -5.5, "below"),
    "OIL": (87.0, -2.1, -4.0, -6.0, "below"),
}


def fetch_macro(demo: bool = False) -> dict:
    out = {}
    if demo:
        for k, (last, c1, c5, c20, vs) in DEMO.items():
            out[k] = {"last": last, "chg_1d_pct": c1, "chg_5d_pct": c5, "chg_20d_pct": c20, "vs_ema50": vs}
        out["_note"] = "synthetic demo values"
        return out
    import yfinance as yf
    for name, tkr in TICKERS.items():
        try:
            df = yf.Ticker(tkr).history(period="6mo", interval="1d", auto_adjust=False)
            c = df["close" if "close" in df else "Close"].dropna()
            ema50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
            pct = lambda n: round(float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100), 2) if len(c) > n else None
            out[name] = {"last": round(float(c.iloc[-1]), 4), "chg_1d_pct": pct(1), "chg_5d_pct": pct(5),
                         "chg_20d_pct": pct(20), "vs_ema50": "above" if c.iloc[-1] > ema50 else "below"}
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    out["_note"] = "Yahoo quotes; verify units (e.g. ^TNX) and freshness"
    return out


def regime_hint(m: dict) -> dict:
    """Rule-based risk regime used by the heuristic fallback and passed as a hint to the LLM."""
    def g(k, f, default=0.0):
        v = (m.get(k) or {}).get(f)
        return default if v is None else v
    risk_off = 0
    if g("VIX", "last") > 25: risk_off += 2
    elif g("VIX", "last") > 20: risk_off += 1
    if g("DXY", "chg_20d_pct") > 1: risk_off += 1
    if g("US10Y", "chg_20d_pct") > 3: risk_off += 1
    if (m.get("SPX") or {}).get("vs_ema50") == "below": risk_off += 1
    if (m.get("NDX") or {}).get("vs_ema50") == "below": risk_off += 1
    regime = "RISK_OFF" if risk_off >= 3 else "RISK_ON" if risk_off <= 1 else "NEUTRAL"
    usd_up = g("DXY", "chg_20d_pct") > 0.5
    yld_up = g("US10Y", "chg_20d_pct") > 2
    gold_bias = "BEARISH" if (usd_up and yld_up) else "BULLISH" if (g("DXY", "chg_20d_pct") < -0.5 and not yld_up) else "NEUTRAL"
    return {"risk_off_score": risk_off, "regime": regime, "gold_bias": gold_bias}
