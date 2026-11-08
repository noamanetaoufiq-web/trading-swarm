"""Market data: real sources (ccxt / yfinance), cleaning, snapshot building, and a synthetic generator for --demo."""
from __future__ import annotations
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .indicators import atr

TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
DEMO_ANCHORS = {"XAUUSD": 3944.0, "BTCUSD": 78050.0, "NAS100": 28470.0}
DEMO_VOL = {"commodity": 0.16, "crypto": 0.55, "index": 0.24, "forex": 0.08}
COLS = ["open", "high", "low", "close", "volume"]


@dataclass
class MarketData:
    symbol: str
    frames: dict = field(default_factory=dict)    # tf -> cleaned DataFrame
    quality: dict = field(default_factory=dict)   # tf -> cleaning report
    last_price: float = 0.0
    spread: float = 0.0
    atr: dict = field(default_factory=dict)
    levels: dict = field(default_factory=dict)
    fetched_at: str = ""
    demo: bool = False


# --------------------------------------------------------------------------- synthetic (demo)
def synthetic_ohlcv(symbol, asset_class, tf, n=600, anchor=None, forward_from=None, salt=0, end=None):
    """Random-walk OHLCV with slow trend waves and displacement candles.
    forward_from=(ts, price): generate n bars AFTER ts starting near price (used by the Critic demo)."""
    rng = np.random.default_rng(zlib.crc32(f"{symbol}|{tf}|{salt}".encode()))
    mins = TF_MIN[tf]
    sigma = DEMO_VOL.get(asset_class, 0.2) * np.sqrt(mins / (365 * 24 * 60))
    t = np.arange(n)
    drift = 0.25 * sigma * np.sin(2 * np.pi * t / (n / 3.0) + rng.uniform(0, 6.28))
    shocks = rng.standard_normal(n) * sigma
    jumps = rng.random(n) < 0.03
    shocks[jumps] *= rng.uniform(2.5, 4.0, int(jumps.sum()))
    path = np.exp(np.cumsum(drift + shocks))
    if forward_from:
        start_ts, start_price = forward_from
        close = start_price * path
        idx = pd.date_range(start=pd.Timestamp(start_ts) + pd.Timedelta(minutes=mins), periods=n, freq=f"{mins}min", tz="UTC")
        first_open = start_price
    else:
        anchor = anchor or DEMO_ANCHORS.get(symbol, 100.0)
        close = path * anchor / path[-1]
        end = end or pd.Timestamp.now(tz="UTC").floor(f"{mins}min")
        idx = pd.date_range(end=end, periods=n, freq=f"{mins}min", tz="UTC")
        first_open = close[0]
    open_ = np.concatenate([[first_open], close[:-1]])
    wick_u = np.abs(rng.standard_normal(n)) * sigma * 0.5 * close
    wick_d = np.abs(rng.standard_normal(n)) * sigma * 0.5 * close
    high = np.maximum(open_, close) + wick_u
    low = np.minimum(open_, close) - wick_d
    vol = rng.lognormal(10, 0.4, n) * (1 + 2 * jumps)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


# --------------------------------------------------------------------------- real sources
def _yahoo(ticker: str, tf: str, limit: int) -> pd.DataFrame:
    import yfinance as yf
    interval = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "4h": "60m", "1d": "1d"}[tf]
    period = {"1m": "5d", "5m": "30d", "15m": "30d", "1h": "180d", "4h": "360d", "1d": "3y"}[tf]
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"yahoo returned no data for {ticker} {tf}")
    df = df.rename(columns=str.lower)[COLS]
    df.index = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
    if tf == "4h":
        df = df.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df.tail(limit)


def _ccxt(exchange_id: str, symbol: str, tf: str, limit: int) -> pd.DataFrame:
    import ccxt
    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(raw, columns=["ts"] + COLS)
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop(columns="ts")


def _mt5_rates(inst, tf: str, limit: int) -> pd.DataFrame:
    """Candles straight from the linked MT5 account (the broker's own prices, incl. its symbol name)."""
    from . import accounts
    with accounts.mt5_session() as mt5:
        tfs = {"1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5, "15m": mt5.TIMEFRAME_M15, "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4, "1d": mt5.TIMEFRAME_D1}
        name = inst.get("mt5_symbol")
        mt5.symbol_select(name, True)
        rates = mt5.copy_rates_from_pos(name, tfs[tf], 0, int(limit))
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 returned no {tf} bars for {name}: {mt5.last_error()}")
        d = pd.DataFrame(rates)
        d.index = pd.to_datetime(d["time"], unit="s", utc=True)
        d["volume"] = d["tick_volume"] if "tick_volume" in d else d.get("real_volume", 0.0)
        return d[COLS]


def fetch_ohlcv(symbol: str, inst, tf: str, limit: int = 500, demo: bool = False) -> pd.DataFrame:
    if demo:
        return synthetic_ohlcv(symbol, inst.asset_class, tf, n=limit)
    if inst.get("feed") == "mt5":
        try:
            return _mt5_rates(inst, tf, limit)
        except Exception:
            pass  # broker feed unavailable -> fall back to the public feed below (the Inspector warns about proxy feeds)
    if inst.get("ccxt_exchange"):
        try:
            return _ccxt(inst.ccxt_exchange, inst.ccxt_symbol, tf, limit)
        except Exception:
            pass  # fall back to yahoo
    return _yahoo(inst.yahoo, tf, limit)


