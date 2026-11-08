"""Smart Money Concepts detection (algorithmic approximations):
swings, BOS/CHoCH market structure, Fair Value Gaps, Order Blocks, liquidity pools (EQH/EQL),
liquidity sweeps, premium/discount (+OTE zones).

NOTE: SMC is partly discretionary. These are transparent, rule-based definitions so the
Technical agent has consistent inputs. Review the rules in this file and adapt them to your style.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from .indicators import atr, ema, rsi, rsi_divergence


def find_swings(df: pd.DataFrame, left: int = 3, right: int = 3):
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    sh, sl = [], []
    for i in range(left, len(df) - right):
        if h[i] > h[i - left:i].max() and h[i] >= h[i + 1:i + right + 1].max():
            sh.append((i, float(h[i])))
        if l[i] < l[i - left:i].min() and l[i] <= l[i + 1:i + right + 1].min():
            sl.append((i, float(l[i])))
    return sh, sl


def market_structure(df: pd.DataFrame, sh: list, sl: list, right: int = 3) -> dict:
    """Sequential walk. A swing is only 'known' `right` bars after it forms (no look-ahead).
    Close above last swing high = bullish break (BOS if trend already bullish, CHoCH if it was bearish)."""
    c = df["close"].to_numpy()
    idx = df.index
    events, trend = [], "range"
    last_h = last_l = None
    h_broken = l_broken = True
    ih = il = 0
    for k in range(len(df)):
        while ih < len(sh) and sh[ih][0] + right <= k:
            last_h, h_broken, ih = sh[ih], False, ih + 1
        while il < len(sl) and sl[il][0] + right <= k:
            last_l, l_broken, il = sl[il], False, il + 1
        if last_h and not h_broken and c[k] > last_h[1]:
            events.append({"type": "CHoCH" if trend == "bearish" else "BOS", "direction": "bullish",
                           "level": round(last_h[1], 4), "bar": k, "time": str(idx[k])})
            trend, h_broken = "bullish", True
        if last_l and not l_broken and c[k] < last_l[1]:
            events.append({"type": "CHoCH" if trend == "bullish" else "BOS", "direction": "bearish",
                           "level": round(last_l[1], 4), "bar": k, "time": str(idx[k])})
            trend, l_broken = "bearish", True
    return {
        "trend": trend,
        "events": events[-6:],
        "last_swing_high": [str(idx[sh[-1][0]]), round(sh[-1][1], 4)] if sh else None,
        "last_swing_low": [str(idx[sl[-1][0]]), round(sl[-1][1], 4)] if sl else None,
    }


def fair_value_gaps(df: pd.DataFrame, atr_s: pd.Series, max_age: int = 250, min_gap_atr: float = 0.1) -> list[dict]:
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    n = len(df)
    out = []
    for i in range(max(2, n - max_age), n):
        a = atr_s.iloc[i]
        a = 0.0 if np.isnan(a) else float(a)
        if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= min_gap_atr * a:
            lo, hi, side = float(h[i - 2]), float(l[i]), "bullish"
            later = l[i + 1:]
            filled = later.size > 0 and later.min() <= lo
            partial = later.size > 0 and later.min() < hi
        elif h[i] < l[i - 2] and (l[i - 2] - h[i]) >= min_gap_atr * a:
            lo, hi, side = float(h[i]), float(l[i - 2]), "bearish"
            later = h[i + 1:]
            filled = later.size > 0 and later.max() >= hi
            partial = later.size > 0 and later.max() > lo
        else:
            continue
        if filled:
            continue
        out.append({"side": side, "low": round(lo, 4), "high": round(hi, 4), "bar": i - 1,
                    "time": str(df.index[i - 1]), "state": "partially_filled" if partial else "open"})
    return out[-8:]


def order_blocks(df: pd.DataFrame, atr_s: pd.Series, lookback: int = 300, disp_mult: float = 1.5) -> list[dict]:
    """Bullish OB = last bearish candle (within 5 bars) before a bullish displacement candle
    (body > disp_mult x ATR) that closes above the prior 10-bar high. Mirrored for bearish.
    Invalidated if a later close breaks through the whole block."""
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    out = []
    for i in range(max(12, n - lookback), n):
        a = atr_s.iloc[i]
        if np.isnan(a) or a <= 0:
            continue
        if abs(c[i] - o[i]) < disp_mult * a:
            continue
        if c[i] > o[i] and c[i] > h[i - 10:i].max():
            for j in range(i - 1, max(i - 6, 0), -1):
                if c[j] < o[j]:
                    lo, hi = float(l[j]), float(h[j])
                    later_l, later_c = l[i + 1:], c[i + 1:]
                    broken = later_c.size > 0 and later_c.min() < lo
                    tapped = later_l.size > 0 and later_l.min() <= hi
                    if not broken:
                        out.append({"side": "bullish", "low": round(lo, 4), "high": round(hi, 4), "bar": j,
                                    "time": str(df.index[j]), "state": "tapped" if tapped else "fresh"})
                    break
        elif c[i] < o[i] and c[i] < l[i - 10:i].min():
            for j in range(i - 1, max(i - 6, 0), -1):
                if c[j] > o[j]:
                    lo, hi = float(l[j]), float(h[j])
                    later_h, later_c = h[i + 1:], c[i + 1:]
                    broken = later_c.size > 0 and later_c.max() > hi
                    tapped = later_h.size > 0 and later_h.max() >= lo
                    if not broken:
                        out.append({"side": "bearish", "low": round(lo, 4), "high": round(hi, 4), "bar": j,
                                    "time": str(df.index[j]), "state": "tapped" if tapped else "fresh"})
                    break
    return out[-6:]


def liquidity_pools(sh: list, sl: list, atr_last: float, price: float, tol_mult: float = 0.15) -> list[dict]:
    """Equal highs / equal lows: swing points within tol_mult x ATR of each other."""
    pools = []

    def cluster(points, kind):
        pts = sorted(points, key=lambda x: x[1])
        used = set()
        for a in range(len(pts)):
            if a in used:
                continue
            grp = [pts[a]]
            for b in range(a + 1, len(pts)):
                if b not in used and abs(pts[b][1] - grp[-1][1]) <= tol_mult * atr_last:
                    grp.append(pts[b])
                    used.add(b)
            if len(grp) >= 2:
                pools.append({"type": kind, "level": round(float(np.mean([p[1] for p in grp])), 4), "touches": len(grp)})

    cluster(sh[-20:], "EQH")
    cluster(sl[-20:], "EQL")
    for p in pools:
        p["distance"] = round(p["level"] - price, 4)
    return sorted(pools, key=lambda p: abs(p["distance"]))[:6]


def liquidity_sweeps(df: pd.DataFrame, sh: list, sl: list, lookback: int = 20, right: int = 3) -> list[dict]:
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    out, seen = [], set()
    for k in range(max(0, n - lookback), n):
        for p in [p for p in sh if p[0] + right <= k][-3:]:
            if h[k] > p[1] > c[k] and ("bs", p[1]) not in seen:
                seen.add(("bs", p[1]))
                out.append({"type": "buy_side_sweep", "level": round(p[1], 4), "bar": k, "time": str(df.index[k])})
        for p in [p for p in sl if p[0] + right <= k][-3:]:
            if l[k] < p[1] < c[k] and ("ss", p[1]) not in seen:
                seen.add(("ss", p[1]))
                out.append({"type": "sell_side_sweep", "level": round(p[1], 4), "bar": k, "time": str(df.index[k])})
    return out[-4:]


def premium_discount(price: float, sh: list, sl: list):
    if not sh or not sl:
        return None
    hi, lo = sh[-1][1], sl[-1][1]
    if hi <= lo:
        hi, lo = max(p for _, p in sh[-3:]), min(p for _, p in sl[-3:])
    if hi <= lo:
        return None
    rng = hi - lo
    pos = (price - lo) / rng
    zone = "premium" if pos > 0.55 else "discount" if pos < 0.45 else "equilibrium"
    return {"range_high": round(hi, 4), "range_low": round(lo, 4), "equilibrium": round((hi + lo) / 2, 4),
            "position_pct": round(pos * 100, 1), "zone": zone,
            "ote_long": [round(lo + 0.21 * rng, 4), round(lo + 0.38 * rng, 4)],
            "ote_short": [round(hi - 0.38 * rng, 4), round(hi - 0.21 * rng, 4)]}


def analyze_tf(df: pd.DataFrame, left: int = 3, right: int = 3) -> dict:
    if len(df) < 40:
        return {"error": "insufficient data", "candles": len(df)}
    a = atr(df)
    r = rsi(df["close"])
    sh, sl = find_swings(df, left, right)
    price, atr_last = float(df["close"].iloc[-1]), float(a.iloc[-1])
    e20, e50, e200 = (float(ema(df["close"], n).iloc[-1]) for n in (20, 50, 200))
    return {
        "price": round(price, 4),
        "atr": round(atr_last, 4),
        "structure": market_structure(df, sh, sl, right),
        "fvg": fair_value_gaps(df, a),
        "order_blocks": order_blocks(df, a),
        "liquidity_pools": liquidity_pools(sh, sl, atr_last, price),
        "sweeps": liquidity_sweeps(df, sh, sl, right=right),
        "premium_discount": premium_discount(price, sh, sl),
        "rsi": round(float(r.iloc[-1]), 1),
        "rsi_divergence": rsi_divergence(r, sh, sl),
        "ema": {"ema20": round(e20, 4), "ema50": round(e50, 4), "ema200": round(e200, 4),
                "price_vs_ema50": "above" if price > e50 else "below",
                "stack": "bullish" if e20 > e50 > e200 else "bearish" if e20 < e50 < e200 else "mixed"},
        "recent_swing_highs": [[str(df.index[i]), round(p, 4)] for i, p in sh[-4:]],
        "recent_swing_lows": [[str(df.index[i]), round(p, 4)] for i, p in sl[-4:]],
    }
