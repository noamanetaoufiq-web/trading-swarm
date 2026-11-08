"""Deterministic risk maths used by the Inspector (the LLM never does arithmetic that matters)."""
from __future__ import annotations
import math


def levels_sane(direction: str, entry, sl, tp) -> bool:
    if None in (entry, sl, tp):
        return False
    if direction == "LONG":
        return sl < entry < tp
    if direction == "SHORT":
        return tp < entry < sl
    return False


def rr_ratio(direction: str, entry, sl, tp):
    if not levels_sane(direction, entry, sl, tp):
        return None
    return abs(tp - entry) / abs(entry - sl)


def position_size(equity: float, risk_pct: float, entry: float, sl: float, inst, cm: dict | None = None) -> dict:
    """Lots so that the loss when the stop is hit - INCLUDING spread, stop-exit slippage and commission - stays within the risk budget.
    `cm` = backtest.cost_model(cfg, inst) (price units for spread/slip, account currency per lot for commission). Without it: pure entry-SL distance."""
    dist = abs(entry - sl)
    risk_usd = equity * risk_pct / 100.0
    upp = float(inst.usd_per_point_per_lot)
    extra_price = (float(cm["spread"]) + float(cm["slip"])) if cm else 0.0
    commission = float(cm["comm_per_lot"]) if cm else 0.0
    per_lot_risk = (dist + extra_price) * upp + commission
    if dist <= 0 or per_lot_risk <= 0:
        return {"lots": 0.0, "risk_usd": 0.0, "risk_pct": 0.0, "stop_distance": dist, "note": "invalid stop distance"}
    step = float(inst.lot_step)
    raw = risk_usd / per_lot_risk
    lots = math.floor(raw / step + 1e-9) * step
    lots = round(min(lots, float(inst.max_lot)), 4)
    note = ""
    if lots < float(inst.min_lot):
        lots, note = 0.0, f"risk budget too small for min lot {inst.min_lot} (needs ${float(inst.min_lot) * per_lot_risk:.2f})"
    actual = lots * per_lot_risk
    return {"lots": lots, "risk_usd": round(actual, 2), "risk_pct": round(actual / equity * 100, 3),
            "stop_distance": round(dist, 4), "note": note, "cost_buffer_usd": round(lots * (extra_price * upp + commission), 2),
            "per_lot_risk_all_in": round(per_lot_risk, 2)}
