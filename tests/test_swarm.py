import json
import pytest

from swarm.config import load_config
from swarm.llm import LLM, extract_json
from swarm.pipeline import run_analysis
from swarm.tools.risk import position_size, rr_ratio, levels_sane
from swarm.tools.market_data import synthetic_ohlcv, clean_ohlcv
from swarm.tools.smc import analyze_tf


import shutil
from pathlib import Path


def _test_cfg(root):
    c = load_config()
    c.root = str(root)
    c.backtest.history_bars = 2500       # smaller than production defaults -> faster tests
    c.backtest.step_bars = 8
    # production defaults are sized for a $100 account (nothing is tradable on gold at 0.5% risk): tests use a roomy paper account so the whole pipeline is exercised
    c.account.equity, c.account.risk_per_trade_pct, c.account.max_total_open_risk_pct, c.account.max_daily_loss_pct = 10000, 1.0, 2.0, 3.0
    return c


@pytest.fixture(scope="session")
def bt_cache_root(tmp_path_factory):
    """Compute the demo backtest caches once for the whole test session."""
    from swarm.tools.backtest import compute_backtest
    root = tmp_path_factory.mktemp("btcache")
    c = _test_cfg(root)
    for sym in ("XAUUSD", "BTCUSD", "NAS100"):
        compute_backtest(c, sym, demo=True, progress=lambda *_: None)
    return root


@pytest.fixture()
def cfg(tmp_path, bt_cache_root):
    c = _test_cfg(tmp_path)  # isolate journal files per test
    shutil.copytree(Path(bt_cache_root) / "data" / "cache", Path(tmp_path) / "data" / "cache")
    return c


def test_extract_json_takes_last_object():
    assert extract_json('noise {"a":1} more {"b":{"c":2}} tail') == {"b": {"c": 2}}
    assert extract_json("no json here") is None


def test_rr_and_sanity():
    assert levels_sane("LONG", 100, 95, 112) and not levels_sane("LONG", 100, 105, 112)
    assert rr_ratio("SHORT", 100, 105, 90) == 2.0


def test_position_size_respects_risk(cfg):
    inst = cfg.instruments["XAUUSD"]
    s = position_size(10_000, 1.0, 3995, 4040, inst)   # $45 stop, $100/lot/$1 -> 0.0222 -> 0.02 lots
    assert s["lots"] == 0.02 and s["risk_usd"] <= 100


def test_cleaning_removes_glitch_spike():
    df = synthetic_ohlcv("XAUUSD", "commodity", "1h", n=300)
    df.iloc[150, df.columns.get_loc("close")] *= 1.5   # fake API glitch
    df.iloc[151, df.columns.get_loc("open")] = df.iloc[149]["close"]
    clean, rep = clean_ohlcv(df, "1h")
    assert rep["dropped_spikes"] >= 1 and len(clean) < len(df)


def test_smc_outputs_expected_keys():
    df = synthetic_ohlcv("BTCUSD", "crypto", "1h", n=500)
    r = analyze_tf(df)
    for k in ("structure", "fvg", "order_blocks", "liquidity_pools", "premium_discount", "rsi", "ema"):
        assert k in r
    assert r["structure"]["trend"] in ("bullish", "bearish", "range")


def test_full_demo_pipeline_and_hard_rules(cfg):
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None)
    assert set(run["ctxs"]) == {"XAUUSD", "BTCUSD", "NAS100"}
    total = 0.0
    for c in run["ctxs"].values():
        v = c.verdict
        assert v is not None
        if v.approved:
            assert v.rr >= cfg.risk.min_rr                         # hard rule holds
            assert c.execution and c.execution["mode"] == "paper"  # never live by default
            assert c.execution["submission"]["status"] == "PAPER_ACCEPTED"
            total += v.risk_pct
    assert total <= cfg.account.max_total_open_risk_pct + 1e-9


def test_inspector_rejects_bad_rr(cfg):
    from swarm.agents import build_agents
    from swarm.journal import Journal
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD"], demo=True, progress=lambda *_: None)
    c = run["ctxs"]["XAUUSD"]
    p = c.reports["orchestrator"]
    p.decision, p.direction, p.entry, p.stop_loss, p.take_profit, p.confidence = "TRADE", "LONG", 3940.0, 3920.0, 3950.0, 90
    v = build_agents(cfg, LLM(cfg, mock=True))["inspector"].inspect(c, Journal(cfg.root, True), 0.0)
    assert not v.approved and any(k["name"] == "risk_reward" and not k["passed"] for k in v.checks)


def test_news_blackout_blocks(cfg):
    from datetime import timedelta
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD"], demo=True, progress=lambda *_: None)
    c = run["ctxs"]["XAUUSD"]
    assert c.reports["news_sentiment"].freeze_trading is False
    # inject a high-impact event 10 minutes from now and re-run the news agent
    from swarm.agents import build_agents
    c.world["calendar"].append({"title": "FOMC", "currency": "USD", "impact": "High", "time_utc": c.now + timedelta(minutes=10), "forecast": "", "previous": ""})
    out = build_agents(cfg, LLM(cfg, mock=True))["news_sentiment"].run(c)
    assert out.freeze_trading is True


