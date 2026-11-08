"""11 - Backtesting & Monte Carlo Simulation / Strategy Optimization Agent (the 'probability gate').

Sits between the Inspector and the Execution agent:
  Inspector approves -> Backtester validates the setup statistically -> only PASS reaches Execution.

Deterministic part (code, decides):
  * replays the rule-based SMC strategy on ~4000 historical 1h bars with NO look-ahead (cached per symbol),
  * measures win rate, realised R:R, expectancy, profit factor, max drawdown (after spread costs),
  * Monte Carlo bootstrap (default 1000 paths x 100 trades): P(loss), drawdown distribution, risk of ruin,
  * gate thresholds from config.yaml; if the gate fails, a walk-forward optimised parameter set is tried
    (optimised in-sample, must also work out-of-sample) -> verdict PASS_REOPTIMIZED, else REJECT.
LLM part (can only VETO): overfitting / regime / sample-quality concerns.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict

from ..schemas import BacktestReview
from ..strategy import Params, smc_plan
from ..tools.backtest import compute_backtest, gate_checks, load_cached, select_sample
from ..tools.risk import rr_ratio
from .base import BaseAgent, dumps


@dataclass
class BacktestVerdict:
    verdict: str = "SKIPPED"              # PASS | PASS_REOPTIMIZED | REJECT | INSUFFICIENT_DATA | SKIPPED | ERROR
    checks: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    mc: dict = field(default_factory=dict)
    sample_basis: str = ""
    n_trades: int = 0
    reasons: list = field(default_factory=list)
    suggested_params: dict | None = None
    suggested_plan: dict | None = None
    original_plan: dict | None = None
    llm_review: dict | None = None
    cache_info: dict = field(default_factory=dict)
    optimization_note: str = ""

    def to_dict(self):
        return asdict(self)

    @property
    def passed(self) -> bool:
        return self.verdict in ("PASS", "PASS_REOPTIMIZED")


class BacktesterAgent(BaseAgent):
    key, number, emoji = "backtester", 11, "🧪"
    name = "Backtesting & Monte Carlo Simulation / Strategy Optimization Engine"
    schema = BacktestReview
    system_prompt = """You are the Backtesting & Monte Carlo Simulation / Strategy Optimization Engine, the last statistical gate before money is risked.
