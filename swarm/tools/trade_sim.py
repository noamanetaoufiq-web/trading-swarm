"""Per-trade Monte Carlo (used by agent 12, the Trade Validator).

The confirmed trade (side, entry, SL, TP) is simulated on N synthetic 1h price paths and the outcome is counted:
TP first / SL first / time-out / never filled.  Paths are a BLOCK BOOTSTRAP of the real 1h bars of the instrument
(24-bar blocks keep volatility clustering, fat tails and the intraday high/low structure), rescaled to today's ATR.

Two lenses
  neutral       historical drift removed -> a random-walk market with realistic volatility.  This is the "could it win by luck?" baseline:
                with zero costs P(TP first) == 1 / (1 + R:R) == the break-even win rate, so any real edge must come from somewhere else.
  sample_drift  the drift of the historical sample is kept (information only; flatters the side the market happened to trend to).

The ladder (10 / 100 / 1000 / 5000 by default) is evaluated on nested prefixes of the same paths, so the numbers show how the
estimate converges.  Everything is vectorised numpy; no look-ahead is possible because paths are drawn from the past only.
"""
from __future__ import annotations
import math
import numpy as np

from .backtest import wilson


def hist_stats(px: dict) -> dict:
    o, h, l, c = (np.asarray(px[k], dtype=float) for k in ("o", "h", "l", "c"))
    prev = c[:-1]
    ok = (prev > 0) & (c[1:] > 0) & (h[1:] > 0) & (l[1:] > 0)
    lc, lh, ll = (np.log(x[1:][ok] / prev[ok]) for x in (c, h, l))
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - prev), np.abs(l[1:] - prev)))[ok] / prev[ok]
    return {"lc": lc, "lh": lh, "ll": ll, "mean_tr_pct": float(tr.mean()), "mu": float(lc.mean()), "n": int(ok.sum())}


def breakeven_win_pct(rr: float, risk: float, cost_r_total: float = 0.0, slip_r: float = 0.0) -> float:
    """Win rate needed for zero expectancy: w*(rr-c) - (1-w)*(1+slip+c) = 0."""
    return 100.0 * (1 + slip_r + cost_r_total) / (rr + 1 + slip_r)


