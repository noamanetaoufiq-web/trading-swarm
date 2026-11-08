"""Backtesting engine: no-look-ahead replay of the SMC strategy, Monte Carlo bootstrap, walk-forward
parameter optimisation, and the gate maths. Pure numpy/pandas (no external backtest library).

Conventions
  * All results are in R (multiples of the initial risk). Costs (spread) are charged in R per trade.
  * Entries are limit/stop orders at the plan's entry; same-candle SL+TP => SL first (conservative).
  * One position / pending order at a time per symbol (no overlapping trades).
"""
from __future__ import annotations
import itertools
import json
import math
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..strategy import Params, smc_plan
from .market_data import clean_ohlcv, synthetic_ohlcv, _ccxt, _yahoo
from .smc import analyze_tf

W1 = 300        # 1h bars given to the SMC analysis at each evaluation
W4H = 600       # 1h bars resampled to 4h for the HTF bias
FIRST = 620     # first evaluation index (needs enough history)
GRID = {"sl_buffer_atr": [0.15, 0.25, 0.4], "min_stop_atr": [0.6, 1.0], "tp_cap_r": [3.0, 6.0], "require_pd_zone": [False, True]}


# ----------------------------------------------------------------------------- history
def load_history_csv(path: str) -> pd.DataFrame:
    """Broker export (MT5 'Export bars' / any CSV): columns time|date|datetime, open, high, low, close, [volume|tick_volume]. Resampled to 1h if finer."""
    raw = pd.read_csv(path, sep=None, engine="python")
    raw.columns = [str(c).strip().lower().strip("<>") for c in raw.columns]
    if "date" in raw.columns and "time" in raw.columns:               # MT5 style: <DATE> <TIME>
        raw["time"] = raw["date"].astype(str).str.replace(".", "-", regex=False) + " " + raw["time"].astype(str)
    tcol = next((c for c in ("time", "datetime", "date", "timestamp") if c in raw.columns), None)
    if tcol is None:
        raise ValueError("csv needs a time/date/datetime column")
    idx = pd.to_datetime(raw[tcol], utc=True, errors="coerce")
    vol = next((c for c in ("volume", "tick_volume", "vol", "tickvol") if c in raw.columns), None)
    df = pd.DataFrame({"open": raw["open"], "high": raw["high"], "low": raw["low"], "close": raw["close"],
                       "volume": raw[vol] if vol else 0.0}).set_index(idx)
    df = df[~df.index.isna()].sort_index()
    df = df[~df.index.duplicated()].astype(float)
    if len(df) > 3 and (df.index[1:] - df.index[:-1]).median() < pd.Timedelta(minutes=59):
        df = df.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df


def _mt5_history(inst, bars: int) -> pd.DataFrame:
    """1h history from the linked MT5 account (its own symbol, its own prices). UNTESTED against a real terminal; unit-tested with a stub."""
    from . import accounts
    with accounts.mt5_session() as mt5:
        name = inst.get("mt5_symbol")
        mt5.symbol_select(name, True)
        rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_H1, 0, int(bars))
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 returned no bars for {name}: {mt5.last_error()}")
        d = pd.DataFrame(rates)
        d.index = pd.to_datetime(d["time"], unit="s", utc=True)
        d["volume"] = d["tick_volume"] if "tick_volume" in d else 0.0
        return d[["open", "high", "low", "close", "volume"]]


def feed_info(inst, df=None) -> dict:
    feed = inst.get("feed", "yahoo") if hasattr(inst, "get") else "yahoo"
    used = (df.attrs.get("feed_used") if df is not None else None) or feed
    fb = (df.attrs.get("fallback_reason") if df is not None else "") or ""
    proxy = bool(inst.get("feed_is_proxy", False)) or bool(fb)
    return {"feed": used, "configured": feed, "proxy": proxy, "fallback_reason": fb,
            "source": inst.get("history_csv") if feed == "csv" else inst.get("yahoo") if feed == "yahoo" else inst.get("mt5_symbol")}


