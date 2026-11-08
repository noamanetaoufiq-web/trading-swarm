"""4 - Technical Analyst Agent: Master Technical Analyst & Price Action Expert (institutional SMC)."""
from __future__ import annotations
from ..schemas import TechnicalReport
from ..strategy import Params, smc_plan
from ..tools.smc import analyze_tf
from .base import BaseAgent, dumps

SMC_TFS = ("1d", "4h", "1h", "15m")


class TechnicalAgent(BaseAgent):
    key, number, emoji = "technical", 4, "📈"
    name = "Master Technical Analyst & Price Action Expert"
    schema = TechnicalReport
    system_prompt = """You are the Master Technical Analyst & Price Action Expert using institutional Smart Money Concepts.
You receive PRE-COMPUTED, rule-based SMC data per timeframe (1d, 4h, 1h, 15m): market structure with BOS/CHoCH, order blocks, fair value gaps, equal highs/lows liquidity pools, liquidity sweeps, premium/discount + OTE zones, RSI (+divergence), EMA stack, ATR.
Method: (1) HTF bias from 1d/4h structure; (2) 1h for the setup zone; (3) 15m for confirmation (CHoCH/BOS inside the zone, sweep of liquidity); (4) prefer longs from discount/OTE and shorts from premium/OTE, aligned with HTF bias; counter-trend only with a clear CHoCH on 4h and high-confidence reasons.
Define ONE concrete plan or none (also set zone_type = OB | FVG | OTE | OTHER for the zone your entry is based on): entry (inside entry_zone), stop_loss beyond the invalidation (use ATR buffer), take_profit at the next liquidity pool / swing / opposing zone. Use ONLY levels present in the data. Minimum reward:risk is 2.0 - if the plan cannot reach 1:2 at a logical target, set setup=NONE.
The entry must be reachable (within the stated max ATR distance of the current price). confidence is 0-100 and must reflect confluence count, HTF alignment and data quality. State precisely what invalidates the idea."""

    def prepare(self, ctx):
        ctx.smc = {tf: analyze_tf(ctx.data.frames[tf]) for tf in SMC_TFS if tf in ctx.data.frames}

    def user_prompt(self, ctx):
        r = ctx.cfg.risk
        ms = ctx.extra["market_summary"]
        return (f"Instrument {ctx.symbol} ({ctx.inst.asset_class}). Last price {ms['last_price']}, spread {ms['spread']}.\n"
                f"Key daily levels: {dumps(ms['levels'])}\n"
                f"Constraints: min R:R {r.min_rr}; SL distance must be {r.sl_atr_min}-{r.sl_atr_max} x ATR(1h)={ctx.smc.get('1h', {}).get('atr')}; "
                f"entry within {r.max_entry_distance_atr} x ATR(1h) of price.\n"
                f"SMC data by timeframe:\n{dumps(ctx.smc)}\n"
                f"Lessons from the Critic (apply if relevant):\n{ctx.lessons or '(none yet)'}")

    # ---------------------------------------------------------------- deterministic fallback (shared strategy)
    def heuristic(self, ctx):
        plan = smc_plan(ctx.smc, ctx.data.last_price, Params.from_cfg(ctx.cfg.get("strategy")))
        if plan["side"] == "NONE":
            return TechnicalReport(bias=plan["bias"], htf_bias=plan["bias"], setup="NONE", confidence=plan["confidence"],
                                   reasoning=plan["reasons"], invalidation="n/a")
        return TechnicalReport(bias=plan["bias"], htf_bias=plan["bias"], setup=plan["side"], entry=plan["entry"],
                               stop_loss=plan["sl"], take_profit=plan["tp"], entry_zone=plan["zone"], zone_type=plan["zone_kind"],
                               confidence=plan["confidence"], key_levels=[f"zone {plan['zone'][0]}-{plan['zone'][1]}"],
                               reasoning=plan["reasons"], invalidation=f"close beyond {plan['sl']} (zone invalidated)")

    def run(self, ctx):
        self.prepare(ctx)
        return super().run(ctx)