def simulate_paths(hs: dict, side: str, entry: float, sl: float, tp: float, price: float, cm: dict, runs: int, expiry: int,
                   max_hold: int, block: int = 24, neutral: bool = True, vol_scale: float = 1.0, seed: int = 12) -> dict:
    rng = np.random.default_rng(seed)
    M, L = hs["n"], int(expiry + max_hold)
    block = max(2, min(int(block), M - 1))
    nb = math.ceil(L / block)
    starts = rng.integers(0, M - block, (runs, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(runs, nb * block)[:, :L]
    mu = hs["mu"] if neutral else 0.0
    rc, rh, rl = ((hs[k][idx] - mu) * vol_scale for k in ("lc", "lh", "ll"))
    close = price * np.exp(np.cumsum(rc, axis=1))
    prev = np.concatenate([np.full((runs, 1), price), close[:, :-1]], axis=1)
    high = np.maximum(prev * np.exp(rh), np.maximum(close, prev))
    low = np.minimum(prev * np.exp(rl), np.minimum(close, prev))
    long_ = side == "LONG"
    limit = (entry <= price) if long_ else (entry >= price)
    hit = (low <= entry) if (long_ and limit) or (not long_ and not limit) else (high >= entry)
    hit_w = hit[:, :expiry]
    filled = hit_w.any(axis=1)
    f = hit_w.argmax(axis=1)
    j = np.arange(L)[None, :]
    active = filled[:, None] & (j >= f[:, None]) & (j < f[:, None] + max_hold)
    slh, tph = ((low <= sl), (high >= tp)) if long_ else ((high >= sl), (low <= tp))
    res = (slh | tph) & active
    has = res.any(axis=1)
    first = res.argmax(axis=1)
    ar = np.arange(runs)
    win = has & tph[ar, first] & ~slh[ar, first]
    loss = has & ~win
    timeout = filled & ~has
    end = np.minimum(f + max_hold, L) - 1
    held = np.where(has, first - f, end - f).astype(float)
    risk = abs(entry - sl)
    rr = abs(tp - entry) / risk
    cost = (cm["spread"] / risk + (0.0 if limit else cm["slip"] / risk) + cm["comm_per_lot"] / (risk * cm["usd_per_pt"])
            + cm["swap_day"] * (held / 24.0) / (risk * cm["usd_per_pt"]))
    mtm = ((close[ar, end] - entry) if long_ else (entry - close[ar, end])) / risk
    r = np.where(win, rr - cost, np.where(loss, -1.0 - cm["slip"] / risk - cost, mtm - cost))
    r = np.where(filled, r, np.nan)
    return {"filled": filled, "win": win, "loss": loss, "timeout": timeout, "r": r, "fill_bar": f, "held": held, "cost": np.where(filled, cost, np.nan),
            "rr": rr, "risk": risk}


def summarize(sim: dict, n: int) -> dict:
    fl, w, ls, to, r = (sim[k][:n] for k in ("filled", "win", "loss", "timeout", "r"))
    nf = int(fl.sum())
    out = {"runs": int(n), "filled": nf, "fill_prob_pct": round(nf / n * 100, 1), "tp": int(w.sum()), "sl": int(ls.sum()), "timeout": int(to.sum()),
           "unfilled": int(n - nf)}
    if nf == 0:
        return {**out, "win_rate_pct": None, "win_rate_ci95": [0.0, 0.0], "expectancy_r": None, "expectancy_per_signal_r": 0.0}
    rf = r[fl]
    wins = int((rf > 0).sum())            # SAME definition as backtest.metrics(): a trade is a win when R > 0 after costs (a profitable time-out counts)
    tp = int(w.sum())
    se = float(rf.std(ddof=1) / math.sqrt(nf)) if nf > 1 else None
    out.update({"win_rate_pct": round(wins / nf * 100, 1), "win_rate_ci95": wilson(wins, nf), "tp_rate_pct": round(tp / nf * 100, 1),
                "avg_win_r": round(float(rf[rf > 0].mean()), 2) if wins else 0.0, "avg_loss_r": round(float(rf[rf <= 0].mean()), 2) if wins < nf else 0.0,
                "expectancy_r": round(float(rf.mean()), 3),
                "expectancy_se_r": round(se, 3) if se is not None else None, "expectancy_per_signal_r": round(float(rf.sum() / n), 3),
                "profit_rate_pct": round(float((rf > 0).mean() * 100), 1), "median_bars_in_trade": round(float(np.median(sim["held"][:n][fl])), 1),
                "mean_cost_r": round(float(sim["cost"][:n][fl].mean()), 3),
                "r_pct": {f"p{q}": round(float(np.percentile(rf, q)), 2) for q in (5, 25, 50, 75, 95)}})
    return out


def r_histogram(sim: dict, n: int, bins: int = 14) -> dict:
    rf = sim["r"][:n][sim["filled"][:n]]
    if len(rf) == 0:
        return {"edges": [], "counts": []}
    lo, hi = float(min(rf.min(), -1.5)), float(max(rf.max(), sim["rr"] + 0.5))
    cnt, edges = np.histogram(rf, bins=bins, range=(lo, hi))
    return {"edges": [round(float(e), 2) for e in edges], "counts": [int(c) for c in cnt]}


def run_ladder(px: dict, side, entry, sl, tp, price, cm, atr_now, ladder, expiry, max_hold, block=24, vol_match=True, seed=12) -> dict:
    """Simulate once at max(ladder) paths per lens and report every ladder step on nested prefixes."""
    hs = hist_stats(px)
    vs = 1.0
    if vol_match and atr_now and price:
        vs = float(np.clip((atr_now / price) / hs["mean_tr_pct"], 0.4, 2.5))
    top = int(max(ladder))
    lenses = {}
    for name, neutral in (("neutral", True), ("sample_drift", False)):
        sim = simulate_paths(hs, side, entry, sl, tp, price, cm, top, int(expiry), int(max_hold), block, neutral, vs, seed)
        lenses[name] = {"ladder": [summarize(sim, int(n)) for n in sorted(ladder)], "r_hist": r_histogram(sim, top),
                        "outcome_mix": {"TP": int(sim["win"].sum()), "SL": int(sim["loss"].sum()), "TIMEOUT": int(sim["timeout"].sum()),
                                        "UNFILLED": int(top - sim["filled"].sum())}}
    risk = abs(entry - sl)
    rr = abs(tp - entry) / risk
    limit = (entry <= price) if side == "LONG" else (entry >= price)
    c0 = cm["spread"] / risk + (0.0 if limit else cm["slip"] / risk) + cm["comm_per_lot"] / (risk * cm["usd_per_pt"])
    return {"lenses": lenses, "vol_scale": round(vs, 3), "hist_bars": hs["n"], "block_bars": block, "rr": round(rr, 3), "risk": round(risk, 4),
            "breakeven_pct": round(100.0 / (1 + rr), 1),
            "breakeven_cost_adj_pct": round(breakeven_win_pct(rr, risk, c0, cm["slip"] / risk), 1), "win_definition": "win = R > 0 after costs", "ladder": sorted(int(x) for x in ladder)}