def fetch_history(symbol, inst, bars: int, demo: bool) -> pd.DataFrame:
    if demo:
        return synthetic_ohlcv(symbol, inst.asset_class, "1h", n=bars, salt=11)
    feed = inst.get("feed", "yahoo")
    if feed in ("csv", "mt5"):
        try:
            raw = load_history_csv(inst.history_csv) if feed == "csv" else _mt5_history(inst, bars)
            df, _ = clean_ohlcv(raw, "1h")
            df = df.tail(bars)
            df.attrs["feed_used"] = feed
            return df
        except Exception as e:  # noqa: BLE001
            fallback_reason = f"{feed} feed failed ({type(e).__name__}: {str(e)[:160]}) -> public feed used instead"
    else:
        fallback_reason = ""
    df = _fetch_public_history(symbol, inst, bars)
    df.attrs["feed_used"] = "yahoo/ccxt"
    df.attrs["fallback_reason"] = fallback_reason
    return df


def _fetch_public_history(symbol, inst, bars: int) -> pd.DataFrame:
    df = None
    if inst.get("ccxt_exchange"):
        try:
            import ccxt
            ex = getattr(ccxt, inst.ccxt_exchange)({"enableRateLimit": True})
            tf_ms = 3600_000
            since = ex.milliseconds() - bars * tf_ms
            rows = []
            while len(rows) < bars:
                chunk = ex.fetch_ohlcv(inst.ccxt_symbol, timeframe="1h", since=since, limit=1000)
                if not chunk:
                    break
                rows += chunk
                since = chunk[-1][0] + tf_ms
                if len(chunk) < 2:
                    break
            d = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
            d.index = pd.to_datetime(d["ts"], unit="ms", utc=True)
            df = d.drop(columns="ts")
        except Exception:
            df = None
    if df is None:
        import yfinance as yf
        h = yf.Ticker(inst.yahoo).history(period="730d", interval="60m", auto_adjust=False)
        h = h.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        h.index = h.index.tz_convert("UTC") if h.index.tz is not None else h.index.tz_localize("UTC")
        df = h
    df, _ = clean_ohlcv(df, "1h")
    return df.tail(bars)


def resample_4h(df):
    return df.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def build_snapshots(df: pd.DataFrame, step: int, progress=None) -> list[dict]:
    snaps = []
    n = len(df)
    idxs = list(range(FIRST, n - 2, step))
    for k, t in enumerate(idxs):
        s1 = analyze_tf(df.iloc[t - W1 + 1:t + 1])
        s4 = analyze_tf(resample_4h(df.iloc[t - W4H + 1:t + 1]))
        snaps.append({"t": t, "price": float(df["close"].iloc[t]), "smc": {"1h": s1, "4h": s4}})
        if progress and k % 100 == 0:
            progress(f"    snapshots {k}/{len(idxs)}")
    return snaps


# ----------------------------------------------------------------------------- costs
def cost_model(cfg, inst) -> dict:
    """All frictions in price/USD units; converted to R per trade by trade_cost_r()."""
    c = cfg.get("costs", {}) or {}
    return {"spread": float(inst.typical_spread) * float(cfg.backtest.cost_spread_mult),
            "slip": float(inst.get("typical_slippage", 0.0)) if c.get("slippage_stop_entry", True) else 0.0,
            "comm_per_lot": float(c.get("commission_per_lot_usd", 0.0)),
            "swap_day": float(inst.get("swap_per_lot_per_day_usd", 0.0)),
            "usd_per_pt": float(inst.usd_per_point_per_lot)}


def as_cost_model(x) -> dict:
    if isinstance(x, dict):
        return x
    return {"spread": float(x), "slip": 0.0, "comm_per_lot": 0.0, "swap_day": 0.0, "usd_per_pt": 1.0}


def trade_cost_r(cm: dict, risk: float, bars_held: float, stop_entry: bool) -> float:
    """Spread + (stop-entry slippage) + commission + swap, in R. (Stop-loss exit slippage is added on losing trades by the caller.)"""
    c = cm["spread"] / risk
    if stop_entry:
        c += cm["slip"] / risk
    c += cm["comm_per_lot"] / (risk * cm["usd_per_pt"])
    c += cm["swap_day"] * (bars_held / 24.0) / (risk * cm["usd_per_pt"])
    return c


