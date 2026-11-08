"""Agent 12 (per-trade Monte Carlo validator), leak-free optimisation, account sizing from the broker, equity-based loss limits, costs, csv feed."""
import json
import numpy as np
import pytest

from swarm.agents import build_agents
from swarm.agents.trade_validator import TradeValidatorAgent
from swarm.config import load_config
from swarm.journal import Journal
from swarm.llm import LLM
from swarm.pipeline import run_analysis
from swarm.tools import accounts as acc
from swarm.tools.backtest import (Params, build_snapshots, cost_model, load_history_csv, optimize, run_strategy, sim_trade, split_snaps, trade_cost_r)
from swarm.tools.market_data import synthetic_ohlcv
from swarm.tools.risk import position_size
from swarm.tools.trade_sim import breakeven_win_pct, run_ladder
from tests.test_swarm import cfg, bt_cache_root, _test_cfg  # noqa: F401  (fixtures)

CM = {"spread": 0.3, "slip": 0.15, "comm_per_lot": 0.0, "swap_day": 0.0, "usd_per_pt": 100.0}


def _walk(n=4000, seed=1, drift=0.0, vol=0.002):
    rng = np.random.default_rng(seed)
    c = 2000 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    return {"o": c, "h": c * (1 + abs(rng.normal(0, 0.001, n))), "l": c * (1 - abs(rng.normal(0, 0.001, n))), "c": c}


# ------------------------------------------------------------------ the Monte Carlo ladder
def test_ladder_has_every_step_and_converges_to_breakeven_on_a_random_walk():
    px = _walk()
    r = run_ladder(px, "LONG", 1995, 1985, 2015, 2000, {**CM, "spread": 0, "slip": 0}, 4.0, [10, 100, 1000, 5000], 24, 240)
    nl = r["lenses"]["neutral"]["ladder"]
    assert [s["runs"] for s in nl] == [10, 100, 1000, 5000]
    assert abs(nl[-1]["tp_rate_pct"] - 100 / 3) < 3.0                    # zero costs + zero drift: P(TP first) = 1/(1+R:R)
    assert nl[-1]["win_rate_pct"] >= nl[-1]["tp_rate_pct"]               # win = R > 0, so profitable time-outs count too
    assert abs(nl[-1]["win_rate_pct"] - nl[-2]["win_rate_pct"]) < 4.0     # 1000 vs 5000 agree
    assert nl[0]["win_rate_ci95"][1] - nl[0]["win_rate_ci95"][0] > nl[-1]["win_rate_ci95"][1] - nl[-1]["win_rate_ci95"][0]   # CI shrinks with more runs
    mix = r["lenses"]["neutral"]["outcome_mix"]
    assert sum(mix.values()) == 5000 and r["rr"] == 2.0


def test_costs_lower_the_simulated_edge_and_breakeven_rises():
    px = _walk()
    free = run_ladder(px, "LONG", 1995, 1985, 2015, 2000, {**CM, "spread": 0, "slip": 0}, 4.0, [2000], 24, 240)
    paid = run_ladder(px, "LONG", 1995, 1985, 2015, 2000, {**CM, "spread": 1.0, "slip": 0.5}, 4.0, [2000], 24, 240)
    assert paid["lenses"]["neutral"]["ladder"][0]["expectancy_r"] < free["lenses"]["neutral"]["ladder"][0]["expectancy_r"]
    assert paid["breakeven_cost_adj_pct"] > free["breakeven_cost_adj_pct"]
    assert breakeven_win_pct(2.0, 10, 0.0, 0.0) == pytest.approx(100 / 3)


def test_neutral_lens_removes_drift_sample_lens_keeps_it():
    up = _walk(drift=0.0006)                 # strongly trending sample
    r = run_ladder(up, "LONG", 1999, 1989, 2019, 2000, {**CM, "spread": 0, "slip": 0}, 4.0, [3000], 24, 240)
    assert r["lenses"]["sample_drift"]["ladder"][0]["win_rate_pct"] > r["lenses"]["neutral"]["ladder"][0]["win_rate_pct"] + 5


