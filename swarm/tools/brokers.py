"""Broker-ready payload builders (MetaTrader5, Binance USD-M futures, Interactive Brokers) + gated submission.

Default = PAPER. Live sending requires ALL of: execution.mode=live in config, --live CLI flag,
and LIVE_TRADING=YES in the environment. Live code paths are UNTESTED - try on a demo account first.
"""
from __future__ import annotations
import os

MT5_ORDER_TYPE = {("LONG", "MARKET"): 0, ("SHORT", "MARKET"): 1, ("LONG", "LIMIT"): 2, ("SHORT", "LIMIT"): 3,
                  ("LONG", "STOP"): 4, ("SHORT", "STOP"): 5}


def build_mt5(inst, p, lots, order_type, magic, comment="swarm") -> dict:
    pending = order_type != "MARKET"
    return {
        "broker": "mt5",
        "request": {
            "action": 5 if pending else 1,                 # TRADE_ACTION_PENDING / TRADE_ACTION_DEAL
            "symbol": inst.mt5_symbol,
            "volume": lots,
            "type": MT5_ORDER_TYPE[(p.direction, order_type)],
            "price": p.entry if pending else None,         # None -> filled with live ask/bid at send time
            "sl": p.stop_loss, "tp": p.take_profit,
            "deviation": 20, "magic": magic, "comment": comment[:30],
            "type_time": 0,                                 # ORDER_TIME_GTC
            "type_filling": 1,                              # ORDER_FILLING_IOC (check your broker)
        },
    }


def build_binance(inst, p, lots, order_type) -> dict:
    side, opp = ("BUY", "SELL") if p.direction == "LONG" else ("SELL", "BUY")
    otype = {"LIMIT": "LIMIT", "MARKET": "MARKET", "STOP": "STOP_MARKET"}[order_type]
    entry = {"symbol": inst.binance_symbol, "side": side, "type": otype, "quantity": round(lots, 3)}
    if order_type == "LIMIT":
        entry.update({"price": round(p.entry, 1), "timeInForce": "GTC"})
    if order_type == "STOP":
        entry.update({"stopPrice": round(p.entry, 1)})
    return {
        "broker": "binance_usdm",
        "endpoint": "POST /fapi/v1/order",
        "orders": [
            {**entry, "place": "now"},
            {"symbol": inst.binance_symbol, "side": opp, "type": "STOP_MARKET", "stopPrice": round(p.stop_loss, 1),
             "closePosition": "true", "workingType": "MARK_PRICE", "place": "after_entry_fill"},
            {"symbol": inst.binance_symbol, "side": opp, "type": "TAKE_PROFIT_MARKET", "stopPrice": round(p.take_profit, 1),
             "closePosition": "true", "workingType": "MARK_PRICE", "place": "after_entry_fill"},
        ],
    }


def build_ibkr(inst, p, lots, order_type) -> dict:
    spec = dict(inst.ibkr)
    qty_per_lot = float(spec.pop("qty_per_lot", 1))
    qty = lots * qty_per_lot
    qty = max(1, round(qty)) if spec.get("secType") == "FUT" else round(qty, 2)
    action, opp = ("BUY", "SELL") if p.direction == "LONG" else ("SELL", "BUY")
    parent_type = {"LIMIT": "LMT", "MARKET": "MKT", "STOP": "STP"}[order_type]
    parent = {"action": action, "orderType": parent_type, "totalQuantity": qty, "transmit": False}
    if order_type == "LIMIT":
        parent["lmtPrice"] = p.entry
    if order_type == "STOP":
        parent["auxPrice"] = p.entry
    return {
        "broker": "ibkr",
        "note": "ib_insync bracket: set parentId on children; transmit=True on the last child only",
        "contract": spec,
        "bracket": [
            parent,
            {"action": opp, "orderType": "LMT", "lmtPrice": p.take_profit, "totalQuantity": qty, "transmit": False},
            {"action": opp, "orderType": "STP", "auxPrice": p.stop_loss, "totalQuantity": qty, "transmit": True},
        ],
    }


BUILDERS = {"mt5": lambda i, p, l, o, m: build_mt5(i, p, l, o, m),
            "binance": lambda i, p, l, o, m: build_binance(i, p, l, o),
            "ibkr": lambda i, p, l, o, m: build_ibkr(i, p, l, o)}


def submit(payload: dict, mode: str, allow_live: bool) -> dict:
    """Paper: accepted locally. Live: triple-gated."""
    if mode != "live":
        return {"status": "PAPER_ACCEPTED", "broker": payload["broker"]}
    if not (allow_live and os.getenv("LIVE_TRADING") == "YES"):
        return {"status": "LIVE_BLOCKED", "reason": "need --live flag AND LIVE_TRADING=YES env"}
    if payload["broker"] == "mt5":
        from . import accounts
        try:
            with accounts.mt5_session() as mt5:                 # same login / password / server / path as the dashboard account
                req = dict(payload["request"])
                mt5.symbol_select(req["symbol"], True)
                if req["price"] is None:
                    tick = mt5.symbol_info_tick(req["symbol"])
                    req["price"] = tick.ask if req["type"] == 0 else tick.bid
                res = mt5.order_send(req)
                return {"status": "LIVE_SENT", "retcode": getattr(res, "retcode", None), "comment": getattr(res, "comment", "")}
        except RuntimeError as e:
            return {"status": "ERROR", "reason": str(e)}
    if payload["broker"] == "binance_usdm":
        import ccxt
        ex = ccxt.binanceusdm({"apiKey": os.environ["BINANCE_API_KEY"], "secret": os.environ["BINANCE_API_SECRET"]})
        sym = payload["orders"][0]["symbol"].replace("USDT", "/USDT")
        results = []
        for o in payload["orders"]:
            params = {k: v for k, v in o.items() if k in ("stopPrice", "closePosition", "workingType", "timeInForce")}
            typ = o["type"].lower() if o["type"] in ("LIMIT", "MARKET") else o["type"]
            results.append(ex.create_order(sym, typ, o["side"].lower(), o.get("quantity"), o.get("price"), params))
        return {"status": "LIVE_SENT", "orders": len(results)}
    return {"status": "UNSUPPORTED", "reason": "IBKR: use the payload with ib_insync manually"}