def fetch_since(symbol: str, inst, tf: str, since_ts, demo: bool = False, entry_price: float | None = None, n: int = 192):
    """Candles after `since_ts` (used by the paper-trade review / Critic)."""
    if demo:
        return synthetic_ohlcv(symbol, inst.asset_class, tf, n=n, forward_from=(since_ts, entry_price or 100.0), salt=7)
    df = fetch_ohlcv(symbol, inst, tf, limit=1000, demo=False)
    df, _ = clean_ohlcv(df, tf)
    return df[df.index > pd.Timestamp(since_ts)]


def fetch_spread(inst, demo: bool = False) -> float:
    if not demo and inst.get("feed") == "mt5":
        try:
            from . import accounts
            with accounts.mt5_session() as mt5:
                t = mt5.symbol_info_tick(inst.get("mt5_symbol"))
                if t is not None and t.ask and t.bid and t.ask >= t.bid:
                    return float(t.ask - t.bid)
        except Exception:
            pass
    if not demo and inst.get("ccxt_exchange"):
        try:
            import ccxt
            t = getattr(ccxt, inst.ccxt_exchange)({"enableRateLimit": True}).fetch_ticker(inst.ccxt_symbol)
            if t.get("bid") and t.get("ask"):
                return float(t["ask"] - t["bid"])
        except Exception:
            pass
    return float(inst.typical_spread)


# --------------------------------------------------------------------------- cleaning
def clean_ohlcv(df: pd.DataFrame, tf: str):
    rep = {"rows_in": int(len(df))}
    df = df[~df.index.duplicated(keep="last")].sort_index()[COLS].astype(float)
    n0 = len(df)
    df = df.dropna(subset=["open", "high", "low", "close"])
    rep["dropped_nan"] = int(n0 - len(df))
    n0 = len(df)
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    rep["dropped_nonpositive"] = int(n0 - len(df))
    df = df.copy()
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
    ret = df["close"].pct_change()
    mad = (ret - ret.median()).abs().median() * 1.4826
    rep["dropped_spikes"] = 0
    if len(df) > 50 and mad and mad > 0:
        z = ret.abs() / mad
        reverts = (np.sign(ret) != np.sign(ret.shift(-1))) & (ret.shift(-1).abs() > 0.6 * ret.abs())
        spike = (z > 15) & reverts
        rep["dropped_spikes"] = int(spike.sum())
        df = df[~spike]
    mins = TF_MIN[tf]
    dt = df.index.to_series().diff().dt.total_seconds() / 60
    rep["gaps_gt_3_bars"] = int((dt > mins * 3.5).sum())  # weekends/holidays count for non-24/7 markets
    rep["rows_out"] = int(len(df))
    rep["last_ts"] = str(df.index[-1]) if len(df) else None
    rep["minutes_since_last_candle"] = round((pd.Timestamp.now(tz="UTC") - df.index[-1]).total_seconds() / 60, 1) if len(df) else None
    return df, rep


# --------------------------------------------------------------------------- snapshot
def build_market_data(symbol: str, inst, timeframes: list, demo: bool = False, limit: int = 500) -> MarketData:
    md = MarketData(symbol=symbol, demo=demo, fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def one(tf):
        try:
            raw = fetch_ohlcv(symbol, inst, tf, limit, demo)
            df, rep = clean_ohlcv(raw, tf)
            return tf, df, rep
        except Exception as e:  # noqa: BLE001
            return tf, None, {"error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=max(1, len(timeframes))) as ex:
        for tf, df, rep in ex.map(one, timeframes):
            md.quality[tf] = rep
            if df is not None and len(df):
                md.frames[tf] = df
                md.atr[tf] = float(atr(df).iloc[-1]) if len(df) > 15 else None
    if not md.frames:
        raise RuntimeError(f"No market data available for {symbol}: {md.quality}")
    fastest = min(md.frames, key=lambda t: TF_MIN[t])
    md.last_price = float(md.frames[fastest]["close"].iloc[-1])
    md.spread = fetch_spread(inst, demo)
    d1 = md.frames.get("1d")
    if d1 is not None and len(d1) >= 7:
        md.levels = {
            "today_high": round(float(d1["high"].iloc[-1]), 4), "today_low": round(float(d1["low"].iloc[-1]), 4),
            "prev_day_high": round(float(d1["high"].iloc[-2]), 4), "prev_day_low": round(float(d1["low"].iloc[-2]), 4),
            "5d_high": round(float(d1["high"].iloc[-5:].max()), 4), "5d_low": round(float(d1["low"].iloc[-5:].min()), 4),
            "20d_high": round(float(d1["high"].iloc[-20:].max()), 4), "20d_low": round(float(d1["low"].iloc[-20:].min()), 4),
        }
    return md


def market_summary(md: MarketData) -> dict:
    out = {"symbol": md.symbol, "last_price": round(md.last_price, 4), "spread": round(md.spread, 4),
           "spread_pct": round(md.spread / md.last_price * 100, 5), "fetched_at": md.fetched_at,
           "levels": md.levels, "timeframes": {}}
    for tf, df in md.frames.items():
        a = atr(df)
        avg = a.rolling(50).mean().iloc[-1] if len(a) >= 60 else np.nan
        out["timeframes"][tf] = {
            "candles": int(len(df)), "last_close": round(float(df["close"].iloc[-1]), 4),
            "last_high": round(float(df["high"].iloc[-1]), 4), "last_low": round(float(df["low"].iloc[-1]), 4),
            "atr14": round(float(a.iloc[-1]), 4), "atr_pct": round(float(a.iloc[-1] / df["close"].iloc[-1] * 100), 4),
            "atr_vs_50bar_avg": None if np.isnan(avg) else round(float(a.iloc[-1] / avg), 2),
            "avg_volume_20": round(float(df["volume"].tail(20).mean()), 2), "last_volume": round(float(df["volume"].iloc[-1]), 2),
            "quality": md.quality.get(tf),
        }
    return out