# ----------------------------------------------------------------------------- simulation
def sim_trade(H, L, C, t, plan, expiry, max_hold, spread):
    """`spread` may be a plain number (spread only) or a cost_model() dict (spread + slippage + commission + swap)."""
    cm = as_cost_model(spread)
    side, entry, sl, tp = plan["side"], plan["entry"], plan["sl"], plan["tp"]
    price, n = C[t], len(H)
    risk = abs(entry - sl)
    rr = abs(tp - entry) / risk
    limit = (entry <= price) if side == "LONG" else (entry >= price)
    fill = None
    for j in range(t + 1, min(t + 1 + expiry, n)):
        if side == "LONG":
            hit = L[j] <= entry if limit else H[j] >= entry
        else:
            hit = H[j] >= entry if limit else L[j] <= entry
        if hit:
            fill = j
            break
    if fill is None:
        return {"filled": False, "exit_idx": t + expiry}
    mfe = mae = 0.0
    end = min(fill + max_hold, n)
    for j in range(fill, end):
        if side == "LONG":
            fav, adv, sl_hit, tp_hit = (H[j] - entry) / risk, (entry - L[j]) / risk, L[j] <= sl, H[j] >= tp
        else:
            fav, adv, sl_hit, tp_hit = (entry - L[j]) / risk, (H[j] - entry) / risk, H[j] >= sl, L[j] <= tp
        mfe, mae = max(mfe, fav), max(mae, adv)
        if sl_hit or tp_hit:
            win = tp_hit and not sl_hit
            cost_r = trade_cost_r(cm, risk, j - fill, not limit)
            r = (rr - cost_r) if win else (-1.0 - cm["slip"] / risk - cost_r)       # a stop-loss exit also pays slippage
            return {"filled": True, "r": r, "outcome": "TP" if win else "SL",
                    "bars": j - fill, "exit_idx": j, "mfe": mfe, "mae": mae}
    j = end - 1
    cost_r = trade_cost_r(cm, risk, j - fill, not limit)
    r = ((C[j] - entry) / risk if side == "LONG" else (entry - C[j]) / risk) - cost_r
    return {"filled": True, "r": r, "outcome": "TIMEOUT", "bars": j - fill, "exit_idx": j, "mfe": mfe, "mae": mae}


def run_strategy(arrs, snaps, params: Params, spread: float, bt) -> dict:
    H, L, C = arrs
    trades, busy, unfilled, signals = [], -1, 0, 0
    for s in snaps:
        t = s["t"]
        if t <= busy:
            continue
        plan = smc_plan(s["smc"], s["price"], params)
        if plan["side"] == "NONE":
            continue
        signals += 1
        res = sim_trade(H, L, C, t, plan, int(bt.entry_expiry_bars), int(bt.max_hold_bars), spread)
        busy = res["exit_idx"]
        if not res["filled"]:
            unfilled += 1
            continue
        trades.append({"t": t, "side": plan["side"], "kind": plan["zone_kind"], "rr": plan["rr"], "conf": plan["confidence"],
                       "pd": plan.get("pd_zone"), "r": round(res["r"], 4), "outcome": res["outcome"], "bars": res["bars"]})
    return {"trades": trades, "signals": signals, "unfilled": unfilled}


# ----------------------------------------------------------------------------- metrics
def wilson(wins, n, z=1.96):
    if n == 0:
        return [0.0, 0.0]
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - m) / d * 100, 1), round((c + m) / d * 100, 1)]


