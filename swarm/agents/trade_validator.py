"""12 - Trade Validator Agent: Per-Trade Monte Carlo Stress-Test Engine ("is THIS trade any good?").

Sits AFTER the Backtest gate (agent 11) and BEFORE Execution.  Only trades already approved by the Inspector (7) and the
Backtester (11) reach it.  It re-simulates the exact confirmed trade (entry / SL / TP of the final plan) many times:

   ladder 10 -> 100 -> 1000 -> 5000 simulated futures  (all shown in the dashboard, for every trade, and saved in the journal)

and answers three questions with code:
  1. Could this entry/SL/TP win by luck?  (random-walk lens = break-even baseline, costs and slippage included)
  2. Does the strategy evidence from agent 11 beat that baseline AND the break-even win rate, with statistical confidence (95% CI)?
  3. Does the edge survive a cost stress (spread / slippage x cost_stress_mult)?
Verdict:  GOOD (full risk)  |  MARGINAL (reduced risk, or refused when min_verdict=GOOD / min lot makes the reduced risk impossible)  |  BAD (refused).
The LLM part can only VETO.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict

from ..schemas import ValidatorReview
from ..tools.backtest import as_cost_model, cost_model, fetch_history, load_price_cache, save_price_cache
from ..tools.risk import position_size, rr_ratio
from ..tools.trade_sim import run_ladder
from .base import BaseAgent, dumps

ORDER = {"BAD": 0, "MARGINAL": 1, "GOOD": 2}


@dataclass
class ValidationVerdict:
    verdict: str = "SKIPPED"             # GOOD | MARGINAL | BAD | SKIPPED | ERROR
    approved: bool = False               # may this trade go on to Execution?
    checks: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    sim: dict = field(default_factory=dict)          # run_ladder() output (both lenses, every ladder step, histogram, break-evens)
    evidence: dict = field(default_factory=dict)     # what agent 11 measured for the same setup (win rate, CI, n, expectancy)
    stress: dict = field(default_factory=dict)
    risk_mult: float = 1.0
    sizing_note: str = ""
    sizing: dict = field(default_factory=dict)
    llm_review: dict | None = None

    def to_dict(self):
        return asdict(self)


class TradeValidatorAgent(BaseAgent):
    key, number, emoji = "trade_validator", 12, "🎲"
    name = "Trade Validator · Per-Trade Monte Carlo Stress-Test Engine"
    schema = ValidatorReview
    system_prompt = """You are the Trade Validator, the last statistical check on ONE confirmed trade before an order is built.