def test_ladder_is_deterministic():
    px = _walk()
    a = run_ladder(px, "SHORT", 2005, 2015, 1985, 2000, CM, 4.0, [10, 1000], 24, 240, seed=3)
    b = run_ladder(px, "SHORT", 2005, 2015, 1985, 2000, CM, 4.0, [10, 1000], 24, 240, seed=3)
    assert a == b


# ------------------------------------------------------------------ verdict logic
def _evaluate(cfg, ev, plan=None):
    ag = TradeValidatorAgent(cfg, LLM(cfg, mock=True))
    plan = plan or {"side": "LONG", "entry": 1995, "sl": 1985, "tp": 2015}
    return ag.evaluate(cfg, cfg.instruments["XAUUSD"], plan, 2000.0, 4.0, _walk(), ev, cm=CM)


def test_verdict_good_marginal_bad(cfg):
    strong = {"n": 400, "win_rate_pct": 55.0, "win_rate_ci95": [50.0, 60.0], "expectancy_r": 0.6}
    wide = {"n": 12, "win_rate_pct": 55.0, "win_rate_ci95": [28.0, 79.0], "expectancy_r": 0.6}
    weak = {"n": 400, "win_rate_pct": 20.0, "win_rate_ci95": [16.0, 24.0], "expectancy_r": -0.4}
    assert _evaluate(cfg, strong).verdict == "GOOD"
    assert _evaluate(cfg, wide).verdict == "MARGINAL"             # point estimate fine, confidence interval not
    bad = _evaluate(cfg, weak)
    assert bad.verdict == "BAD" and any("beats_" in r for r in bad.reasons)
    assert _evaluate(cfg, None).verdict == "MARGINAL"             # no evidence can never be GOOD


def test_cost_stress_can_kill_an_edge(cfg):
    cfg.validator.cost_stress_mult = 400.0
    v = _evaluate(cfg, {"n": 400, "win_rate_pct": 55.0, "win_rate_ci95": [50.0, 60.0], "expectancy_r": 0.6})
    assert v.verdict == "BAD" and any(c["name"] == "edge_after_cost_stress" and not c["passed"] for c in v.checks)


# ------------------------------------------------------------------ pipeline integration
def _permissive_gate(cfg):
    """Any trade the Inspector approves must also pass the gate + validator, so the integration path is exercised whenever the synthetic data yields one."""
    b = cfg.backtest
    b.min_trades, b.min_win_rate_pct, b.breakeven_margin_pct, b.min_expectancy_r, b.min_profit_factor = 1, 0, -100, -99, 0
    b.max_mc_prob_loss_pct, b.max_mc_dd_p95_pct, b.max_mc_ruin_pct, b.llm_review = 100, 1000, 100, False
    cfg.validator.min_stress_expectancy_r, cfg.validator.edge_margin_pp, cfg.validator.llm_review = -99, -100, False


def test_pipeline_runs_validator_and_stores_every_ladder_step(cfg):
    _permissive_gate(cfg)
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None)
    seen = 0
    for c in run["ctxs"].values():
        if c.verdict and c.verdict.approved:
            tv = c.reports["trade_validator"]
            assert tv.approved and tv.verdict in ("GOOD", "MARGINAL")
            assert [s["runs"] for s in tv.sim["lenses"]["neutral"]["ladder"]] == [10, 100, 1000, 5000]
            seen += 1
    j = Journal(cfg.root, True).all()
    assert all(("validation" in r) for r in j)
    assert seen == len([r for r in j if r["status"] == "PENDING"])
    if seen == 0:
        pytest.skip("no approved trade in this synthetic run")


def test_validator_blocks_execution_when_it_refuses(cfg):
    cfg.validator.min_stress_expectancy_r = 99.0           # impossible after-cost edge
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None)
    for c in run["ctxs"].values():
        assert c.execution is None and not (c.verdict and c.verdict.approved and "trade_validator" in c.reports and not c.reports["trade_validator"].approved)
    refused = [c for c in run["ctxs"].values() if "trade_validator" in c.reports]
    assert all(not c.reports["trade_validator"].approved for c in refused)


