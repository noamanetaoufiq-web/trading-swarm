"""5 - Macro-Economic Agent: Senior Global Macro Economist."""
from __future__ import annotations
from ..schemas import MacroReport
from ..tools.macro_data import regime_hint
from .base import BaseAgent, dumps


class MacroAgent(BaseAgent):
    key, number, emoji = "macro", 5, "🌍"
    name = "Senior Global Macro Economist"
    schema = MacroReport
    web_search = True
    system_prompt = """You are the Senior Global Macro Economist. Provide the top-down context:
- central bank stances (Fed path: cuts/hold/hikes, next meeting), US Dollar Index (DXY), bond yields, VIX, oil, equities;
- decide the regime: RISK_ON, RISK_OFF or NEUTRAL, and the macro bias for THIS instrument (BULLISH/BEARISH/NEUTRAL).
Asset rules of thumb: gold is driven mostly by real yields + USD (and safe-haven flows) - rising yields/USD usually bearish, even in risk-off; BTC and Nasdaq are risk/liquidity assets - risk-off + rising yields usually bearish.
You may use web search to confirm the latest central-bank decisions/expectations; cite concrete numbers in drivers."""

    def user_prompt(self, ctx):
        return (f"Instrument: {ctx.symbol} ({ctx.inst.asset_class}). Now (UTC): {ctx.now.isoformat(timespec='minutes')}\n"
                f"Macro snapshot (daily data): {dumps(ctx.world['macro'])}\n"
                f"Rule-based regime hint (use as a sanity check, not gospel): {dumps(regime_hint(ctx.world['macro']))}")

    def heuristic(self, ctx):
        h = regime_hint(ctx.world["macro"])
        if ctx.inst.asset_class == "commodity":
            bias = h["gold_bias"]
        else:
            bias = {"RISK_ON": "BULLISH", "RISK_OFF": "BEARISH", "NEUTRAL": "NEUTRAL"}[h["regime"]]
        m = ctx.world["macro"]
        drivers = [f"DXY 20d {m.get('DXY', {}).get('chg_20d_pct')}%", f"US10Y 20d {m.get('US10Y', {}).get('chg_20d_pct')}%",
                   f"VIX {m.get('VIX', {}).get('last')}"]
        return MacroReport(regime=h["regime"], asset_bias=bias, drivers=drivers, summary="Rule-based regime (no LLM).")
