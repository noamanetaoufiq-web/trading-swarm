"""Orchestration of the 12 agents (follows the architecture diagram).

 0 Orchestrator.plan  ->  1 Data Fetcher (parallel/symbol)  ->  2 Scanner (cross-market)
 -> 3 News | Macro | Technical | Social/On-chain (parallel)  ->  4 Orchestrator.decide (resolve conflicts)
 -> 5 Inspector (hard rules + audit, sequential for exposure accounting)  ->  11 Backtest gate  ->  12 Trade Validator
 -> 6 Execution (approved only)
 -> 7 Journal  ->  8 Executive summary.   The Critic (10) runs later via `main.py review` on closed trades.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone

from .agents import build_agents
from .context import AgentTrace, Ctx
from .journal import Journal
from .schemas import DataReview
from .tools import accounts as accts
from .tools.risk import position_size
from .tools.macro_data import fetch_macro
from .tools.news_feeds import fetch_calendar, fetch_headlines


def _dump(o):
    return o.model_dump() if hasattr(o, "model_dump") else (asdict(o) if hasattr(o, "__dataclass_fields__") else o)


def run_analysis(cfg, llm, symbols, query="", demo=False, equity=None, allow_live=False, progress=print, account=None) -> dict:
    with accts.keep_mt5():            # one MT5 login for the whole run (account read, candles, history, spreads); closed afterwards
        return _run_analysis(cfg, llm, symbols, query, demo, equity, allow_live, progress, account)


def _run_analysis(cfg, llm, symbols, query="", demo=False, equity=None, allow_live=False, progress=print, account=None) -> dict:
    """`account` = dashboard account dict (broker / type / sandbox / label). The account SIZE is never typed into the code: it is read from the
    linked broker here (demo or real); with no broker it is the paper simulator's capital (`equity` override, else config) + realised paper P&L."""
    now = datetime.now(timezone.utc)
    journal = Journal(cfg.root, demo)
    acct_state = accts.resolve_account(cfg.root, account, cfg, journal, equity_override=equity, demo=demo)
    if not acct_state["ok"]:
        raise RuntimeError(f"cannot read the {acct_state['broker']} account - run stopped (no fallback to a typed equity): {acct_state['message']}")
    equity = float(acct_state["equity_usd"])
    progress(f"• account: {acct_state['broker']} / {acct_state['kind']} / equity {equity:,.2f} {acct_state.get('unit_currency') or acct_state['currency']} ({acct_state['source']})")
    for note in accts.apply_broker_specs(cfg, acct_state):       # real symbol names, lot limits, tick values, spreads: read from the account, not typed
        progress(f"  · broker spec {note}")
    A = build_agents(cfg, llm)

    progress("• world data: macro / calendar / headlines")
    kws = {s: cfg.instruments[s].news_keywords for s in symbols}
    with ThreadPoolExecutor(3) as ex:
        fm = ex.submit(fetch_macro, demo)
        fc = ex.submit(fetch_calendar, cfg.calendar.url, cfg.calendar.manual_events, demo, now)
        fn = ex.submit(fetch_headlines, cfg.news_feeds, kws, 12, demo)
        world = {"macro": fm.result(), "calendar": fc.result(), "headlines": fn.result()}

    lessons = journal.recent_lessons()
    ctxs = {s: Ctx(symbol=s, inst=cfg.instruments[s], cfg=cfg, query=query or f"Analyse {s}", demo=demo,
                   equity=equity, now=now, world=world, lessons=lessons, account=acct_state) for s in symbols}

    progress("• [1] Orchestrator: planning")
    plan = A["orchestrator"].plan(symbols, cfg)

    progress("• [2] Data Fetcher: OHLCV 1m/15m/1h/4h/1d, cleaning, ATR, spread")

    def do_data(s):
        c = ctxs[s]
        try:
            c.reports["data_fetcher"] = A["data_fetcher"].run(c)
        except Exception as e:  # noqa: BLE001
            c.reports["data_fetcher"] = DataReview(quality_ok=False, issues=[f"fetch failed: {type(e).__name__}: {e}"])
            c.extra["abort"] = str(e)

    with ThreadPoolExecutor(len(symbols)) as ex:
        list(ex.map(do_data, symbols))
    live = {s: c for s, c in ctxs.items() if c.data is not None and c.reports["data_fetcher"].quality_ok}

    progress("• [6] Opportunity Scanner: cross-market scan")
    scan, scan_trace = None, []
    if live:
        scan, scan_trace = A["scanner"].scan(live, cfg, now, world, demo)
        for cand in scan.candidates:
            if cand.symbol in ctxs:
                ctxs[cand.symbol].reports["scanner"] = cand

    progress("• [3][4][5][8] News | Technical | Macro | Social/On-chain running in parallel")
    jobs = [(s, k) for s in live for k in ("news_sentiment", "technical", "macro", "social_onchain")]

    def do_job(job):
        s, k = job
        ctxs[s].reports[k] = A[k].run(ctxs[s])

    with ThreadPoolExecutor(max(1, len(jobs))) as ex:
        list(ex.map(do_job, jobs))

    progress("• [1] Orchestrator: resolving conflicts -> trade proposals")

    def do_decide(s):
        ctxs[s].reports["orchestrator"] = A["orchestrator"].run(ctxs[s])

    with ThreadPoolExecutor(max(1, len(live))) as ex:
        list(ex.map(do_decide, live))

    progress("• [7] Inspector: hard rules + false-signal audit  ->  [11] Backtest / Monte Carlo gate  ->  [12] Trade Validator")
    running = journal.open_risk_pct()
    bt_on = bool(cfg.backtest.enabled)
    for s in sorted(live, key=lambda k: -ctxs[k].reports["orchestrator"].confidence):
        c = ctxs[s]
        v = A["inspector"].inspect(c, journal, running)
        if v.approved and bt_on:
            progress(f"• [11] Backtester gate for {s}")
            bv = A["backtester"].gate(c, progress=progress)
            c.reports["backtester"] = bv
            if bv.verdict == "PASS_REOPTIMIZED":
                pl, p = bv.suggested_plan, c.reports["orchestrator"]
                p.entry, p.stop_loss, p.take_profit = pl["entry"], pl["sl"], pl["tp"]
                p.conflicts_resolved = list(p.conflicts_resolved) + [f"Backtester re-parameterised the plan: {bv.optimization_note}"]
                v = A["inspector"].inspect(c, journal, running)  # hard rules again on the new levels
                if not v.approved:
                    bv.verdict = "REJECT"
                    bv.reasons.append("re-optimised plan failed the Inspector hard rules")
            if not bv.passed:
                v.approved = False
                v.reasons.append(f"backtest_gate: {bv.verdict} - " + "; ".join(bv.reasons[:3]))
            c.trace.append(AgentTrace("backtester", "deterministic+llm" if bv.llm_review else "deterministic", 0.0))
        if v.approved and cfg.validator.enabled:
            progress(f"• [12] Trade Validator for {s}: simulating the confirmed trade at {list(cfg.validator.runs_ladder)} runs")
            tv = A["trade_validator"].validate(c, c.reports.get("backtester"), progress=progress)
            c.reports["trade_validator"] = tv
            if tv.approved and tv.risk_mult != 1.0 and getattr(tv, "sizing", None):
                v.lots, v.risk_usd, v.risk_pct = tv.sizing["lots"], tv.sizing["risk_usd"], tv.sizing["risk_pct"]   # MARGINAL -> reduced risk
            if not tv.approved:
                v.approved = False
                v.reasons.append(f"trade_validator: {tv.verdict} - " + "; ".join(tv.reasons[:3]))
            c.trace.append(AgentTrace("trade_validator", "deterministic+llm" if tv.llm_review else "deterministic", 0.0))
        if v.approved:
            running += v.risk_pct

    progress("• [9] Execution: building broker payloads for approved trades")
    for s, c in live.items():
        if c.verdict and c.verdict.approved:
            c.execution = A["execution"].build(c, allow_live)

    progress("• journal + executive summary")
    stamp = now.strftime("%Y%m%d-%H%M%S")
    items = []
    for s, c in ctxs.items():
        p = c.reports.get("orchestrator")
        approved = bool(c.verdict and c.verdict.approved)
        items.append({"symbol": s, "decision": p.decision if p else "NO_DATA", "direction": p.direction if p else "NONE",
                      "confidence": p.confidence if p else 0, "approved": approved,
                      "rr": c.verdict.rr if c.verdict else None,
                      "rejections": (c.verdict.reasons if c.verdict else c.reports["data_fetcher"].issues)})
        if p and p.decision == "TRADE":
            journal.append({
                "id": f"{stamp}-{s}", "ts": now.isoformat(timespec="seconds"), "symbol": s, "mode": cfg.execution.mode, "demo": demo,
                "status": "PENDING" if approved else "REJECTED",
                "proposal": p.model_dump(), "verdict": c.verdict.to_dict() if c.verdict else None,
                "orders": ({k: c.execution[k] for k in ("order_type", "lots", "primary_broker", "submission")} if c.execution else None),
                "backtest": c.reports["backtester"].to_dict() if "backtester" in c.reports else None,
                "validation": c.reports["trade_validator"].to_dict() if "trade_validator" in c.reports else None,
                "account": {k: acct_state.get(k) for k in ("broker", "kind", "currency", "equity", "equity_usd", "source", "day_pnl_pct", "week_pnl_pct", "drawdown_pct")},
                "risk_pct": c.verdict.risk_pct if approved else 0, "lots": c.verdict.lots if approved else 0,
                "reports": {k: _dump(v) for k, v in c.reports.items() if k not in ("orchestrator", "backtester", "trade_validator")},
                "trace": [asdict(t) for t in c.trace]})
    summary_trace = []
    summary = A["orchestrator"].summarize(items, cfg, summary_trace)

    return {"ts": now, "stamp": stamp, "symbols": symbols, "ctxs": ctxs, "plan": plan, "scan": scan,
            "scan_trace": scan_trace + summary_trace, "summary": summary, "items": items, "usage": dict(llm.usage),
            "journal_stats": journal.stats(), "lessons": lessons, "demo": demo, "world": world, "account": acct_state}
