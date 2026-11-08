"""3 - News & Sentiment Agent: Chief Market Sentiment & Fundamental Intelligence Analyst."""
from __future__ import annotations
from ..schemas import SentimentReport
from ..tools.news_feeds import upcoming, high_impact_in_window, event_to_dict
from .base import BaseAgent, dumps

POS = ("surge", "rally", "record", "beat", "cut", "dovish", "inflow", "approve", "upgrade", "gain", "rebound", "strong")
NEG = ("plunge", "selloff", "sell-off", "hack", "miss", "hike", "hawkish", "outflow", "ban", "downgrade", "fall", "slump", "war", "crash", "weak")


class NewsSentimentAgent(BaseAgent):
    key, number, emoji = "news_sentiment", 3, "📰"
    name = "Chief Market Sentiment & Fundamental Intelligence Analyst"
    schema = SentimentReport
    web_search = True
    system_prompt = """You are the Chief Market Sentiment & Fundamental Intelligence Analyst.
You receive filtered headlines and the upcoming economic calendar (CPI, NFP, rate decisions...). You may also use web search to verify/enrich the very latest news for the instrument.
- Filter out noise; evaluate only high-impact items for THIS instrument.
- score: strict integer from -10 (extremely bearish for the instrument) to +10 (extremely bullish). 0 = nothing material.
- freeze_trading=true if a high-impact event is imminent (inside the blackout window) or conditions are too chaotic/unclear to trade.
- key_events: what actually moves price now. upcoming_risks: scheduled catalysts in the next 48h."""

    def prepare(self, ctx):
        mins = int(ctx.cfg.risk.news_blackout_minutes)
        cur = tuple(ctx.inst.news_currencies)
        ctx.extra["news_upcoming"] = [event_to_dict(e) for e in upcoming(ctx.world["calendar"], ctx.now, 48, currencies=cur)]
        ctx.extra["news_blackout"] = [event_to_dict(e) for e in high_impact_in_window(ctx.world["calendar"], ctx.now, mins, cur)]
        ctx.extra["news_headlines"] = ctx.world["headlines"].get(ctx.symbol, [])

    def user_prompt(self, ctx):
        return (f"Instrument: {ctx.symbol} ({ctx.inst.asset_class}). Now (UTC): {ctx.now.isoformat(timespec='minutes')}\n"
                f"High-impact events INSIDE the +/-{ctx.cfg.risk.news_blackout_minutes} min blackout window: {dumps(ctx.extra['news_blackout'])}\n"
                f"Upcoming calendar (48h, USD high/medium): {dumps(ctx.extra['news_upcoming'])}\n"
                f"Filtered headlines: {dumps(ctx.extra['news_headlines'])}\n"
                f"Macro context: {dumps(ctx.world['macro'])}")

    def heuristic(self, ctx):
        titles = " ".join(h["title"].lower() for h in ctx.extra["news_headlines"])
        score = max(-10, min(10, sum(titles.count(w) for w in POS) - sum(titles.count(w) for w in NEG)))
        return SentimentReport(
            score=score, freeze_trading=bool(ctx.extra["news_blackout"]),
            key_events=[h["title"] for h in ctx.extra["news_headlines"][:3]],
            upcoming_risks=[f"{e['title']} @ {e['time_utc']}" for e in ctx.extra["news_upcoming"][:5]],
            summary="Keyword-lexicon sentiment (no LLM).")

    def run(self, ctx):
        self.prepare(ctx)
        out = super().run(ctx)
        if ctx.extra["news_blackout"]:  # deterministic override: calendar blackout always freezes
            out.freeze_trading = True
        return out