def metrics(rs) -> dict:
    arr = np.asarray(rs, dtype=float)
    n = len(arr)
    if n == 0:
        return {"n": 0}
    wins, losses = arr[arr > 0], arr[arr <= 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    cum = np.cumsum(arr)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    streak = best = 0
    for x in arr:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    avg_w = float(wins.mean()) if len(wins) else 0.0
    avg_l = float(losses.mean()) if len(losses) else 0.0
    return {"n": n, "win_rate_pct": round(len(wins) / n * 100, 1), "win_rate_ci95": wilson(len(wins), n),
            "avg_win_r": round(avg_w, 2), "avg_loss_r": round(avg_l, 2),
            "realized_rr": round(avg_w / abs(avg_l), 2) if avg_l else None,
            "expectancy_r": round(float(arr.mean()), 3), "expectancy_se_r": round(float(arr.std(ddof=1) / math.sqrt(n)), 3) if n > 1 else None,
            "profit_factor": round(gp / gl, 2) if gl > 0 else 99.0,
            "total_r": round(float(arr.sum()), 2), "max_dd_r": round(float((peak - cum).max()), 2),
            "max_losing_streak": int(best)}


def equity_curve(rs, points=120):
    cum = np.concatenate([[0.0], np.cumsum(np.asarray(rs, dtype=float))])
    idx = np.unique(np.linspace(0, len(cum) - 1, min(points, len(cum))).astype(int))
    return [[int(i), round(float(cum[i]), 2)] for i in idx]


def monte_carlo(rs, runs: int, horizon: int, risk_pct: float, ruin_dd_pct: float, seed: int = 7) -> dict:
    arr = np.asarray(rs, dtype=float)
    if len(arr) < 2:
        return {"runs": 0}
    rng = np.random.default_rng(seed)
    pick = arr[rng.integers(0, len(arr), (runs, horizon))]
    eq = np.cumprod(1 + pick * risk_pct / 100.0, axis=1)
    eq = np.concatenate([np.ones((runs, 1)), eq], axis=1)
    dd = 1 - eq / np.maximum.accumulate(eq, axis=1)
    maxdd = dd.max(axis=1) * 100
    final = (eq[:, -1] - 1) * 100
    steps = np.unique(np.linspace(0, horizon, min(60, horizon + 1)).astype(int))
    fan = {f"p{q}": [round(float((np.percentile(eq[:, i], q) - 1) * 100), 2) for i in steps] for q in (5, 25, 50, 75, 95)}
    hist, edges = np.histogram(maxdd, bins=12)
    return {"runs": runs, "horizon": horizon, "risk_pct": risk_pct,
            "final_return_pct": {f"p{q}": round(float(np.percentile(final, q)), 2) for q in (5, 25, 50, 75, 95)},
            "prob_loss_pct": round(float((final < 0).mean() * 100), 1),
            "max_dd_pct": {"p50": round(float(np.percentile(maxdd, 50)), 1), "p95": round(float(np.percentile(maxdd, 95)), 1)},
            "prob_ruin_pct": round(float((maxdd >= ruin_dd_pct).mean() * 100), 2), "ruin_level_pct": ruin_dd_pct,
            "fan_steps": [int(s) for s in steps], "fan": fan,
            "dd_hist": {"edges": [round(float(e), 1) for e in edges], "counts": [int(c) for c in hist]}}


# ----------------------------------------------------------------------------- gate maths
def select_sample(trades, side, rr, bt):
    sim = [t for t in trades if t["side"] == side and abs(t["rr"] - rr) <= float(bt.rr_similarity)]
    if len(sim) >= int(bt.min_similar):
        return sim, f"similar setups (same side, R:R {rr:.1f} +/- {bt.rr_similarity})"
    same = [t for t in trades if t["side"] == side]
    if len(same) >= int(bt.min_trades):
        return same, f"all {side} trades of the strategy"
    return trades, "all strategy trades (too few same-direction trades)"


def gate_checks(sample, plan_rr, bt, risk_pct, seed=7, min_n=None):
    """Returns (checks, metrics, mc). Every check is a hard threshold from config."""
    m = metrics([t["r"] for t in sample])
    chk = []

    def add(name, ok, detail):
        chk.append({"name": name, "passed": bool(ok), "detail": detail})

    n = m.get("n", 0)
    need = int(min_n if min_n is not None else bt.min_trades)
    add("sample_size", n >= need or not bt.reject_if_insufficient, f"{n} trades (min {need})")
    if n < 2:
        return chk, m, {"runs": 0}
    breakeven = 100.0 / (1.0 + plan_rr)
    add("win_rate_floor", m["win_rate_pct"] >= float(bt.min_win_rate_pct), f"win rate {m['win_rate_pct']}% (floor {bt.min_win_rate_pct}%), 95% CI {m['win_rate_ci95']}")
    add("win_rate_vs_breakeven", m["win_rate_pct"] >= breakeven + float(bt.breakeven_margin_pct),
        f"win rate {m['win_rate_pct']}% vs break-even {breakeven:.1f}% for R:R {plan_rr:.1f} (+{bt.breakeven_margin_pct}pp margin)")
    add("expectancy", m["expectancy_r"] >= float(bt.min_expectancy_r), f"{m['expectancy_r']}R per trade after costs (min {bt.min_expectancy_r}R)")
    add("profit_factor", m["profit_factor"] >= float(bt.min_profit_factor), f"PF {m['profit_factor']} (min {bt.min_profit_factor})")
    mc = monte_carlo([t["r"] for t in sample], int(bt.mc_runs), int(bt.mc_horizon_trades), risk_pct, float(bt.mc_ruin_drawdown_pct), seed)
    add("mc_prob_loss", mc["prob_loss_pct"] <= float(bt.max_mc_prob_loss_pct), f"P(loss after {mc['horizon']} trades) = {mc['prob_loss_pct']}% (max {bt.max_mc_prob_loss_pct}%)")
    add("mc_drawdown_p95", mc["max_dd_pct"]["p95"] <= float(bt.max_mc_dd_p95_pct), f"95th pct max drawdown {mc['max_dd_pct']['p95']}% (max {bt.max_mc_dd_p95_pct}%)")
    add("mc_risk_of_ruin", mc["prob_ruin_pct"] <= float(bt.max_mc_ruin_pct), f"P(drawdown >= {mc['ruin_level_pct']}%) = {mc['prob_ruin_pct']}% (max {bt.max_mc_ruin_pct}%)")
    return chk, m, mc


# ----------------------------------------------------------------------------- optimisation (walk-forward, leak-free)
def split_snaps(snaps, bt):
    """TRAIN | VALIDATION | FINAL TEST, with an embargo so that no trade started in one segment can resolve using bars of the next.
    A trade started at bar t resolves by bar t + entry_expiry + max_hold, so snapshots closer than that to a boundary are dropped."""
    n = len(snaps)
    i1, i2 = int(n * float(bt.train_frac)), int(n * (float(bt.train_frac) + float(bt.val_frac)))
    emb = int(bt.entry_expiry_bars) + int(bt.max_hold_bars)
    b1 = snaps[i1]["t"] if i1 < n else 10 ** 12
    b2 = snaps[i2]["t"] if i2 < n else 10 ** 12
    train = [s for s in snaps[:i1] if s["t"] + emb <= b1]
    val = [s for s in snaps[i1:i2] if s["t"] + emb <= b2]
    test = snaps[i2:]
    return train, val, test, {"train_end_bar": b1, "val_end_bar": b2, "embargo_bars": emb,
                              "n_train": len(train), "n_val": len(val), "n_test": len(test), "dropped_for_embargo": n - len(train) - len(val) - len(test)}


def optimize(arrs, snaps, base: Params, cost, bt, risk_pct) -> dict:
    """Parameters are CHOSEN on TRAIN, must also earn money on VALIDATION, and are only REPORTED on FINAL TEST.
    The final-test slice never influences which parameters are selected (unit test: change it, the selection does not move)."""
    train, val, test, info = split_snaps(snaps, bt)
    rows = []
    for combo in itertools.product(*GRID.values()):
        pr = replace(base, **dict(zip(GRID.keys(), combo)))
        tin = run_strategy(arrs, train, pr, cost, bt)["trades"]
        tval = run_strategy(arrs, val, pr, cost, bt)["trades"]
        mi, mv = metrics([t["r"] for t in tin]), metrics([t["r"] for t in tval])
        n_is = mi.get("n", 0)
        score = mi["expectancy_r"] * math.sqrt(n_is) if n_is >= 15 else -9.0
        keys = ("n", "win_rate_pct", "expectancy_r", "profit_factor", "max_dd_r")
        rows.append({"params": {k: v for k, v in zip(GRID.keys(), combo)}, "score": round(score, 3),
                     "is": {k: mi.get(k) for k in keys}, "val": {k: mv.get(k) for k in keys}})
    rows.sort(key=lambda r: -r["score"])
    best, robust, fallback = None, False, None
    for r in rows:                                        # best-by-train first; keep going until one ALSO passes validation
        if r["score"] <= 0 or (r["val"].get("n") or 0) < int(bt.oos_min_trades):
            continue
        fallback = fallback or r                          # best candidate that merely had enough validation trades (for display only)
        if (r["val"]["expectancy_r"] or -1) >= float(bt.oos_min_expectancy_r) and (r["val"]["profit_factor"] or 0) >= 1.0:
            best, robust = r, True
            break
    sel, final_trades, final = None, [], {}
    shown = best or fallback
    if best:
        sel = replace(base, **best["params"])
        final_trades = run_strategy(arrs, test, sel, cost, bt)["trades"]      # first and only look at the final-test slice
        final = metrics([t["r"] for t in final_trades])
    return {"rows": rows[:12], "selected": sel.to_dict() if sel else None, "robust": bool(robust), "selected_row": shown,
            "final_trades": final_trades, "final": final, "splits": info, "grid_size": len(rows)}


# ----------------------------------------------------------------------------- orchestration + cache
def _cache_path(root, symbol, demo):
    return Path(root) / "data" / "cache" / f"bt_{symbol}{'_demo' if demo else ''}.json"


def _signature(cfg) -> str:
    sig = {"strategy": dict(cfg.strategy), "bt": {k: cfg.backtest[k] for k in ("timeframe", "history_bars", "step_bars", "entry_expiry_bars", "max_hold_bars", "cost_spread_mult", "train_frac", "val_frac")},
           "costs": dict(cfg.get("costs", {}) or {}),
           "inst": {k: [i.get("feed"), i.get("mt5_symbol"), bool(i.get("feed_is_proxy")), i.get("typical_spread"), i.get("usd_per_point_per_lot")] for k, i in cfg.instruments.items()}}
    return json.dumps(sig, sort_keys=True, default=str)


def _px_path(root, symbol, demo):
    return Path(root) / "data" / "cache" / f"px_{symbol}{'_demo' if demo else ''}.npz"


def save_price_cache(root, symbol, demo, df):
    """1h OHLC arrays kept next to the backtest cache so the Trade Validator (agent 12) can simulate paths without re-downloading."""
    p = _px_path(root, symbol, demo)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(p, o=df["open"].to_numpy(), h=df["high"].to_numpy(), l=df["low"].to_numpy(), c=df["close"].to_numpy())


def load_price_cache(root, symbol, demo):
    p = _px_path(root, symbol, demo)
    if not p.exists():
        return None
    z = np.load(p)
    return {k: z[k] for k in ("o", "h", "l", "c")}


def load_cached(cfg, symbol, demo):
    p = _cache_path(cfg.root, symbol, demo)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    age_h = (time.time() - d["created_epoch"]) / 3600
    if d.get("signature") != _signature(cfg) or age_h > float(cfg.backtest.cache_hours):
        return None
    return d


def compute_backtest(cfg, symbol, demo=False, progress=print) -> dict:
    inst, bt = cfg.instruments[symbol], cfg.backtest
    t0 = time.time()
    progress(f"  [11] backtest {symbol}: fetching {bt.history_bars} x 1h bars")
    df = fetch_history(symbol, inst, int(bt.history_bars), demo)
    if len(df) < FIRST + 100:
        raise RuntimeError(f"not enough history for {symbol}: {len(df)} bars")
    base = Params.from_cfg(cfg.strategy)
    spread = cost_model(cfg, inst)
    progress(f"  [11] backtest {symbol}: building SMC snapshots (no look-ahead)")
    snaps = build_snapshots(df, int(bt.step_bars), progress)
    arrs = (df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy())
    progress(f"  [11] backtest {symbol}: replaying strategy on {len(snaps)} evaluation points")
    res = run_strategy(arrs, snaps, base, spread, bt)
    trades = res["trades"]
    risk_pct = float(cfg.account.risk_per_trade_pct)
    out = {
        "symbol": symbol, "demo": demo, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "created_epoch": time.time(), "signature": _signature(cfg), "compute_seconds": None,
        "bars": len(df), "bars_from": str(df.index[0]), "bars_to": str(df.index[-1]), "evaluations": len(snaps),
        "signals": res["signals"], "unfilled": res["unfilled"], "spread_cost": spread, "cost_model": spread, "feed": feed_info(inst, df), "base_params": base.to_dict(),
        "metrics": metrics([t["r"] for t in trades]), "equity_curve": equity_curve([t["r"] for t in trades]),
        "mc": monte_carlo([t["r"] for t in trades], int(bt.mc_runs), int(bt.mc_horizon_trades), risk_pct, float(bt.mc_ruin_drawdown_pct)),
        "trades": trades[-1500:],
        "assumptions": ["limit/stop entry at plan price, fills on touch; costs = spread + slippage (stop entries and stop-loss exits) + commission + swap, all charged in R; gaps through a stop are NOT modelled",
                        "same-candle SL+TP => SL first", "one position at a time", f"max hold {bt.max_hold_bars} bars, entry expiry {bt.entry_expiry_bars} bars",
                        "strategy = rule-based SMC planner (4h bias, 1h zones) - NOT the discretionary LLM plan"],
    }
    progress(f"  [11] backtest {symbol}: walk-forward optimisation ({len(list(itertools.product(*GRID.values())))} parameter sets)")
    out["optimization"] = optimize(arrs, snaps, base, spread, bt, risk_pct)
    save_price_cache(cfg.root, symbol, demo, df)
    out["compute_seconds"] = round(time.time() - t0, 1)
    p = _cache_path(cfg.root, symbol, demo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, default=str), encoding="utf-8")
    return out
