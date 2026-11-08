"""7 - Inspector / Risk Manager Agent: Chief Risk Officer & Trade Verification Inspector (the gatekeeper).

Two layers:
  1) HARD RULES in code (R:R >= 1:2, news windows, ATR-based stop sanity, sizing, exposure caps, data health).
     The LLM can never override a failed hard rule.
  2) LLM devil's-advocate audit for false signals - it can only VETO, never approve.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from ..schemas import InspectorAudit
from ..tools.risk import levels_sane, rr_ratio, position_size
from .base import BaseAgent, dumps


@dataclass
class Verdict:
    approved: bool = False
    checks: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    rr: float | None = None
    lots: float = 0.0
    risk_usd: float = 0.0
    risk_pct: float = 0.0
    llm_audit: dict | None = None

    def to_dict(self):
        return asdict(self)


class InspectorAgent(BaseAgent):
    key, number, emoji = "inspector", 7, "👮"
    name = "Chief Risk Officer & Trade Verification Inspector"
    schema = InspectorAudit
    system_prompt = """You are the Chief Risk Officer & Trade Verification Inspector - a strict, uncompromising gatekeeper.
All numeric hard rules (R:R >= 1:2, news windows, sizing, exposure caps) were ALREADY verified by code and passed. Your remaining job is the qualitative audit for FALSE SIGNALS:
- is the thesis built on one weak confluence? chasing a move? entering into an obvious liquidity grab? ignoring a conflicting agent report? stale or low-quality data? event risk the calendar rules do not capture (e.g. elections, central-bank speakers, geopolitical headlines)?
- set veto=true if the trade should be rejected for any such reason; false_signal_risk 0-100; list concrete concerns.
You can only veto; you cannot approve something that failed a hard rule. When in doubt about a real risk, veto."""

    def _chk(self, v: Verdict, name, passed, detail, warn=False):
        v.checks.append({"name": name, "passed": bool(passed), "detail": detail, "warn": bool(warn)})
        if not passed:
            v.reasons.append(f"{name}: {detail}")

    def evaluate(self, ctx, journal, running_open_risk: float) -> Verdict:
        cfg, p, v = ctx.cfg, ctx.reports["orchestrator"], Verdict()
        r = cfg.risk
        if p.decision != "TRADE":
            self._chk(v, "orchestrator_decision", False, f"decision is {p.decision}, nothing to approve")
            return v
        d = p.direction
        self._chk(v, "levels_sane", levels_sane(d, p.entry, p.stop_loss, p.take_profit), f"{d} entry {p.entry} sl {p.stop_loss} tp {p.take_profit}")
        if not v.checks[-1]["passed"]:
            return v
        v.rr = round(rr_ratio(d, p.entry, p.stop_loss, p.take_profit), 2)
        self._chk(v, "risk_reward", v.rr >= r.min_rr, f"R:R {v.rr} (min {r.min_rr})")
        self._chk(v, "confidence", p.confidence >= r.min_confidence, f"{p.confidence} (min {r.min_confidence})")
        news = ctx.reports["news_sentiment"]
        self._chk(v, "news_window", not news.freeze_trading and not ctx.extra.get("news_blackout"),
                  "high-impact event inside blackout / freeze flag" if (news.freeze_trading or ctx.extra.get("news_blackout")) else "clear")
        dq = ctx.reports["data_fetcher"]
        fresh_limit = 45 if ctx.inst.asset_class == "crypto" else 3 * 24 * 60
        age = (ctx.data.quality.get("15m") or {}).get("minutes_since_last_candle")
        self._chk(v, "data_quality", dq.quality_ok and (age is None or age <= fresh_limit), f"quality_ok={dq.quality_ok}, last 15m candle {age} min old")
        degraded = [t.agent for t in ctx.trace if t.source == "heuristic-fallback"]
        self._chk(v, "agents_healthy", not degraded, f"LLM failed for: {degraded}" if degraded else "all agents ran normally")
        atr1 = ctx.smc.get("1h", {}).get("atr") or ctx.data.atr.get("1h")
        dist = abs(p.entry - p.stop_loss)
        if atr1:
            ratio = dist / atr1
            self._chk(v, "stop_vs_atr", r.sl_atr_min <= ratio <= r.sl_atr_max, f"SL distance {ratio:.2f} x ATR(1h) (allowed {r.sl_atr_min}-{r.sl_atr_max})")
            ed = abs(ctx.data.last_price - p.entry) / atr1
            self._chk(v, "entry_reachable", ed <= r.max_entry_distance_atr, f"entry {ed:.2f} x ATR(1h) from price (max {r.max_entry_distance_atr})")
        sp = ctx.data.spread / dist if dist else 9
        self._chk(v, "spread_vs_stop", sp <= r.max_spread_to_sl_ratio, f"spread/stop {sp:.3f} (max {r.max_spread_to_sl_ratio})")
        macro = ctx.reports["macro"]
        opposed = (macro.asset_bias == "BULLISH" and d == "SHORT") or (macro.asset_bias == "BEARISH" and d == "LONG")
        self._chk(v, "macro_alignment", not (opposed and p.confidence < 75), f"macro {macro.asset_bias}/{macro.regime} vs {d} (counter-macro needs conf >= 75)")
        acc = ctx.account or {}
        if acc.get("equity") is not None and "day_pnl_pct" in acc:
            # equity based: realised + FLOATING P&L of open positions, since the first snapshot of the UTC day / week
            dpl, wpl, ddp = acc["day_pnl_pct"], acc.get("week_pnl_pct", 0.0), acc.get("drawdown_pct", 0.0)
            self._chk(v, "daily_loss_limit", dpl > -cfg.account.max_daily_loss_pct, f"equity {dpl:+.2f}% today incl. floating (limit -{cfg.account.max_daily_loss_pct}%)")
            self._chk(v, "weekly_loss_limit", wpl > -cfg.account.max_weekly_loss_pct, f"equity {wpl:+.2f}% this week (limit -{cfg.account.max_weekly_loss_pct}%)")
            self._chk(v, "equity_drawdown_limit", ddp < cfg.account.max_equity_drawdown_pct, f"{ddp:.2f}% below recorded equity peak ${acc.get('peak_equity')} (limit {cfg.account.max_equity_drawdown_pct}%)")
            self._chk(v, "account_currency", acc.get("currency_ok", True), f"account currency {acc.get('currency', 'USD')} (sizing assumes USD)")
        else:
            pnl = journal.today_pnl_pct()
            self._chk(v, "daily_loss_limit", pnl > -cfg.account.max_daily_loss_pct, f"today realised {pnl}% (limit -{cfg.account.max_daily_loss_pct}%)")
        proxy = bool(ctx.inst.get("feed_is_proxy"))
        need_broker_feed = bool(cfg.risk.get("require_broker_feed", False))
        self._chk(v, "data_feed", not (proxy and need_broker_feed),
                  (f"{ctx.symbol} history/analysis uses a PROXY ({ctx.inst.get('yahoo')}), not the broker's own feed" if proxy else f"feed={ctx.inst.get('feed', 'yahoo')}"),
                  warn=proxy)
        from ..tools.backtest import cost_model
        size = position_size(ctx.equity, cfg.account.risk_per_trade_pct, p.entry, p.stop_loss, ctx.inst, cost_model(cfg, ctx.inst))
        v.lots, v.risk_usd, v.risk_pct = size["lots"], size["risk_usd"], size["risk_pct"]
        self._chk(v, "position_size", size["lots"] > 0, f"{size['lots']} lots, risk ${size['risk_usd']} ({size['risk_pct']}%) {size['note']}")
        tot = running_open_risk + v.risk_pct
        self._chk(v, "total_open_risk", tot <= cfg.account.max_total_open_risk_pct, f"{tot:.2f}% after this trade (cap {cfg.account.max_total_open_risk_pct}%)")
        v.approved = all(c["passed"] for c in v.checks)
        return v

    def user_prompt(self, ctx):
        R = {k: (x.model_dump() if hasattr(x, "model_dump") else x) for k, x in ctx.reports.items()
             if k in ("orchestrator", "technical", "news_sentiment", "macro", "social_onchain", "scanner")}
        return (f"Instrument {ctx.symbol}, last price {ctx.data.last_price}. Hard checks (all passed): {dumps(ctx.verdict.checks)}\n"
                f"Agent reports:\n{dumps(R)}\nUpcoming calendar: {dumps(ctx.extra.get('news_upcoming', []))}")

    def heuristic(self, ctx):
        p = ctx.reports["orchestrator"]
        risk = 20 + 15 * len(p.conflicts_resolved) + (10 if p.confidence < 70 else 0)
        return InspectorAudit(veto=False, false_signal_risk=min(100, risk),
                              concerns=list(p.conflicts_resolved), summary="Rule-based audit (no LLM).")

    def inspect(self, ctx, journal, running_open_risk: float) -> Verdict:
        v = self.evaluate(ctx, journal, running_open_risk)
        ctx.verdict = v
        if v.approved:
            audit = super().run(ctx)
            v.llm_audit = audit.model_dump()
            if audit.veto:
                v.approved = False
                v.reasons.append(f"llm_audit_veto: {'; '.join(audit.concerns[:3]) or audit.summary}")
        return v
