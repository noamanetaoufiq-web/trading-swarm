"""The rule-based SMC strategy as a PURE function of (SMC snapshot, price, params).

Used by:
  * TechnicalAgent.heuristic  (live planning when no LLM / as fallback)
  * Backtester (agent 11)     (historical replay, Monte Carlo, parameter optimisation)
Because it is the same code, what you backtest is what the rule-based planner actually does.
(The LLM Technical agent is discretionary and cannot be replayed bar-by-bar: the Backtester validates the
 strategy family + the specific setup geometry, see README 'What the backtest does and does not prove'.)
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, fields


@dataclass
class Params:
    sl_buffer_atr: float = 0.25
    min_stop_atr: float = 0.6
    tp_cap_r: float = 6.0
    fallback_rr: float = 2.2
    min_rr: float = 2.0
    require_pd_zone: bool = False
    zone_tf: str = "1h"

    @classmethod
    def from_cfg(cls, d) -> "Params":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dict(d or {}).items() if k in names})

    def to_dict(self) -> dict:
        return asdict(self)


def smc_plan(smc: dict, price: float, p: Params) -> dict:
    """Returns {'side': 'LONG'|'SHORT'|'NONE', ...plan fields...}. Uses only 4h structure for bias and p.zone_tf zones."""
    tr = lambda tf: (smc.get(tf) or {}).get("structure", {}).get("trend", "range")
    htf = tr("4h")
    bias = {"bullish": "BULLISH", "bearish": "BEARISH"}.get(htf, "NEUTRAL")
    zt = smc.get(p.zone_tf)
    none = lambda why, c=25: {"side": "NONE", "bias": bias, "why": why, "confidence": c, "reasons": [why]}
    if bias == "NEUTRAL" or not zt or "error" in zt:
        return none("No clear 4h structure / zone timeframe data missing")
    side = "LONG" if bias == "BULLISH" else "SHORT"
    want = "bullish" if side == "LONG" else "bearish"
    atr1 = zt["atr"]
    zones = [("OB", z) for z in zt["order_blocks"] if z["side"] == want] + [("FVG", z) for z in zt["fvg"] if z["side"] == want]
    zones = [x for x in zones if (x[1]["high"] <= price + 0.5 * atr1 if side == "LONG" else x[1]["low"] >= price - 0.5 * atr1)]
    if not zones:
        return none(f"{bias} bias but no {want} OB/FVG at a reachable location", 35)
    pdz = zt.get("premium_discount") or {}
    pd_match = pdz.get("zone") == ("discount" if side == "LONG" else "premium")
    if p.require_pd_zone and not pd_match:
        return none("price not in discount/premium half of the range (require_pd_zone)", 30)
    kind, z = min(zones, key=lambda x: abs(price - (x[1]["low"] + x[1]["high"]) / 2))
    entry = round((z["low"] + z["high"]) / 2, 2)
    sl = z["low"] - p.sl_buffer_atr * atr1 if side == "LONG" else z["high"] + p.sl_buffer_atr * atr1
    if abs(entry - sl) < p.min_stop_atr * atr1:
        sl = entry - p.min_stop_atr * atr1 if side == "LONG" else entry + p.min_stop_atr * atr1
    sl = round(sl, 2)
    risk = abs(entry - sl)
    if risk <= 0:
        return none("degenerate stop distance")
    cands = []
    for t in (smc.get("1h"), smc.get("4h")):
        if not t or "error" in t:
            continue
        cands += [q["level"] for q in t["liquidity_pools"] if q["type"] == ("EQH" if side == "LONG" else "EQL")]
        sw = t["structure"]["last_swing_high" if side == "LONG" else "last_swing_low"]
        if sw:
            cands.append(sw[1])
        pd2 = t.get("premium_discount")
        if pd2:
            cands.append(pd2["range_high" if side == "LONG" else "range_low"])
    cands = sorted([c for c in cands if (c > entry if side == "LONG" else c < entry)], key=lambda c: abs(c - entry))
    tp, note = None, ""
    for c in cands:
        if abs(c - entry) >= p.min_rr * risk:
            tp, note = round(c, 2), "TP at nearest liquidity/swing target"
            break
    cap = max(p.tp_cap_r, p.min_rr)
    if tp is not None and abs(tp - entry) > cap * risk:
        tp = round(entry + cap * risk if side == "LONG" else entry - cap * risk, 2)
        note = f"nearest liquidity target is > {cap}R away: TP capped at {cap}R"
    if tp is None:
        fr = max(p.fallback_rr, p.min_rr)
        tp = round(entry + fr * risk if side == "LONG" else entry - fr * risk, 2)
        note = f"no liquidity target >= {p.min_rr}R: TP set at {fr}R (weaker)"
    conf = 45
    reasons = [f"HTF {bias} (4h structure)", f"Zone: {p.zone_tf} {kind} {z['low']}-{z['high']} ({z['state']})", note]
    if tr(p.zone_tf) == htf:
        conf += 15; reasons.append(f"{p.zone_tf} structure aligned with HTF")
    if pd_match:
        conf += 10; reasons.append(f"price in {pdz['zone']} of {p.zone_tf} range")
    if z["state"] in ("fresh", "open"):
        conf += 8
    want_sweep = "sell_side_sweep" if side == "LONG" else "buy_side_sweep"
    if any(s["type"] == want_sweep for s in zt.get("sweeps", [])):
        conf += 6; reasons.append("recent liquidity sweep in favour")
    if ("bullish" if side == "LONG" else "bearish") in zt.get("rsi_divergence", []):
        conf += 5; reasons.append("RSI divergence in favour")
    if abs(price - entry) > 1.5 * atr1:
        conf -= 10; reasons.append("entry far from price")
    return {"side": side, "bias": bias, "entry": entry, "sl": sl, "tp": tp, "rr": round(abs(tp - entry) / risk, 2),
            "zone": [z["low"], z["high"]], "zone_kind": kind, "zone_tf": p.zone_tf, "zone_state": z["state"],
            "pd_zone": pdz.get("zone"), "confidence": max(0, min(100, conf)), "reasons": reasons}