def _trade_ctx(cfg):
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None)
    cand = [c for c in run["ctxs"].values() if c.reports["orchestrator"].decision == "TRADE"]
    if not cand:
        pytest.skip("no TRADE proposal in this synthetic run")
    return cand[0]


def test_marginal_trade_gets_reduced_risk_or_is_refused(cfg):
    c = _trade_ctx(cfg)
    ag = build_agents(cfg, LLM(cfg, mock=True))["trade_validator"]
    wide = type("B", (), {"metrics": {"n": 12, "win_rate_pct": 55.0, "win_rate_ci95": [1.0, 95.0], "expectancy_r": 0.6}, "sample_basis": "t"})()
    cfg.validator.llm_review = False
    cfg.validator.min_verdict = "MARGINAL"
    v = ag.validate(c, wide)
    assert v.verdict == "MARGINAL"
    p = c.reports["orchestrator"]
    half = position_size(c.equity, cfg.account.risk_per_trade_pct * 0.5, p.entry, p.stop_loss, c.inst, cost_model(cfg, c.inst))
    assert v.approved == (half["lots"] > 0) and v.risk_mult == 0.5
    if v.approved:
        assert v.sizing["lots"] == half["lots"]
    cfg.validator.min_verdict = "GOOD"
    v2 = ag.validate(c, wide)
    assert not v2.approved and any("min_verdict" in r for r in v2.reasons)


# ------------------------------------------------------------------ leak-free optimisation
def _snaps(cfg, n=2600):
    df = synthetic_ohlcv("XAUUSD", "commodity", "1h", n=n, salt=11)
    return df, build_snapshots(df, 8)


def test_splits_are_ordered_and_embargoed(cfg):
    df, snaps = _snaps(cfg)
    bt = cfg.backtest
    train, val, test, info = split_snaps(snaps, bt)
    emb = int(bt.entry_expiry_bars) + int(bt.max_hold_bars)
    assert max(s["t"] for s in train) + emb <= min(s["t"] for s in val)
    assert max(s["t"] for s in val) + emb <= min(s["t"] for s in test)
    assert info["n_train"] and info["n_val"] and info["n_test"] and info["dropped_for_embargo"] > 0


def test_selected_parameters_do_not_depend_on_the_final_test_segment(cfg):
    """Change ONLY the prices of the final-test segment: the parameters chosen on train/validation must not move."""
    df, snaps = _snaps(cfg)
    bt = cfg.backtest
    inst = cfg.instruments["XAUUSD"]
    cm = cost_model(cfg, inst)
    base = Params(**{k: v for k, v in cfg.backtest.get("base_params", {}).items()}) if cfg.backtest.get("base_params") else Params()
    H, L, C = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    a = optimize((H, L, C), snaps, base, cm, bt, 1.0)
    b2 = a["splits"]["val_end_bar"]
    rng = np.random.default_rng(5)
    f = np.exp(np.cumsum(rng.normal(0, 0.01, len(C) - b2)))
    H2, L2, C2 = H.copy(), L.copy(), C.copy()
    H2[b2:], L2[b2:], C2[b2:] = H[b2:] * f, L[b2:] * f, C[b2:] * f
    b = optimize((H2, L2, C2), snaps, base, cm, bt, 1.0)
    assert a["selected"] == b["selected"] and a["rows"] == b["rows"]          # selection identical
    if a["selected"]:
        assert a["final_trades"] != b["final_trades"] or not a["final_trades"]   # ...while the reported final-test result does react


def test_final_trades_only_start_in_the_final_segment(cfg):
    df, snaps = _snaps(cfg)
    cm = cost_model(cfg, cfg.instruments["XAUUSD"])
    o = optimize((df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()), snaps, Params(), cm, cfg.backtest, 1.0)
    for t in o["final_trades"]:
        assert t["t"] >= o["splits"]["val_end_bar"]


