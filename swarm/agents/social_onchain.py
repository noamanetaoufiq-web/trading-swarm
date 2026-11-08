"""8 - Social & On-Chain Sentiment Agent: Crowd Psychology & On-Chain Metrics Analyst."""
from __future__ import annotations
from ..schemas import SocialReport
from ..tools.sentiment_data import social_snapshot
from .base import BaseAgent, dumps


class SocialOnChainAgent(BaseAgent):
    key, number, emoji = "social_onchain", 8, "🐋"
    name = "Crowd Psychology & On-Chain Metrics Analyst"
    schema = SocialReport
    system_prompt = """You are the Crowd Psychology & On-Chain Metrics Analyst.
Read behavioural-finance metrics (Fear & Greed, funding rates, open interest changes, long/short ratio, whale flows; VIX as a proxy for non-crypto assets) and identify retail FOMO / capitulation.
- contrarian_signal LONG when the crowd is capitulating/extremely fearful and positioning is washed out; SHORT when crowd is euphoric/over-leveraged long (positive funding + rising OI + greed) -> long-squeeze risk; otherwise NONE.
- Fear & Greed around 30 is fear but NOT extreme: do not call a clean contrarian long on that alone.
- If the data is missing for this asset class say so and keep strength low."""

    def user_prompt(self, ctx):
        return f"Instrument: {ctx.symbol} ({ctx.inst.asset_class}).\nCrowd data: {dumps(ctx.extra['social_data'])}"

    def heuristic(self, ctx):
        d = ctx.extra["social_data"]
        if d.get("asset_class") != "crypto":
            vix = (d.get("vix") or {}).get("last") or 0
            bias = "FEARFUL" if vix > 25 else "NEUTRAL"
            return SocialReport(crowd_bias=bias, contrarian_signal="NONE", strength=10,
                                evidence=[f"VIX {vix}", "no on-chain data for this asset"], summary="VIX proxy only.")
        fg = (d.get("fear_greed") or {}).get("latest", 50)
        fr = (d.get("futures") or {}).get("funding_rate_pct_latest", 0) or 0
        oi = (d.get("futures") or {}).get("open_interest_24h_change_pct", 0) or 0
        ev = [f"Fear&Greed {fg}", f"funding {fr}%", f"OI 24h {oi}%"]
        if fg >= 75 and fr > 0.03:
            return SocialReport(crowd_bias="EUPHORIC", contrarian_signal="SHORT", strength=min(100, fg), evidence=ev, summary="Crowded longs.")
        if fg <= 20 and fr <= 0:
            return SocialReport(crowd_bias="CAPITULATING", contrarian_signal="LONG", strength=min(100, 100 - fg), evidence=ev, summary="Capitulation.")
        bias = "FEARFUL" if fg < 40 else "GREEDY" if fg > 60 else "NEUTRAL"
        return SocialReport(crowd_bias=bias, contrarian_signal="NONE", strength=15, evidence=ev, summary="No extreme.")

    def run(self, ctx):
        ctx.extra["social_data"] = social_snapshot(ctx.inst, ctx.world["macro"], ctx.demo)
        return super().run(ctx)
