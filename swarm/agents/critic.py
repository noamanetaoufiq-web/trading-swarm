"""10 - Critic / Learning Agent: Post-Trade Performance Auditor & Continuous Learning Engine."""
from __future__ import annotations
from ..schemas import CriticReport, ParamFeedback
from .base import BaseAgent, dumps


class CriticAgent(BaseAgent):
    key, number, emoji = "critic", 10, "📝"
    name = "Post-Trade Performance Auditor & Continuous Learning Engine"
    schema = CriticReport
    system_prompt = """You are the Post-Trade Performance Auditor & Continuous Learning Engine.
You receive a CLOSED trade: the original proposal, every agent's report at decision time, the Inspector verdict and the outcome (WIN/LOSS, R multiple, max favourable/adverse excursion in R, bars to fill/in trade).
Perform a root-cause analysis: was the result NORMAL_VARIANCE (a sound, correctly-sized trade that simply lost) or a specific miscalculation (BAD_ENTRY, BAD_STOP, BAD_TARGET, NEWS_SHOCK, MACRO_MISREAD, TECHNICAL_MISREAD, SENTIMENT_MISREAD, EXECUTION)? Do not blame agents for normal variance. A win can still contain a flaw (e.g. TP far too close, MFE >> TP).
blamed_agents: agent keys (orchestrator, technical, macro, news_sentiment, social_onchain, inspector...). lessons: short, actionable, reusable rules (max 3). parameter_feedback: concrete suggested parameter changes (advisory only - a human decides)."""

    def user_prompt(self, ctx):
        t = ctx.extra["trade"]
        slim = {k: t.get(k) for k in ("id", "ts", "symbol", "proposal", "verdict", "orders", "reports")}
        return f"Trade record:\n{dumps(slim)}\nOutcome:\n{dumps(ctx.extra['outcome'])}"

    def heuristic(self, ctx):
        o, t = ctx.extra["outcome"], ctx.extra["trade"]
        rr = (t.get("verdict") or {}).get("rr")
        if o["result"] == "WIN":
            cause, lessons, blamed = "NORMAL_VARIANCE", [], []
            if o["mfe_r"] > 2 * (rr or 2):
                cause, lessons = "BAD_TARGET", [f"Price ran {o['mfe_r']}R vs {rr}R target: consider partials / trailing."]
        else:
            if o["mfe_r"] < 0.3:
                cause, blamed, lessons = "BAD_ENTRY", ["technical"], ["Price went against the entry almost immediately: wait for 15m confirmation inside the zone."]
            elif o["mfe_r"] >= 1.0:
                cause, blamed, lessons = "BAD_TARGET", ["technical"], [f"Trade was +{o['mfe_r']}R before reversing: consider partial profit at 1R / break-even."]
            else:
                cause, blamed, lessons = "NORMAL_VARIANCE", [], []
        return CriticReport(trade_id=t["id"], outcome=f"{o['result']} {o['r_multiple']}R", root_cause=cause,
                            blamed_agents=blamed, lessons=lessons,
                            parameter_feedback=[ParamFeedback(parameter="n/a", suggestion="needs more trades before tuning")])