# ------------------------------------------------------------------ trading costs
def test_costs_are_charged_in_r():
    cm = {"spread": 0.4, "slip": 0.2, "comm_per_lot": 7.0, "swap_day": 6.0, "usd_per_pt": 100.0}
    base = trade_cost_r({**cm, "slip": 0, "comm_per_lot": 0, "swap_day": 0}, 10.0, 24, False)
    assert base == pytest.approx(0.04)
    assert trade_cost_r(cm, 10.0, 0, True) == pytest.approx(0.04 + 0.02 + 0.007)               # + stop-entry slippage + commission
    assert trade_cost_r(cm, 10.0, 48, False) == pytest.approx(0.04 + 0.007 + 0.012)            # + 2 days of swap
    n = 40
    H = np.full(n, 101.0); L = np.full(n, 99.0); C = np.full(n, 100.0)
    L[3] = 89.0                                                                                 # filled at 95? no: limit at 95 never touched -> use stop-out below
    plan = {"side": "LONG", "entry": 100.0, "sl": 90.0, "tp": 130.0}
    r0 = sim_trade(H, L, C, 1, plan, 5, 20, {**cm, "slip": 0, "comm_per_lot": 0, "swap_day": 0})
    r1 = sim_trade(H, L, C, 1, plan, 5, 20, cm)
    assert r0["outcome"] == r1["outcome"] == "SL" and r1["r"] < r0["r"]                          # stop-loss exit pays slippage


# ------------------------------------------------------------------ csv feed for the broker's own XAUUSD history
def test_csv_history_loader_mt5_export(tmp_path):
    rows = ["<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>"]
    t0 = np.datetime64("2026-01-05T00:00")
    for i in range(300):                                      # M15 bars -> must be resampled to 1h
        t = t0 + np.timedelta64(15 * i, "m")
        d, hh = str(t).split("T")
        rows.append(f"{d.replace('-', '.')}\t{hh}:00\t{2000 + i * .1:.2f}\t{2001 + i * .1:.2f}\t{1999 + i * .1:.2f}\t{2000.5 + i * .1:.2f}\t10")
    p = tmp_path / "x.csv"
    p.write_text("\n".join(rows))
    df = load_history_csv(str(p))
    assert len(df) == 75 and list(df.columns) == ["open", "high", "low", "close", "volume"] and df.index.is_monotonic_increasing


# ------------------------------------------------------------------ account size comes from the broker, never from code
def _fake_broker(equity, currency="USD", kind="demo", fail=False):
    def fn(broker, creds=None, sandbox=False, *a, **k):
        if fail:
            return {"ok": False, "message": "terminal not running", "equity": None}
        return {"ok": True, "message": "fake", "equity": equity, "balance": equity - 2.0, "free_margin": equity, "leverage": 500, "currency": currency,
                "account_kind": kind, "open_positions": 1, "floating_pnl": -2.0}
    return fn


def test_linked_broker_equity_is_read_not_configured(cfg, monkeypatch):
    monkeypatch.setattr(acc, "read_broker", _fake_broker(137.5))
    st = acc.resolve_account(cfg.root, {"broker": "mt5", "type": "demo", "label": "demo-mt5"}, cfg, Journal(cfg.root, False))
    assert st["ok"] and st["equity_usd"] == 137.5 and st["source"] == "broker" and st["floating_pnl"] == -2.0 and st["balance"] == 135.5
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD"], demo=True, progress=lambda *_: None, account={"broker": "mt5", "type": "demo", "label": "demo-mt5"})
    assert run["ctxs"]["XAUUSD"].equity == 137.5 and run["ctxs"]["XAUUSD"].account["broker"] == "mt5"      # every agent sizes on the broker's number


def test_unreadable_broker_stops_the_run_no_silent_fallback(cfg, monkeypatch):
    monkeypatch.setattr(acc, "read_broker", _fake_broker(0, fail=True))
    with pytest.raises(RuntimeError, match="no fallback"):
        run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD"], demo=True, progress=lambda *_: None, account={"broker": "mt5", "type": "real", "label": "r"})


