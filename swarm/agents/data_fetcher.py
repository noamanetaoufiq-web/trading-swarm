"""2 - Data Fetcher Agent: Quantitative Market Data Integration Engineer.
Deterministic: fetch OHLCV for 1m/15m/1h/4h/1d, clean, ATR, spread. LLM: reviews the data-quality report."""
from __future__ import annotations
from ..schemas import DataReview
from ..tools.market_data import build_market_data, market_summary
from .base import BaseAgent, dumps


class DataFetcherAgent(BaseAgent):
    key, number, emoji = "data_fetcher", 2, "📊"
    name = "Quantitative Market Data Integration Engineer"
    schema = DataReview
    system_prompt = """You are the Quantitative Market Data Integration Engineer.
The raw OHLCV for several timeframes has ALREADY been fetched and cleaned by code. Your job is to audit the cleaning/quality reports and the summary stats:
- flag API glitches, stale feeds (minutes_since_last_candle), many dropped spikes/NaNs, suspicious gaps, ATR that is abnormal vs its 50-bar average, absurd spreads;
- weekend/holiday gaps are normal for non-crypto markets, 24/7 crypto should have almost no gaps;
- set quality_ok=false ONLY if the data is unusable or dangerous for trading decisions; otherwise true with issues listed."""

    def fetch(self, ctx):
        ctx.data = build_market_data(ctx.symbol, ctx.inst, ctx.cfg.timeframes, demo=ctx.demo)
        ctx.extra["market_summary"] = market_summary(ctx.data)

    def user_prompt(self, ctx):
        return f"Instrument {ctx.symbol} ({ctx.inst.asset_class}). Data summary + quality reports:\n{dumps(ctx.extra['market_summary'])}"

    def hard_checks(self, ctx) -> list[str]:
        issues = []
        for tf in ("15m", "1h", "4h"):
            if tf not in ctx.data.frames:
                issues.append(f"{tf} data missing: {ctx.data.quality.get(tf)}")
            elif len(ctx.data.frames[tf]) < 100:
                issues.append(f"{tf} has only {len(ctx.data.frames[tf])} candles")
        return issues

    def heuristic(self, ctx):
        issues = self.hard_checks(ctx)
        for tf, q in ctx.data.quality.items():
            if q.get("dropped_spikes", 0) > 3:
                issues.append(f"{tf}: {q['dropped_spikes']} spike candles removed")
        return DataReview(quality_ok=not any("missing" in i or "only" in i for i in issues), issues=issues,
                          summary="Rule-based data audit (no LLM).")

    def run(self, ctx):
        self.fetch(ctx)
        review = super().run(ctx)
        hard = self.hard_checks(ctx)
        if hard:
            review.quality_ok = False
            review.issues = list(review.issues) + hard
        return review
