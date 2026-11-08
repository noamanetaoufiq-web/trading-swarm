"""1 - LWST / Orchestrator Agent: Chief Investment Officer & System Coordinator."""
from __future__ import annotations
import time
from ..context import AgentTrace
from ..schemas import TradeProposal, ExecutiveSummary
from .base import BaseAgent, dumps


class OrchestratorAgent(BaseAgent):
    key, number, emoji = "orchestrator", 1, "🎛️"
    name = "Chief Investment Officer & System Coordinator"
    schema = TradeProposal
    system_prompt = """You are the Chief Investment Officer & System Coordinator - the brain of the system.
You receive the reports of the specialist agents for ONE instrument: Technical (SMC plan), News & Sentiment (-10..+10, freeze flag), Macro (regime + bias), Social/On-chain (crowd + contrarian signal), Scanner (opportunity score), Data quality.
Your job: RESOLVE CONFLICTS between them (e.g. technicals say buy but news/macro say sell) and issue ONE final decision:
- decision TRADE only if a coherent thesis survives the conflicts and confidence >= the configured minimum; WAIT if the idea is valid but conditions are not right yet (event risk, no confirmation, conflicting signals); NO_TRADE if there is no edge.
- You may only use the Technical agent's entry/stop_loss/take_profit (you may tighten/refine them using levels in the data but never invent levels). Never override a news freeze.
- List each conflict and how you resolved it in conflicts_resolved. Thesis: 2-4 sentences. Invalidation: precise.
A separate Inspector will audit your proposal with hard rules (R:R >= 1:2, news windows, sizing) - do not argue with it; make sure the proposal is clean."""

    # ---- stage 0: routing (deterministic, saves tokens)
    def plan(self, symbols, cfg) -> dict:
        plan = {}
        for s in symbols:
            inst = cfg.instruments[s]
            plan[s] = {"asset_class": inst.asset_class, "timeframes": list(cfg.timeframes),
                       "agents": ["data_fetcher", "news_sentiment", "macro", "technical", "social_onchain", "scanner",
                                  "orchestrator", "inspector", "execution"],
                       "social_mode": "on-chain" if inst.asset_class == "crypto" else "vix-proxy"}
        return plan

    # ---- stage 4: decision
    def user_prompt(self, ctx):
        R = {k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in ctx.reports.items()
             if k in ("data_fetcher", "news_sentiment", "technical", "macro", "social_onchain", "scanner")}
        return (f"Instrument {ctx.symbol} ({ctx.inst.asset_class}). Last price {ctx.data.last_price}. Now (UTC) {ctx.now.isoformat(timespec='minutes')}.\n"
                f"User query: {ctx.query}\n"
                f"Config: min_confidence={ctx.cfg.risk.min_confidence}, min_rr={ctx.cfg.risk.min_rr}.\n"
                f"Agent reports:\n{dumps(R)}\n"
                f"Lessons from the Critic:\n{ctx.lessons or '(none yet)'}")

    def heuristic(self, ctx):
        r = ctx.reports
        tech, news, macro, social = r["technical"], r["news_sentiment"], r["macro"], r["social_onchain"]
        base = TradeProposal(symbol=ctx.symbol, confidence=tech.confidence)
        if tech.setup == "NONE":
            base.decision, base.thesis = "WAIT", "No valid technical setup: " + "; ".join(tech.reasoning[:2])
            return base
        d, conf, conflicts = tech.setup, tech.confidence, []
        aligned = (macro.asset_bias == "BULLISH" and d == "LONG") or (macro.asset_bias == "BEARISH" and d == "SHORT")
        opposed = (macro.asset_bias == "BULLISH" and d == "SHORT") or (macro.asset_bias == "BEARISH" and d == "LONG")
        if aligned:
            conf += 8
        elif opposed:
            conf -= 15
            conflicts.append("Macro opposes the technical setup -> confidence reduced")
        if (d == "LONG" and news.score <= -4) or (d == "SHORT" and news.score >= 4):
            conf -= 12
            conflicts.append(f"News sentiment ({news.score}) opposes the setup -> confidence reduced")
        elif (d == "LONG" and news.score >= 3) or (d == "SHORT" and news.score <= -3):
            conf += 4
        if social.contrarian_signal == d:
            conf += min(8, social.strength // 12)
        elif social.contrarian_signal not in ("NONE", d):
            conf -= 6
            conflicts.append("Crowd contrarian signal opposes the setup")
        conf = max(0, min(100, conf))
        base.direction, base.entry, base.stop_loss, base.take_profit = d, tech.entry, tech.stop_loss, tech.take_profit
        base.confidence, base.conflicts_resolved, base.invalidation = conf, conflicts, tech.invalidation
        base.decision = "TRADE" if conf >= ctx.cfg.risk.min_confidence else "WAIT"
        base.thesis = (f"{d} from technical plan; macro {macro.asset_bias}/{macro.regime}, news {news.score:+d}, "
                       f"crowd {social.crowd_bias}. Net confidence {conf}.")
        return base

    def run(self, ctx):
        p = super().run(ctx)
        p.symbol = ctx.symbol
        news = ctx.reports["news_sentiment"]
        if news.freeze_trading and p.decision == "TRADE":
            p.decision = "WAIT"
            p.conflicts_resolved = list(p.conflicts_resolved) + ["News freeze active -> forced WAIT (hard rule)"]
        if p.direction == "NONE" or None in (p.entry, p.stop_loss, p.take_profit):
            if p.decision == "TRADE":
                p.decision = "NO_TRADE"
            p.direction = "NONE" if p.decision != "TRADE" else p.direction
        return p

    # ---- final executive memo
    def summarize(self, items: list[dict], cfg, trace: list):
        t0 = time.time()
        prompt = (f"Write the final executive memo for the desk. Per-instrument outcomes:\n{dumps(items)}\n"
                  "headline: one sentence. ranking: instruments best->worst with a short reason. "
                  "portfolio_risk_notes: correlation/exposure/event-risk notes. bottom_line: what to do now.")
        if self.llm.is_mock:
            out, src = self._heuristic_summary(items), "heuristic"
        else:
            try:
                out = self.llm.complete(self.key, self.system(), prompt, ExecutiveSummary)
                src = "llm"
            except Exception:  # noqa: BLE001
                out, src = self._heuristic_summary(items), "heuristic-fallback"
        trace.append(AgentTrace("orchestrator.summary", src, round(time.time() - t0, 2)))
        return out

    @staticmethod
    def _heuristic_summary(items):
        ranked = sorted(items, key=lambda i: (i["approved"], i["confidence"]), reverse=True)
        n_app = sum(1 for i in items if i["approved"])
        return ExecutiveSummary(
            headline=f"{n_app} of {len(items)} setups approved by the Inspector.",
            ranking=[f"{i['symbol']}: {i['decision']} {i['direction']} (conf {i['confidence']}, approved={i['approved']})" for i in ranked],
            portfolio_risk_notes=["Instruments are correlated through yields/USD/liquidity: treat combined risk accordingly."],
            bottom_line="Only act on Inspector-approved setups; everything else waits for confirmation.")