def test_paper_equity_is_start_capital_plus_realised_pnl(cfg):
    j = Journal(cfg.root, False)
    j.append({"id": "a", "ts": "2026-11-01T00:00:00", "symbol": "XAUUSD", "status": "CLOSED", "verdict": {"risk_usd": 10.0}, "outcome": {"r_multiple": 2.0}})
    j.append({"id": "b", "ts": "2026-11-02T00:00:00", "symbol": "XAUUSD", "status": "CLOSED", "verdict": {"risk_usd": 10.0}, "outcome": {"r_multiple": -1.0}})
    st = acc.resolve_account(cfg.root, {"broker": "paper", "type": "demo", "equity": 100}, cfg, j, record=False)
    assert st["equity_usd"] == 110.0 and st["realised_paper_pnl"] == 10.0


def test_non_usd_account_is_refused_by_the_inspector(cfg, monkeypatch):
    monkeypatch.setattr(acc, "read_broker", _fake_broker(5000, currency="EUR"))
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None, account={"broker": "mt5", "type": "demo", "label": "eur"})
    traded = [c for c in run["ctxs"].values() if c.reports["orchestrator"].decision == "TRADE"]
    assert all(c.account["currency_ok"] is False for c in run["ctxs"].values())
    if not traded:
        pytest.skip("no TRADE proposal in this synthetic run")
    for c in traded:
        assert not c.verdict.approved and any(k["name"] == "account_currency" and not k["passed"] for k in c.verdict.checks)


# ------------------------------------------------------------------ equity-based daily / weekly / drawdown protection (includes FLOATING loss)
def test_equity_log_stats_and_inspector_limits(cfg):
    import time
    from datetime import datetime, timezone
    log = acc.EquityLog(cfg.root, "t")
    now = datetime.now(timezone.utc)
    log.record(100.0, now=now)
    s = log.stats(95.0, now=now)
    assert s["day_pnl_pct"] == -5.0 and s["drawdown_pct"] == 5.0 and s["peak_equity"] == 100.0
    # inspector: a floating loss with NO closed trade must still stop new entries
    c = _trade_ctx(cfg)
    c.account = {"equity": 95.0, "equity_usd": 95.0, "day_pnl_pct": -3.5, "week_pnl_pct": -3.5, "drawdown_pct": 3.5, "peak_equity": 100.0, "currency_ok": True}
    v = build_agents(cfg, LLM(cfg, mock=True))["inspector"].evaluate(c, Journal(cfg.root, True), 0.0)
    assert any(k["name"] == "daily_loss_limit" and not k["passed"] for k in v.checks) and not v.approved
    c.account.update(day_pnl_pct=-0.5, week_pnl_pct=-9.0, drawdown_pct=0.5)
    v = build_agents(cfg, LLM(cfg, mock=True))["inspector"].evaluate(c, Journal(cfg.root, True), 0.0)
    assert any(k["name"] == "weekly_loss_limit" and not k["passed"] for k in v.checks)
    c.account.update(week_pnl_pct=0.0, drawdown_pct=25.0)
    v = build_agents(cfg, LLM(cfg, mock=True))["inspector"].evaluate(c, Journal(cfg.root, True), 0.0)
    assert any(k["name"] == "equity_drawdown_limit" and not k["passed"] for k in v.checks)


def test_hundred_dollar_account_reality_check():
    """Documented limit: on gold, 0.01 lot = $1 per $1 move, so $100 @ 0.5% ($0.50) can never fund a normal stop -> Inspector refuses (correctly)."""
    c = load_config()
    gold = c.instruments["XAUUSD"]
    sz = position_size(100.0, 0.5, 2000.0, 1995.0, gold)
    assert sz["lots"] == 0.0 and "too small" in sz["note"]


# ------------------------------------------------------------------ review fixes: one definition of "win", optimizer search, sizing with costs
def test_win_definition_is_identical_in_backtester_and_validator():
    from swarm.tools.backtest import metrics
    from swarm.tools.trade_sim import summarize
    # 6 filled trades: 2 TP wins, 1 PROFITABLE time-out, 1 losing time-out, 2 SL losses
    r = np.array([2.0, 2.0, 0.4, -0.3, -1.0, -1.0, np.nan])
    sim = {"filled": ~np.isnan(r), "win": np.array([1, 1, 0, 0, 0, 0, 0], bool), "loss": np.array([0, 0, 0, 0, 1, 1, 0], bool),
           "timeout": np.array([0, 0, 1, 1, 0, 0, 0], bool), "r": r, "held": np.ones(7), "cost": np.full(7, 0.1)}
    v = summarize(sim, 7)
    m = metrics([x for x in r if not np.isnan(x)])
    assert v["win_rate_pct"] == m["win_rate_pct"] == 50.0                 # 3 of 6: the profitable time-out is a win in BOTH
    assert v["win_rate_ci95"] == m["win_rate_ci95"] and v["tp_rate_pct"] == 33.3
    assert m["expectancy_se_r"] is not None


