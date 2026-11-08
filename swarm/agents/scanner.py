"""6 - Opportunity Scanner Agent: Market Surveillance & Opportunity Discovery Agent (cross-market)."""
from __future__ import annotations
from ..context import Ctx
from ..schemas import ScannerReport, ScanCandidate
from ..tools.indicators import atr
from .base import BaseAgent, dumps


def features(ctx) -> dict:
    """Deterministic anomaly features from the 1h and 15m frames."""
    f, sig = {}, []
    h1, m15 = ctx.data.frames.get("1h"), ctx.data.frames.get("15m")
    if h1 is not None and len(h1) > 70:
        a = atr(h1)
        ratio = float(a.iloc[-1] / a.rolling(50).mean().iloc[-1])
        f["atr_vs_50avg_1h"] = round(ratio, 2)
        if ratio < 0.75:
            sig.append("volatility compression (1h ATR < 75% of 50-bar avg)")
        hi20, lo20 = float(h1["high"].iloc[-21:-1].max()), float(h1["low"].iloc[-21:-1].min())
        c = float(h1["close"].iloc[-1])
        f["range20_1h"] = [round(lo20, 4), round(hi20, 4)]
        if c > hi20:
            sig.append("1h close above 20-bar high (breakout)")
        if c < lo20:
            sig.append("1h close below 20-bar low (breakdown)")
        last = h1.iloc[-1]
        if last["high"] > hi20 and last["close"] < hi20:
            sig.append("1h liquidity sweep above 20-bar high, closed back inside")
        if last["low"] < lo20 and last["close"] > lo20:
            sig.append("1h liquidity sweep below 20-bar low, closed back inside")
    for name, df in (("1h", h1), ("15m", m15)):
        if df is not None and len(df) > 30:
            vr = float(df["volume"].iloc[-1] / df["volume"].iloc[-21:-1].mean())
            f[f"volume_ratio_{name}"] = round(vr, 2)
            if vr > 2:
                sig.append(f"{name} volume spike x{vr:.1f}")
    if m15 is not None and len(m15) > 30:
        a15 = atr(m15)
        body = float(abs(m15["close"].iloc[-1] - m15["open"].iloc[-1]))
        if body > 2 * float(a15.iloc[-1]):
            sig.append("15m displacement candle (body > 2 ATR)")
    f["signals"] = sig
    return f


class ScannerAgent(BaseAgent):
    key, number, emoji = "scanner", 6, "🔎"
    name = "Market Surveillance & Opportunity Discovery Agent"
    schema = ScannerReport
    system_prompt = """You are the Market Surveillance & Opportunity Discovery Agent.
You receive deterministic anomaly features for each asset in the watchlist (volatility compression, volume spikes, range breakouts, liquidity sweeps, displacement candles). Rank the assets as candidates for the analytical pipeline: score 0-100 (how interesting/actionable right now), list the concrete signals, and a one-line comment. Include every watchlist symbol exactly once. Use only the provided features."""

    def user_prompt(self, ctx):
        return f"Watchlist features:\n{dumps(ctx.extra['features'])}"

    def heuristic(self, ctx):
        cands = []
        for sym, f in ctx.extra["features"].items():
            s = f.get("signals", [])
            cands.append(ScanCandidate(symbol=sym, score=min(100, 20 + 20 * len(s)), signals=s,
                                       comment="rule-based score: 20 + 20 per signal"))
        return ScannerReport(candidates=sorted(cands, key=lambda c: -c.score), market_notes="Heuristic scan (no LLM).")

    def scan(self, ctxs: dict, cfg, now, world, demo):
        pseudo = Ctx(symbol="WATCHLIST", inst=None, cfg=cfg, query="", demo=demo, equity=0, now=now, world=world)
        pseudo.extra["features"] = {s: features(c) for s, c in ctxs.items() if c.data is not None}
        rep = self.run(pseudo)
        return rep, pseudo.trace