Code has ALREADY simulated this exact entry/SL/TP on thousands of synthetic price paths (random-walk lens = what luck alone would do, costs included) at 10/100/1000/5000 runs, compared the strategy's historical win rate (with its 95% CI) against that luck baseline and against the break-even win rate, and stress-tested the costs. The verdict (GOOD / MARGINAL / BAD) is decided by code.
Your job is a sceptical review, VETO ONLY - you cannot rescue a BAD verdict nor upgrade a MARGINAL one:
- is the evidence sample tiny or clustered (look at n and the CI width)? is the simulated fill probability fragile? is the time-out share large (the trade drifts without resolving)?
- does the geometry look unrealistic for the instrument (stop inside normal noise, target far beyond typical daily range)?
- any contradiction between the simulation, the strategy evidence and the thesis?
Set veto=true only for a concrete reason and list the concerns."""

    # ------------------------------------------------------------ data
    def _px(self, ctx):
        px = load_price_cache(ctx.cfg.root, ctx.symbol, ctx.demo)
        if px is None:
            df = fetch_history(ctx.symbol, ctx.inst, int(ctx.cfg.backtest.history_bars), ctx.demo)
            save_price_cache(ctx.cfg.root, ctx.symbol, ctx.demo, df)
            px = load_price_cache(ctx.cfg.root, ctx.symbol, ctx.demo)
        return px

    # ------------------------------------------------------------ pure maths (no LLM, no I/O) - unit-testable
    def evaluate(self, cfg, inst, plan: dict, price: float, atr_now, px: dict, evidence: dict | None, cm: dict | None = None) -> ValidationVerdict:
        V = cfg.validator
        bt = cfg.backtest
        v = ValidationVerdict(plan=dict(plan), evidence=dict(evidence or {}))
        cm = cm or cost_model(cfg, inst)
        sim = run_ladder(px, plan["side"], plan["entry"], plan["sl"], plan["tp"], price, cm, atr_now, list(V.runs_ladder),
                         int(bt.entry_expiry_bars), int(bt.max_hold_bars), int(V.block_bars), bool(V.vol_match), int(V.seed))
        v.sim = sim
        nl = sim["lenses"]["neutral"]["ladder"]
        top = nl[-1]
        ev0 = v.evidence
        # break-even on the SAME footing as the win rate (win = R > 0 after costs): with the strategy's realised average win / loss in R,
        # w* = |avg loss| / (avg win + |avg loss|).  Without evidence fall back to the geometric TP/SL break-even of this exact trade.
        aw, al = (ev0 or {}).get("avg_win_r"), (ev0 or {}).get("avg_loss_r")
        if aw and al and aw > 0 and al < 0:
            be_adj, basis = round(100.0 * abs(al) / (aw + abs(al)), 1), "realised avg win/loss of the strategy sample"
        else:
            be_adj, basis = sim["breakeven_cost_adj_pct"], "geometry of this trade (R:R, costs)"
        sim["breakeven_used_pct"], sim["breakeven_basis"] = be_adj, basis

        def add(name, passed, detail, warn=False, tier="soft"):
            v.checks.append({"name": name, "passed": bool(passed), "detail": detail, "warn": bool(warn), "tier": tier})

        # 1 - simulation health
        by_n = {s["runs"]: s for s in nl}
        if 1000 in by_n and 5000 in by_n and by_n[1000]["win_rate_pct"] is not None and by_n[5000]["win_rate_pct"] is not None:
            d = abs(by_n[1000]["win_rate_pct"] - by_n[5000]["win_rate_pct"])
            add("sim_converged", d <= float(V.converge_tol_pp), f"win rate {by_n[1000]['win_rate_pct']}% @1000 runs vs {by_n[5000]['win_rate_pct']}% @5000 (|diff| {d:.1f}pp, tol {V.converge_tol_pp}pp)", tier="hard")
        else:
            add("sim_converged", True, "ladder has no 1000/5000 pair - convergence not assessed", warn=True)
        add("fill_probability", top["fill_prob_pct"] >= float(V.min_fill_prob_pct),
            f"entry fills in {top['fill_prob_pct']}% of {top['runs']} simulated futures within {bt.entry_expiry_bars} bars (min {V.min_fill_prob_pct}%)", tier="hard")
        wr0 = top["win_rate_pct"]
        if wr0 is not None:
            tp0, geo = top.get("tp_rate_pct"), sim["breakeven_cost_adj_pct"]
            dev = (tp0 if tp0 is not None else wr0) - geo
            add("baseline_sane", abs(dev) <= 6.0, f"luck-only TP-first rate {tp0}% vs geometric break-even {geo}% (R:R {sim['rr']}); big gaps mean time-outs dominate", warn=abs(dev) > 6.0)
        # 2 - strategy evidence from agent 11 vs luck and break-even
        ev = v.evidence
        have = bool(ev) and ev.get("n", 0) > 0 and ev.get("win_rate_pct") is not None
        lo = (ev.get("win_rate_ci95") or [0, 0])[0] if have else None
        pt = ev.get("win_rate_pct") if have else None
        margin = float(V.edge_margin_pp)
        add("strategy_evidence", have, f"agent 11 measured n={ev.get('n', 0)} comparable trades" if have else "no historical strategy evidence available (backtest gate disabled or failed)", tier="info")
        if have and wr0 is not None:
            add("beats_random_point", pt >= wr0 + margin, f"strategy win rate {pt}% vs luck baseline {wr0}% (both: win = R > 0 after costs) (+{margin}pp)", tier="hard")
            add("beats_breakeven_point", pt >= be_adj, f"strategy win rate {pt}% vs break-even {be_adj}% ({basis})", tier="hard")
            add("beats_random_95ci", lo > wr0 + margin, f"lower 95% bound {lo}% vs luck baseline {wr0}%", tier="confidence")
            add("beats_breakeven_95ci", lo >= be_adj, f"lower 95% bound {lo}% vs break-even {be_adj}%", tier="confidence")
            se = ev.get("expectancy_se_r")
            if se is not None:
                elo = float(ev.get("expectancy_r", 0.0)) - 1.96 * float(se)
                add("edge_ci_positive", elo > 0, f"expectancy {ev.get('expectancy_r')}R, 95% lower bound {elo:.3f}R (must be > 0)", tier="confidence")
            cost_r = top.get("mean_cost_r") or 0.0
            stress_exp = float(ev.get("expectancy_r", 0.0)) - (float(V.cost_stress_mult) - 1.0) * cost_r
            v.stress = {"cost_mult": float(V.cost_stress_mult), "expectancy_r": round(float(ev.get("expectancy_r", 0.0)), 3), "stressed_expectancy_r": round(stress_exp, 3), "mean_cost_r": cost_r}
            add("edge_after_cost_stress", stress_exp >= float(V.min_stress_expectancy_r),
                f"expectancy {ev.get('expectancy_r')}R -> {stress_exp:.3f}R with costs x{V.cost_stress_mult} (min {V.min_stress_expectancy_r}R)", tier="hard")
        hard_fail = [c for c in v.checks if c["tier"] == "hard" and not c["passed"]]
        conf_fail = [c for c in v.checks if c["tier"] == "confidence" and not c["passed"]]
        v.reasons = [f"{c['name']}: {c['detail']}" for c in hard_fail]
        if hard_fail:
            v.verdict = "BAD"
        elif conf_fail or not have:
            v.verdict = "MARGINAL"
            v.reasons += [f"{c['name']}: {c['detail']}" for c in conf_fail] or ["no strategy evidence to compare with"]
        else:
            v.verdict = "GOOD"
        return v

    # ------------------------------------------------------------ LLM review (veto only)
    def user_prompt(self, ctx):
        return f"Instrument {ctx.symbol}. Per-trade validation payload:\n{dumps(ctx.extra['val_payload'])}"

    def heuristic(self, ctx):
        e = ctx.extra["val_payload"]["evidence"]
        n = e.get("n", 0) if e else 0
        return ValidatorReview(veto=False, concerns=[f"only {n} comparable historical trades"] if n < 50 else [], summary="Rule-based review (no LLM).")

    # ------------------------------------------------------------ the step used by the pipeline
    def validate(self, ctx, bv, progress=print) -> ValidationVerdict:
        cfg, V = ctx.cfg, ctx.cfg.validator
        p = ctx.reports["orchestrator"]
        plan = {"side": p.direction, "entry": p.entry, "sl": p.stop_loss, "tp": p.take_profit}
        try:
            px = self._px(ctx)
            ev = None
            if bv is not None and bv.metrics and bv.metrics.get("n"):
                ev = {k: bv.metrics.get(k) for k in ("n", "win_rate_pct", "win_rate_ci95", "expectancy_r", "expectancy_se_r", "profit_factor", "realized_rr", "avg_win_r", "avg_loss_r")}
                ev["basis"] = bv.sample_basis
            atr = ctx.smc.get("1h", {}).get("atr") or ctx.data.atr.get("1h")
            v = self.evaluate(cfg, ctx.inst, plan, float(ctx.data.last_price), atr, px, ev)
        except Exception as e:  # noqa: BLE001
            return ValidationVerdict(verdict="ERROR", approved=False, plan=plan, reasons=[f"validation failed: {type(e).__name__}: {e}"])
        # LLM sceptic: may only veto
        veto = False
        if V.llm_review and v.verdict in ("GOOD", "MARGINAL"):
            top = v.sim["lenses"]["neutral"]["ladder"][-1]
            ctx.extra["val_payload"] = {"verdict": v.verdict, "plan": plan, "evidence": v.evidence, "stress": v.stress, "breakeven_pct": v.sim["breakeven_pct"],
                                        "breakeven_cost_adj_pct": v.sim["breakeven_cost_adj_pct"], "ladder": v.sim["lenses"]["neutral"]["ladder"],
                                        "outcome_mix": v.sim["lenses"]["neutral"]["outcome_mix"], "checks": v.checks, "top_step": top}
            rev = super().run(ctx)
            v.llm_review = rev.model_dump()
            veto = bool(rev.veto)
            if veto:
                v.verdict = "BAD"
                v.reasons.append(f"llm_review_veto: {'; '.join(rev.concerns[:3]) or rev.summary}")
        # decision + sizing
        if v.verdict == "BAD":
            v.approved = False
        elif v.verdict == "GOOD":
            v.approved, v.risk_mult = True, 1.0
        else:  # MARGINAL
            if ORDER[str(V.min_verdict)] > ORDER["MARGINAL"]:
                v.approved = False
                v.reasons.append(f"verdict MARGINAL but validator.min_verdict is {V.min_verdict}")
            else:
                v.risk_mult = float(V.marginal_risk_mult)
                size = position_size(ctx.equity, cfg.account.risk_per_trade_pct * v.risk_mult, p.entry, p.stop_loss, ctx.inst, cost_model(cfg, ctx.inst))
                v.sizing_note = f"MARGINAL -> risk x{v.risk_mult}: {size['lots']} lots, ${size['risk_usd']} ({size['risk_pct']}%) {size['note']}"
                if size["lots"] > 0:
                    v.approved = True
                    v.sizing = size
                else:
                    v.approved = False
                    v.reasons.append("MARGINAL trade needs reduced risk but the minimum lot would exceed it: " + size["note"])
        return v