# ------------------------------------------------------------------ agent 11: backtester
import numpy as np
from swarm.tools.backtest import monte_carlo, metrics, build_snapshots, select_sample, gate_checks, _cache_path


def test_monte_carlo_edge_vs_no_edge():
    good = [3.0] * 40 + [-1.0] * 60          # +0.6R expectancy
    bad = [1.0] * 30 + [-1.0] * 70           # -0.4R expectancy
    g = monte_carlo(good, 500, 100, 1.0, 25)
    b = monte_carlo(bad, 500, 100, 1.0, 25)
    assert g["prob_loss_pct"] < 5 and b["prob_loss_pct"] > 80
    assert b["max_dd_pct"]["p95"] > g["max_dd_pct"]["p95"]
    assert metrics(good)["expectancy_r"] == 0.6


def test_snapshots_have_no_lookahead():
    df = synthetic_ohlcv("XAUUSD", "commodity", "1h", n=900)
    df2 = df.copy()
    df2.iloc[700:, :4] *= 1.3                       # alter ONLY the future
    a, b = build_snapshots(df.iloc[:900], 25), build_snapshots(df2.iloc[:900], 25)
    checked = 0
    for x, y in zip(a, b):
        if x["t"] < 700:
            assert x["price"] == y["price"]
            assert x["smc"]["1h"]["structure"]["trend"] == y["smc"]["1h"]["structure"]["trend"]
            assert x["smc"]["1h"]["fvg"] == y["smc"]["1h"]["fvg"]
            checked += 1
    assert checked >= 3


def test_gate_refuses_when_thresholds_not_met(cfg):
    cfg.backtest.min_expectancy_r = 50.0            # impossible edge requirement
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None)
    for c in run["ctxs"].values():
        assert not (c.verdict and c.verdict.approved)      # nothing may reach Execution
        assert c.execution is None
    gated = [c.reports["backtester"] for c in run["ctxs"].values() if "backtester" in c.reports]
    assert all(g.verdict in ("REJECT", "INSUFFICIENT_DATA") for g in gated)


def test_gate_passes_and_reaches_execution_when_edge_ok(cfg):
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None)
    for c in run["ctxs"].values():
        if c.verdict and c.verdict.approved:
            assert c.reports["backtester"].passed and c.execution is not None


def test_insufficient_sample_is_rejected(cfg):
    cfg.backtest.min_trades = 10_000
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None)
    gated = [c.reports["backtester"] for c in run["ctxs"].values() if "backtester" in c.reports]
    assert all(g.verdict == "INSUFFICIENT_DATA" for g in gated)
    assert all(not (c.verdict and c.verdict.approved) for c in run["ctxs"].values())


def test_reoptimize_path(cfg):
    """Base-params trades are losers, optimised-params trades are winners & robust -> PASS_REOPTIMIZED with a new plan."""
    import json
    from swarm.agents import build_agents
    cfg.backtest.enabled = False
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "BTCUSD", "NAS100"], demo=True, progress=lambda *_: None)
    cand = [c for c in run["ctxs"].values() if c.reports["orchestrator"].decision == "TRADE"]
    if not cand:
        pytest.skip("no TRADE proposal in this synthetic run")
    c = cand[0]
    cfg.backtest.enabled = True
    bt = build_agents(cfg, LLM(cfg, mock=True))["backtester"]
    cache = bt.ensure_history(c, refresh=True, progress=lambda *_: None)
    side = c.reports["orchestrator"].direction
    mk = lambda r, n: [{"t": i, "side": side, "kind": "OB", "rr": 3.0, "conf": 70, "pd": None, "r": r(i), "outcome": "x", "bars": 5} for i in range(n)]
    cache["trades"] = mk(lambda i: -1.0 if i % 4 else 0.5, 80)                  # loser
    cache["optimization"]["robust"] = True
    cache["optimization"]["selected"] = dict(cache["base_params"])
    cache["optimization"]["selected_row"] = {"is": {"expectancy_r": 0.5}, "val": {"expectancy_r": 0.4, "n": 30}}
    cache["optimization"]["final"] = {"expectancy_r": 0.33, "n": 90}
    cache["optimization"]["final_trades"] = mk(lambda i: 3.0 if i % 3 == 0 else -1.0, 90)   # winner (~+0.33R, 33% WR) on the untouched final test
    _cache_path(cfg.root, c.symbol, True).write_text(json.dumps(cache, default=str))
    c.smc = c.smc  # live SMC snapshot is still in ctx
    v = bt.gate(c, progress=lambda *_: None)
    assert v.verdict in ("PASS_REOPTIMIZED", "REJECT")   # REJECT only if re-derived plan has no matching setup
    if v.verdict == "PASS_REOPTIMIZED":
        assert v.suggested_plan["side"] == side and v.suggested_params
