"""Paper-trade resolution + Critic loop. `python main.py review`"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pandas as pd

from .agents import build_agents
from .context import Ctx
from .journal import Journal
from .tools.market_data import fetch_since


def simulate(rec: dict, df: pd.DataFrame, expiry_hours: float) -> dict:
    """Walk candles after the signal. Conservative: if SL and TP are both inside one candle -> SL first."""
    p, otype = rec["proposal"], (rec.get("orders") or {}).get("order_type", "LIMIT")
    side, entry, sl, tp = p["direction"], p["entry"], p["stop_loss"], p["take_profit"]
    risk = abs(entry - sl)
    rr = abs(tp - entry) / risk
    t0 = pd.Timestamp(rec["ts"])
    filled, mfe, mae, fill_i = False, 0.0, 0.0, None
    for i, (ts, row) in enumerate(df.iterrows()):
        if not filled:
            if ts - t0 > timedelta(hours=expiry_hours):
                return {"status": "EXPIRED", "reason": "limit/stop never filled in time"}
            if otype == "MARKET":
                hit = True
            elif otype == "LIMIT":
                hit = row["low"] <= entry if side == "LONG" else row["high"] >= entry
            else:
                hit = row["high"] >= entry if side == "LONG" else row["low"] <= entry
            if not hit:
                continue
            filled, fill_i = True, i
        fav = ((row["high"] - entry) if side == "LONG" else (entry - row["low"])) / risk
        adv = ((entry - row["low"]) if side == "LONG" else (row["high"] - entry)) / risk
        mfe, mae = max(mfe, fav), max(mae, adv)
        sl_hit = row["low"] <= sl if side == "LONG" else row["high"] >= sl
        tp_hit = row["high"] >= tp if side == "LONG" else row["low"] <= tp
        if sl_hit or tp_hit:
            win = tp_hit and not sl_hit
            return {"status": "CLOSED", "result": "WIN" if win else "LOSS", "r_multiple": round(rr, 2) if win else -1.0,
                    "closed_at": ts.isoformat(), "bars_to_fill": fill_i, "bars_in_trade": i - fill_i,
                    "mfe_r": round(mfe, 2), "mae_r": round(min(mae, 1.0) if not win else mae, 2),
                    "ambiguous_same_candle": bool(sl_hit and tp_hit)}
    return {"status": "OPEN" if filled else "PENDING", "mfe_r": round(mfe, 2), "mae_r": round(mae, 2)}


def review_trades(cfg, llm, demo=False, progress=print) -> dict:
    journal = Journal(cfg.root, demo)
    A = build_agents(cfg, llm)
    out = {"reviewed": [], "stats": None}
    open_rows = [r for r in journal.all() if r.get("status") in ("PENDING", "OPEN") and r.get("mode") == "paper"]
    if not open_rows:
        progress("No open paper trades to review.")
    for rec in open_rows:
        inst = cfg.instruments[rec["symbol"]]
        df = fetch_since(rec["symbol"], inst, "15m", rec["ts"], demo, entry_price=rec["proposal"]["entry"])
        res = simulate(rec, df, float(cfg.risk.pending_expiry_hours))
        if res["status"] in ("PENDING", "OPEN"):
            journal.update(rec["id"], {"status": res["status"], "outcome": res})
            progress(f"{rec['id']}: still {res['status']}")
            continue
        journal.update(rec["id"], {"status": res["status"], "outcome": res})
        if res["status"] != "CLOSED":
            progress(f"{rec['id']}: {res['status']} ({res.get('reason')})")
            continue
        rec = {**rec, "outcome": res}
        ctx = Ctx(symbol=rec["symbol"], inst=inst, cfg=cfg, query="post-trade review", demo=demo,
                  equity=cfg.account.equity, now=datetime.now(timezone.utc), world={})
        ctx.extra.update({"trade": rec, "outcome": res})
        rep = A["critic"].run(ctx)
        journal.add_lessons({"trade_id": rec["id"], "symbol": rec["symbol"], "root_cause": rep.root_cause,
                             "blamed_agents": rep.blamed_agents, "lessons": rep.lessons,
                             "parameter_feedback": [f.model_dump() for f in rep.parameter_feedback]})
        journal.update(rec["id"], {"critic": rep.model_dump()})
        progress(f"{rec['id']}: {res['result']} {res['r_multiple']}R -> {rep.root_cause}; lessons: {rep.lessons}")
        out["reviewed"].append({"id": rec["id"], "result": res["result"], "critic": rep.model_dump()})
    out["stats"] = journal.stats()
    return out
