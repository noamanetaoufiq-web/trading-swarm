#!/usr/bin/env python3
"""Trading Swarm CLI.

  python main.py analyze XAUUSD BTCUSD NAS100          # real data + Claude agents (needs ANTHROPIC_API_KEY)
  python main.py analyze XAUUSD BTCUSD NAS100 --demo   # offline: synthetic data + rule-based agents
  python main.py review                                # resolve paper trades + Critic learning loop
  python main.py journal                               # stats
  python main.py agents                                # list the 12 agents
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
from pathlib import Path

from swarm.config import load_config, ROOT
from swarm.llm import LLM

ALIASES = {"GOLD": "XAUUSD", "XAU": "XAUUSD", "BTC": "BTCUSD", "BITCOIN": "BTCUSD", "NASDAQ": "NAS100",
           "NDX": "NAS100", "NQ": "NAS100", "US100": "NAS100", "USTEC": "NAS100"}


def norm(sym, cfg):
    s = ALIASES.get(sym.upper(), sym.upper())
    if s not in cfg.instruments:
        sys.exit(f"Unknown symbol '{sym}'. Configured: {', '.join(cfg.instruments)} (add more in config.yaml)")
    return s


def main():
    ap = argparse.ArgumentParser(description="12-agent trading swarm")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("symbols", nargs="+")
    a.add_argument("--query", default="")
    a.add_argument("--demo", action="store_true", help="synthetic data + rule-based agents (no API key, no internet)")
    a.add_argument("--no-llm", action="store_true", help="real market data but rule-based agents (no API cost)")
    a.add_argument("--equity", type=float, default=None)
    a.add_argument("--live", action="store_true", help="allow live order sending (also needs execution.mode=live and LIVE_TRADING=YES)")
    a.add_argument("--json", action="store_true", help="also save the full run as JSON")
    r = sub.add_parser("review")
    r.add_argument("--demo", action="store_true")
    r.add_argument("--no-llm", action="store_true")
    j = sub.add_parser("journal")
    j.add_argument("--demo", action="store_true")
    b = sub.add_parser("backtest", help="run/refresh the backtest + Monte Carlo + optimisation for symbols")
    b.add_argument("symbols", nargs="+")
    b.add_argument("--demo", action="store_true")
    sv = sub.add_parser("serve", help="start the dashboard (http://127.0.0.1:8000)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--allow-live", action="store_true", help="allow the dashboard to send REAL orders (still needs Real account + per-run confirmation)")
    sub.add_parser("agents")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)

    if args.cmd == "agents":
        from swarm.agents import ALL
        for cls in sorted(ALL, key=lambda c: c.number):
            print(f"{cls.number:>2}. {cls.emoji} {cls.key:<15} {cls.name}")
        return

    if args.cmd == "serve":
        import server
        server.run(args.host, args.port, args.allow_live, args.config)
        return

    from rich.console import Console
    from rich.markdown import Markdown
    console = Console()

    if args.cmd == "journal":
        from swarm.journal import Journal
        jr = Journal(cfg.root, args.demo)
        console.print(jr.stats())
        console.print(jr.recent_lessons(20) or "(no lessons yet)")
        return

    if args.cmd == "backtest":
        from swarm.tools.backtest import compute_backtest
        for sym in [norm(x, cfg) for x in args.symbols]:
            out = compute_backtest(cfg, sym, args.demo, progress=console.print)
            console.print({"symbol": sym, "metrics": out["metrics"], "mc": {k: out["mc"][k] for k in ("prob_loss_pct", "max_dd_pct", "prob_ruin_pct")},
                           "optimisation_robust": out["optimization"]["robust"], "selected": out["optimization"]["selected"]})
        return

    llm = LLM(cfg, mock=args.demo or getattr(args, "no_llm", False))

    if args.cmd == "review":
        from swarm.review import review_trades
        out = review_trades(cfg, llm, demo=args.demo, progress=console.print)
        console.print(out["stats"])
        return

    from swarm.pipeline import run_analysis
    from swarm.report import render_report
    symbols = [norm(s, cfg) for s in args.symbols]
    if args.live and cfg.execution.mode != "live":
        console.print("[yellow]--live ignored: execution.mode is 'paper' in config.yaml[/yellow]")
    run = run_analysis(cfg, llm, symbols, query=args.query, demo=args.demo, equity=args.equity,
                       allow_live=args.live, progress=console.print)
    md = render_report(run)
    out_dir = Path(ROOT) / "reports"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"report_{run['stamp']}{'_demo' if args.demo else ''}.md"
    path.write_text(md, encoding="utf-8")
    console.print(Markdown(md))
    console.print(f"\n[green]Report saved:[/green] {path}")
    if args.json:
        jp = path.with_suffix(".json")
        slim = {s: {k: (v.model_dump() if hasattr(v, "model_dump") else getattr(v, "to_dict", lambda: v)()) for k, v in c.reports.items()}
                for s, c in run["ctxs"].items()}
        jp.write_text(json.dumps({"items": run["items"], "reports": slim}, default=str, indent=2), encoding="utf-8")
        console.print(f"[green]JSON saved:[/green] {jp}")


if __name__ == "__main__":
    main()
