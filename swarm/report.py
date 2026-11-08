"""Markdown executive report: every agent's perspective per instrument."""
from __future__ import annotations
import json


def _bul(xs):
    return "\n".join(f"  - {x}" for x in xs) if xs else "  - (none)"


def _fmt(x):
    return "-" if x is None else x


def render_report(run: dict) -> str:
    L = []
    s = run["summary"]
    L += [f"# Trading Swarm - Executive Summary ({run['ts']:%Y-%m-%d %H:%M} UTC){' [DEMO: synthetic data + rule-based agents]' if run['demo'] else ''}", "",
          f"**{s.headline}**", "", "| Symbol | Decision | Dir | Entry | SL | TP | R:R | Conf | Inspector | Lots |", "|---|---|---|---|---|---|---|---|---|---|"]
    for sym in run["symbols"]:
        c = run["ctxs"][sym]
        p = c.reports.get("orchestrator")
        v = c.verdict
        if p:
            L.append(f"| {sym} | {p.decision} | {p.direction} | {_fmt(p.entry)} | {_fmt(p.stop_loss)} | {_fmt(p.take_profit)} | "
                     f"{_fmt(v.rr if v else None)} | {p.confidence} | {'APPROVED' if v and v.approved else 'REJECTED/NA'} | {v.lots if v and v.approved else '-'} |")
        else:
            L.append(f"| {sym} | NO_DATA | - | - | - | - | - | - | REJECTED | - |")
    L += ["", "**Ranking**", _bul(s.ranking), "", "**Portfolio / risk notes**", _bul(s.portfolio_risk_notes), "", f"**Bottom line:** {s.bottom_line}", ""]

    for sym in run["symbols"]:
        c = run["ctxs"][sym]
        r = c.reports
        L += ["---", f"## {sym}", ""]
        d = r["data_fetcher"]
        L += [f"### 2. Data Fetcher - quality {'OK' if d.quality_ok else 'FAILED'}",
              f"- last price **{c.data.last_price:.4f}**, spread {c.data.spread}, ATR 1h/4h: {c.data.atr.get('1h') and round(c.data.atr['1h'], 3)} / {c.data.atr.get('4h') and round(c.data.atr['4h'], 3)}" if c.data else "- no data",
              f"- issues: {d.issues or 'none'}", ""]
        if "orchestrator" not in r:
            L += ["_Pipeline stopped for this symbol: data unusable._", ""]
            continue
        n, t, m, so, sc, o = r["news_sentiment"], r["technical"], r["macro"], r["social_onchain"], r.get("scanner"), r["orchestrator"]
        L += [f"### 3. News & Sentiment - score **{n.score:+d}**{' - FREEZE' if n.freeze_trading else ''}", f"- {n.summary}", "- key events:", _bul(n.key_events), "- upcoming:", _bul(n.upcoming_risks), "",
              f"### 4. Technical (SMC) - bias {t.bias}, setup **{t.setup}**, confidence {t.confidence}",
              f"- plan: entry {_fmt(t.entry)} | SL {_fmt(t.stop_loss)} | TP {_fmt(t.take_profit)} | zone {_fmt(t.entry_zone)}", _bul(t.reasoning), f"- invalidation: {t.invalidation}", "",
              f"### 5. Macro - regime {m.regime}, asset bias {m.asset_bias}", _bul(m.drivers), f"- {m.summary}", ""]
        if o and sc:
            L += [f"### 6. Opportunity Scanner - score {sc.score}", _bul(sc.signals), ""]
        L += [f"### 8. Social & On-chain - crowd {so.crowd_bias}, contrarian {so.contrarian_signal} (strength {so.strength})", _bul(so.evidence), f"- {so.summary}", "",
              f"### 1. Orchestrator - {o.decision} {o.direction} (confidence {o.confidence})", f"- thesis: {o.thesis}", "- conflicts resolved:", _bul(o.conflicts_resolved), f"- invalidation: {o.invalidation}", ""]
        v = c.verdict
        L += [f"### 7. Inspector - {'APPROVED' if v.approved else 'REJECTED'}", "| check | pass | detail |", "|---|---|---|"]
        L += [f"| {k['name']} | {'✅' if k['passed'] else '❌'} | {k['detail']} |" for k in v.checks]
        if v.llm_audit:
            L.append(f"\n- LLM audit: veto={v.llm_audit['veto']}, false-signal risk {v.llm_audit['false_signal_risk']}, concerns {v.llm_audit['concerns']}")
        if v.reasons:
            L += ["- reasons:", _bul(v.reasons)]
        L.append("")
        bv = r.get("backtester")
        if bv:
            L += [f"### 11. Backtest / Monte Carlo gate - **{bv.verdict}** ({bv.sample_basis}, n={bv.n_trades})"]
            if bv.metrics.get("n"):
                m = bv.metrics
                L.append(f"- win rate {m['win_rate_pct']}% (95% CI {m['win_rate_ci95']}), realised R:R {m['realized_rr']}, expectancy {m['expectancy_r']}R, PF {m['profit_factor']}, max DD {m['max_dd_r']}R")
            if bv.mc.get("runs"):
                L.append(f"- Monte Carlo {bv.mc['runs']} paths x {bv.mc['horizon']} trades: P(loss) {bv.mc['prob_loss_pct']}%, DD p95 {bv.mc['max_dd_pct']['p95']}%, P(ruin) {bv.mc['prob_ruin_pct']}%")
            L += ["| check | pass | detail |", "|---|---|---|"] + [f"| {k['name']} | {'✅' if k['passed'] else '❌'} | {k['detail']} |" for k in bv.checks]
            if bv.optimization_note:
                L.append(f"- optimisation: {bv.optimization_note}")
            if bv.reasons:
                L += ["- reasons:", _bul(bv.reasons)]
            L.append("")
        tv = r.get("trade_validator")
        if tv:
            L += [f"### 12. Trade Validator - **{tv.verdict}** ({'approved' if tv.approved else 'refused'}{', risk x' + str(tv.risk_mult) if tv.risk_mult != 1.0 else ''})"]
            if tv.sim:
                nl = tv.sim["lenses"]["neutral"]["ladder"]
                L += [f"- break-even win rate {tv.sim['breakeven_pct']}% (cost-adjusted {tv.sim['breakeven_cost_adj_pct']}%), R:R {tv.sim['rr']}, volatility scale {tv.sim['vol_scale']}",
                      "| runs | fill % | win rate (95% CI) | TP / SL / time-out / unfilled | expectancy R |", "|---|---|---|---|---|"]
                L += [f"| {x['runs']} | {x['fill_prob_pct']} | {x['win_rate_pct']} ({x['win_rate_ci95'][0]}-{x['win_rate_ci95'][1]}) | {x['tp']} / {x['sl']} / {x['timeout']} / {x['unfilled']} | {x['expectancy_r']} |" for x in nl]
            if tv.evidence:
                L.append(f"- strategy evidence (agent 11): n={tv.evidence.get('n')}, win rate {tv.evidence.get('win_rate_pct')}% CI {tv.evidence.get('win_rate_ci95')}, expectancy {tv.evidence.get('expectancy_r')}R")
            L += ["| check | pass | detail |", "|---|---|---|"] + [f"| {k['name']} | {'⚠️' if k.get('warn') else '✅' if k['passed'] else '❌'} | {k['detail']} |" for k in tv.checks]
            if tv.reasons:
                L += ["- reasons:", _bul(tv.reasons)]
            L.append("")
        if c.execution:
            e = c.execution
            L += [f"### 9. Execution - {e['order_type']} {e['lots']} lots ({e['mode'].upper()}), primary {e['primary_broker']}: {e['submission']['status']}", f"- {e['reason']}",
                  "```json", json.dumps(e["payloads"][e["primary_broker"]], indent=2, default=str), "```", ""]
        else:
            L += ["### 9. Execution - not triggered (no Inspector-approved trade)", ""]
    st = run["journal_stats"]
    L += ["---", "### 10. Critic / Learning",
          f"- journal stats: {st}", f"- lessons currently injected into prompts:\n{run['lessons'] or '  (none yet)'}",
          "- Run `python main.py review` after trades resolve: the Critic classifies wins/losses (variance vs. agent error) and feeds lessons back.", "",
          "### Run info", f"- LLM usage: {run['usage']}", "- agent sources: " + "; ".join(f"{sym}: " + ", ".join(f"{t.agent}={t.source}" for t in c.trace) for sym, c in run["ctxs"].items()),
          "", "_Analysis for a human-supervised system, not financial advice. Verify data and levels on your own platform._"]
    return "\n".join(L)