Code has ALREADY replayed the strategy on historical data and applied the numeric gate (win rate vs break-even, expectancy, profit factor, Monte Carlo loss/drawdown/ruin probabilities, walk-forward optimisation). You receive those numbers.
Your job is a sceptical review, VETO ONLY: you cannot rescue a failed gate.
- Is the sample too small or too clustered for the stated win rate (look at the 95% confidence interval)?
- Are there signs of overfitting (optimised parameters with a big in-sample vs out-of-sample gap, parameters at the edge of the grid)?
- Is the historical period unrepresentative for the current regime (e.g. only trending data, volatility far from now)?
- Does the replayed strategy actually resemble the proposed setup? (The discretionary LLM plan is not replayed bar-by-bar; the rule-based strategy family and the setup geometry are.)
Set veto=true only for a concrete reason. overfit_risk is 0-100. parameter_ideas: concrete, optional ideas (advisory only)."""

    # ---------------------------------------------------------------- history / cache
    def ensure_history(self, ctx, refresh: bool = False, progress=print) -> dict:
        cfg = ctx.cfg
        cache = None if refresh else load_cached(cfg, ctx.symbol, ctx.demo)
        if cache is None:
            progress(f"  [11] no fresh backtest cache for {ctx.symbol} -> computing (cached afterwards for {cfg.backtest.cache_hours}h)")
            cache = compute_backtest(cfg, ctx.symbol, ctx.demo, progress)
        return cache

    # ---------------------------------------------------------------- LLM review (veto only)
    def user_prompt(self, ctx):
        e = ctx.extra["bt_payload"]
        return f"Instrument {ctx.symbol}. Backtest gate payload:\n{dumps(e)}"

    def heuristic(self, ctx):
        e = ctx.extra["bt_payload"]
        n = e["n_trades"]
        opt = e.get("optimization") or {}
        gap = None
        row = opt.get("selected_row")
        if row and row["is"].get("expectancy_r") is not None and row["val"].get("expectancy_r") is not None:
            gap = row["is"]["expectancy_r"] - row["val"]["expectancy_r"]
        risk = 20 + (25 if n < 50 else 0) + (25 if gap is not None and gap > 0.4 else 0)
        return BacktestReview(veto=False, overfit_risk=min(100, risk),
                              concerns=[f"only {n} trades in the sample"] if n < 50 else [], summary="Rule-based review (no LLM).")

    # ---------------------------------------------------------------- the gate
    def gate(self, ctx, refresh: bool = False, progress=print) -> BacktestVerdict:
        cfg, bt = ctx.cfg, ctx.cfg.backtest
        p = ctx.reports["orchestrator"]
        v = BacktestVerdict(original_plan={"direction": p.direction, "entry": p.entry, "sl": p.stop_loss, "tp": p.take_profit})
        try:
            cache = self.ensure_history(ctx, refresh, progress)
        except Exception as e:  # noqa: BLE001
            v.verdict, v.reasons = "ERROR", [f"backtest failed: {type(e).__name__}: {e}"]
            return v
        v.cache_info = {k: cache[k] for k in ("created_at", "bars", "bars_from", "bars_to", "evaluations", "signals", "unfilled", "compute_seconds")}
        plan_rr = rr_ratio(p.direction, p.entry, p.stop_loss, p.take_profit) or float(cfg.risk.min_rr)
        risk_pct = float(cfg.account.risk_per_trade_pct)
        sample, basis = select_sample(cache["trades"], p.direction, plan_rr, bt)
        checks, m, mc = gate_checks(sample, plan_rr, bt, risk_pct)
        v.checks, v.metrics, v.mc, v.sample_basis, v.n_trades = checks, m, mc, basis, m.get("n", 0)
        failed = [c for c in checks if not c["passed"]]
        opt = cache.get("optimization") or {}
        if not failed:
            v.verdict = "PASS"
        else:
            v.reasons = [f"{c['name']}: {c['detail']}" for c in failed]
            insufficient = any(c["name"] == "sample_size" for c in failed)
            v.verdict = "INSUFFICIENT_DATA" if insufficient else "REJECT"
            if opt.get("robust") and opt.get("selected") and not insufficient:
                newp = Params(**opt["selected"])
                plan = smc_plan(ctx.smc, ctx.data.last_price, newp)
                if plan["side"] == p.direction and rr_ratio(p.direction, plan["entry"], plan["sl"], plan["tp"]):
                    # the gate for optimised parameters reads ONLY the final-test slice (never used to choose parameters)
                    s2, b2 = select_sample(opt["final_trades"], p.direction, plan["rr"], bt)
                    c2, m2, mc2 = gate_checks(s2, plan["rr"], bt, risk_pct, min_n=int(bt.final_min_trades))
                    if all(c["passed"] for c in c2):
                        v.verdict, v.suggested_params, v.suggested_plan = "PASS_REOPTIMIZED", opt["selected"], plan
                        v.checks, v.metrics, v.mc, v.sample_basis, v.n_trades = c2, m2, mc2, "optimised params, FINAL-TEST slice only: " + b2, m2.get("n", 0)
                        v.optimization_note = ("original plan failed the gate; walk-forward optimised parameters "
                                               f"(validation {opt['selected_row']['val']['expectancy_r']}R over {opt['selected_row']['val']['n']} trades; "
                                               f"untouched final test {opt['final'].get('expectancy_r')}R over {opt['final'].get('n')} trades) "
                                               "pass the gate -> plan re-derived with those parameters")
                    else:
                        v.optimization_note = "optimised parameters exist but still fail the gate for this setup"
                else:
                    v.optimization_note = "optimised parameters produce no matching setup right now"
            elif not insufficient:
                v.optimization_note = "no parameter set that is also profitable on the validation segment -> setup refused"
        # ---- LLM sceptical review (veto only) on anything that passed
        if v.passed and bt.llm_review:
            ctx.extra["bt_payload"] = {"verdict": v.verdict, "metrics": v.metrics, "mc": {k: v.mc[k] for k in v.mc if k not in ("fan", "dd_hist", "fan_steps")},
                                       "sample_basis": v.sample_basis, "n_trades": v.n_trades, "history": v.cache_info,
                                       "plan": v.suggested_plan or v.original_plan,
                                       "optimization": {"selected_row": opt.get("selected_row"), "robust": opt.get("robust"), "grid_size": opt.get("grid_size"), "final": opt.get("final")}}
            rev = super().run(ctx)
            v.llm_review = rev.model_dump()
            if rev.veto:
                v.verdict = "REJECT"
                v.reasons.append(f"llm_review_veto: {'; '.join(rev.concerns[:3]) or rev.summary}")
        return v
