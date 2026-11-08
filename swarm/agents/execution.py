"""9 - Execution Agent: Algorithmic Execution Specialist. Only handles Inspector-approved trades."""
from __future__ import annotations
from ..schemas import OrderDecision
from ..tools.brokers import BUILDERS, submit
from .base import BaseAgent, dumps


class ExecutionAgent(BaseAgent):
    key, number, emoji = "execution", 9, "⚡"
    name = "Algorithmic Execution Specialist"
    schema = OrderDecision
    system_prompt = """You are the Algorithmic Execution Specialist. You receive ONLY risk-approved trades.
Choose the order type: LIMIT (resting order at a better price than the market: BUY below / SELL above), STOP (breakout entry: BUY above / SELL below), or MARKET (only if the entry is essentially at the current price, within ~0.15 ATR, and the spread is small). Consider spread and volatility. Give a one-line reason. The code builds and validates the actual broker payload."""

    def user_prompt(self, ctx):
        p = ctx.reports["orchestrator"]
        return (f"{ctx.symbol} {p.direction}: entry {p.entry}, SL {p.stop_loss}, TP {p.take_profit}. Current price {ctx.data.last_price}, "
                f"spread {ctx.data.spread}, ATR(1h) {ctx.data.atr.get('1h')}.")

    @staticmethod
    def _valid(order_type, direction, entry, price):
        if order_type == "MARKET":
            return True
        if order_type == "LIMIT":
            return entry <= price if direction == "LONG" else entry >= price
        return entry >= price if direction == "LONG" else entry <= price  # STOP

    def heuristic(self, ctx):
        p, price = ctx.reports["orchestrator"], ctx.data.last_price
        atr1 = ctx.data.atr.get("1h") or 0
        if atr1 and abs(price - p.entry) <= 0.15 * atr1:
            return OrderDecision(order_type="MARKET", reason="entry within 0.15 ATR of price")
        better = (p.entry < price) if p.direction == "LONG" else (p.entry > price)
        return OrderDecision(order_type="LIMIT" if better else "STOP", reason="resting order at planned entry")

    def build(self, ctx, allow_live: bool) -> dict:
        p, v, price = ctx.reports["orchestrator"], ctx.verdict, ctx.data.last_price
        dec = super().run(ctx)
        note = dec.reason
        if not self._valid(dec.order_type, p.direction, p.entry, price):
            fixed = self.heuristic(ctx)
            note = f"LLM order type {dec.order_type} invalid for entry/price -> corrected to {fixed.order_type}"
            dec = fixed
        mode = ctx.cfg.execution.mode
        payloads = {b: BUILDERS[b](ctx.inst, p, v.lots, dec.order_type, ctx.cfg.execution.magic_number) for b in ctx.inst.brokers}
        primary = ctx.inst.brokers[0]
        return {"order_type": dec.order_type, "reason": note, "lots": v.lots, "mode": mode, "primary_broker": primary,
                "payloads": payloads, "submission": submit(payloads[primary], mode, allow_live)}