def test_optimizer_keeps_searching_until_a_set_passes_validation(cfg, monkeypatch):
    import itertools
    import swarm.tools.backtest as B
    df, snaps = _snaps(cfg)
    bt = cfg.backtest
    train, val, test, _ = B.split_snaps(snaps, bt)
    combos = list(itertools.product(*B.GRID.values()))
    keys = list(B.GRID.keys())
    first = {"train": train[0]["t"], "val": val[0]["t"], "test": test[0]["t"]}

    def fake_run(arrs, seg, pr, cost, bt_):
        idx = combos.index(tuple(getattr(pr, k) for k in keys))
        part = next(k for k, t in first.items() if seg and seg[0]["t"] == t)
        # combo 0: best on TRAIN but LOSES on validation; combo 1: slightly worse on TRAIN, wins on validation; the rest lose
        mean = {("train", 0): 0.9, ("train", 1): 0.6, ("val", 0): -0.5, ("val", 1): 0.4, ("test", 1): 0.3}.get((part, idx), -0.2)
        rs = [mean + (0.5 if i % 2 else -0.5) for i in range(40)]
        return {"trades": [{"r": x, "t": seg[0]["t"] + i, "side": "LONG", "rr": 2.0} for i, x in enumerate(rs)], "signals": 40, "unfilled": 0}

    monkeypatch.setattr(B, "run_strategy", fake_run)
    o = B.optimize((df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()), snaps, B.Params(), cost_model(cfg, cfg.instruments["XAUUSD"]), bt, 1.0)
    assert o["robust"] and o["selected"] == {**B.Params().to_dict(), **dict(zip(keys, combos[1]))}      # NOT the first (train-best) set
    assert o["selected_row"]["val"]["expectancy_r"] > 0 and o["final"]["n"] == 40
    # when nothing passes validation: no selection, but the best candidate is still shown for the dashboard
    monkeypatch.setattr(B, "run_strategy", lambda a, seg, pr, c, b: {"trades": [{"r": (0.9 if first["train"] == seg[0]["t"] else -0.4) + (0.2 if i % 2 else -0.2), "t": seg[0]["t"] + i, "side": "LONG", "rr": 2.0} for i in range(40)], "signals": 40, "unfilled": 0})
    o2 = B.optimize((df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()), snaps, B.Params(), cost_model(cfg, cfg.instruments["XAUUSD"]), bt, 1.0)
    assert not o2["robust"] and o2["selected"] is None and o2["selected_row"] is not None and o2["final_trades"] == []


def test_position_size_includes_spread_slippage_and_commission(cfg):
    gold = cfg.instruments["XAUUSD"]
    cm = {"spread": 0.3, "slip": 0.15, "comm_per_lot": 7.0, "swap_day": 0.0, "usd_per_pt": 100.0}
    plain = position_size(10_000, 10.0, 2000.0, 1990.0, gold)                 # $1000 budget, $1000/lot -> 1.00 lot
    all_in = position_size(10_000, 10.0, 2000.0, 1990.0, gold, cm)            # per lot: (10 + 0.45) * 100 + 7 = 1052 -> 0.95 lot
    assert plain["lots"] == 1.0 and all_in["lots"] == 0.95
    assert all_in["risk_usd"] <= 1000.0 and all_in["cost_buffer_usd"] == pytest.approx(0.95 * 52.0, abs=0.01)
    # the actual loss at the stop (distance + spread + slippage, times lots, plus commission) never exceeds the budget
    loss = all_in["lots"] * ((10.0 + 0.3 + 0.15) * 100 + 7.0)
    assert loss <= 1000.0 + 1e-6
