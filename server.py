"""Local dashboard server (FastAPI).  python main.py serve   ->  http://127.0.0.1:8000/?token=...

Binds to localhost by default. All /api/* calls require the start-up token (header X-Token or ?token=).
"""
from __future__ import annotations
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from swarm.agents import ALL
from swarm.config import ROOT, load_config
from swarm.journal import Journal
from swarm.llm import LLM
from swarm.serialize import list_runs, load_run, run_to_dict, save_run
from swarm.tools import accounts as acc

DASH = Path(__file__).parent / "dashboard"
_ANSI = re.compile(r"\[/?[a-z ]+\]|\x1b\[[0-9;]*m")


def make_app(root: str | None = None, config_path: str | None = None, allow_live: bool = False, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Trading Swarm Dashboard", docs_url=None, redoc_url=None)
    app.state.token = token or secrets.token_urlsafe(16)
    app.state.allow_live = allow_live
    jobs: dict = {}
    lock = threading.Lock()
    root_dir = str(root or ROOT)

    def cfg_for(acct=None, live=False):
        cfg = load_config(config_path)
        cfg.root = root_dir
        acct = acct if acct is not None else acc.load_account(root_dir)
        if acct:
            # only the paper simulator has a typed starting capital; a linked broker account is READ at run time (resolve_account)
            if acct.get("broker") == "paper" and acct.get("equity"):
                cfg.account.equity = float(acct["equity"])
            for k in ("risk_per_trade_pct", "max_total_open_risk_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_equity_drawdown_pct"):
                if acct.get(k) is not None:
                    cfg.account[k] = float(acct[k])
        cfg.execution.mode = "live" if live else "paper"
        return cfg

    def auth(request: Request):
        t = request.headers.get("x-token") or request.query_params.get("token")
        if t != app.state.token:
            raise HTTPException(401, "bad or missing token (open the URL printed by `main.py serve`)")

    def start_job(kind, fn):
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "another job is running - wait for it to finish")
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "kind": kind, "status": "running", "log": [], "result": None, "error": None, "started": time.time()}
        jobs[jid] = job

        def progress(*a, **k):
            job["log"].append(_ANSI.sub("", " ".join(str(x) for x in a)))

        def work():
            try:
                job["result"] = fn(progress)
                job["status"] = "done"
            except Exception as e:  # noqa: BLE001
                job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
                progress(f"ERROR: {job['error']}")
            finally:
                job["finished"] = time.time()
                lock.release()

        threading.Thread(target=work, daemon=True).start()
        return {"job_id": jid}

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return FileResponse(DASH / "index.html")

    app.mount("/static", StaticFiles(directory=str(DASH)), name="static")
    api = [Depends(auth)]

    def feasibility(cfg, equity_usd):
        """Can this account size trade each instrument at all?  min lot x (min stop = sl_atr_min x typical ATR) vs the per-trade risk budget."""
        out = []
        for sym, i in cfg.instruments.items():
            atr = float(i.get("typical_atr_1h") or 0)
            min_stop = cfg.risk.sl_atr_min * atr
            need = float(i.min_lot) * float(i.usd_per_point_per_lot) * min_stop
            budget = equity_usd * float(cfg.account.risk_per_trade_pct) / 100.0 if equity_usd else None
            need_pct = (need / equity_usd * 100) if equity_usd else None
            out.append({"symbol": sym, "min_lot": i.min_lot, "typical_atr_1h": atr, "min_stop": round(min_stop, 3), "risk_at_min_lot_usd": round(need, 2),
                        "risk_budget_usd": None if budget is None else round(budget, 2), "min_risk_pct_needed": None if need_pct is None else round(need_pct, 2),
                        "tradable": None if budget is None else bool(need <= budget + 1e-9)})
        return out

    # ------------------------------------------------------------------ state / account
    @app.get("/api/state", dependencies=api)
    def state():
        cfg = cfg_for()
        acct = acc.load_account(root_dir)
        j = Journal(root_dir, False)
        jd = Journal(root_dir, True)
        astate = None
        if acct is not None:
            astate = acc.last_state(acct.get("label")) if acct.get("broker") != "paper" else acc.resolve_account(root_dir, acct, cfg, j, record=False)
        eq_usd = (astate or {}).get("equity_usd")
        if astate:
            acc.apply_broker_specs(cfg, astate)
        return {
            "account_state": astate, "feasibility": feasibility(cfg, eq_usd),
            "validator_cfg": dict(cfg.validator), "costs_cfg": dict(cfg.get("costs", {}) or {}),
            "configured": acct is not None, "account": acct,
            "has_credentials": {b: acc.has_credentials(b) for b in acc.BROKERS},
            "allow_live": app.state.allow_live, "brokers": acc.BROKERS,
            "equity": eq_usd,
            "instruments": [{"symbol": s, "asset_class": i.asset_class, "brokers": i.brokers, "feed": i.get("feed", "yahoo"), "feed_is_proxy": bool(i.get("feed_is_proxy"))} for s, i in cfg.instruments.items()],
            "risk": dict(cfg.risk), "account_cfg": dict(cfg.account),
            "backtest_cfg": {k: v for k, v in cfg.backtest.items()},
            "agents": [{"number": c.number, "key": c.key, "name": c.name, "emoji": c.emoji} for c in sorted(ALL, key=lambda c: c.number)],
            "journal_stats": j.stats(), "journal_stats_demo": jd.stats(),
            "exposure": {"open_risk_pct": j.open_risk_pct(), "today_pnl_pct": j.today_pnl_pct()},
            "llm_ready": bool(os.getenv("ANTHROPIC_API_KEY")), "runs": list_runs(root_dir),
        }

    @app.post("/api/account", dependencies=api)
    def set_account(body: dict = Body(...)):
        typ, broker = body.get("type"), body.get("broker", "paper")
        if typ not in ("demo", "real") or broker not in acc.BROKERS:
            raise HTTPException(400, "type must be demo|real and broker must be one of " + ", ".join(acc.BROKERS))
        if typ == "real":
            if broker == "paper":
                raise HTTPException(400, "A REAL account needs a real broker (not the paper simulator).")
            if body.get("confirm_real") != "REAL":
                raise HTTPException(400, 'Type REAL to confirm you are linking a real-money account.')
        acct = {"type": typ, "broker": broker, "label": (body.get("label") or f"{typ}-{broker}")[:40],
                "sandbox": bool(body.get("sandbox")),
                # account SIZE: only the paper simulator takes a typed starting capital; every linked broker account is read from the broker
                "equity": float(body.get("equity") or 100) if broker == "paper" else None,
                "risk_per_trade_pct": float(body.get("risk_per_trade_pct") or 0.5),
                "max_total_open_risk_pct": float(body.get("max_total_open_risk_pct") or 0.5),
                "max_daily_loss_pct": float(body.get("max_daily_loss_pct") or 2.0),
                "max_weekly_loss_pct": float(body.get("max_weekly_loss_pct") or 4.0),
                "max_equity_drawdown_pct": float(body.get("max_equity_drawdown_pct") or 10.0)}
        if broker == "paper" and not 10 <= acct["equity"] <= 10_000_000:
            raise HTTPException(400, "paper starting capital must be between 10 and 10,000,000")
        for k, lo, hi in (("risk_per_trade_pct", 0.05, 5), ("max_total_open_risk_pct", 0.1, 10), ("max_daily_loss_pct", 0.5, 20),
                          ("max_weekly_loss_pct", 0.5, 40), ("max_equity_drawdown_pct", 1, 60)):
            if not lo <= acct[k] <= hi:
                raise HTTPException(400, f"{k} must be between {lo} and {hi}")
        creds = body.get("credentials") or {}
        if broker == "mt5" and creds:
            if not str(creds.get("login", "")).strip().isdigit():
                raise HTTPException(400, "MT5 account number must be digits only")
            if not str(creds.get("server", "")).strip():
                raise HTTPException(400, "MT5 server is required, e.g. Exness-MT5Trial15")
        old = acc.load_account(root_dir) or {}
        acct["symbol_map"] = body.get("symbol_map") if isinstance(body.get("symbol_map"), dict) else (old.get("symbol_map") if old.get("broker") == broker else {}) or {}
        acc.set_credentials(broker, creds)
        acc.save_account(root_dir, acct)
        return {"ok": True, "account": acct}

    @app.post("/api/account/symbols", dependencies=api)
    def set_symbol_map(body: dict = Body(...)):
        """Override the broker symbol the swarm uses for an instrument (e.g. XAUUSD -> XAUUSDm). Empty string = auto-detect again."""
        import re
        acct = acc.load_account(root_dir)
        if acct is None:
            raise HTTPException(400, "Set up an account first.")
        m = {}
        for k, v in (body.get("symbol_map") or {}).items():
            v = str(v or "").strip()
            if k not in cfg_for().instruments or (v and not re.fullmatch(r"[A-Za-z0-9._+#!\-]{1,24}", v)):
                raise HTTPException(400, f"bad symbol mapping for {k}")
            if v:
                m[k] = v
        acct["symbol_map"] = m
        acc.save_account(root_dir, acct)
        return {"ok": True, "account": acct}

    @app.post("/api/account/refresh", dependencies=api)
    def refresh_account():
        """Read the linked account NOW (balance / equity / floating P&L / currency) - same function the swarm uses at the start of every run."""
        acct = acc.load_account(root_dir)
        if acct is None:
            raise HTTPException(400, "Set up an account first.")
        cfg = cfg_for(acct)
        st = acc.resolve_account(root_dir, acct, cfg, Journal(root_dir, False), record=True)
        acc.apply_broker_specs(cfg, st)
        return {"account_state": st, "feasibility": feasibility(cfg, st.get("equity_usd"))}

    @app.delete("/api/account", dependencies=api)
    def reset_account():
        acc.delete_account(root_dir)
        return {"ok": True}

    @app.post("/api/broker/test", dependencies=api)
    def broker_test(body: dict = Body(...)):
        broker = body.get("broker", "paper")
        if broker not in acc.BROKERS:
            raise HTTPException(400, "unknown broker")
        creds = body.get("credentials") or None
        if broker == "mt5" and creds:
            if not str(creds.get("login", "")).strip().isdigit():
                raise HTTPException(400, "MT5 account number must be digits only")
            if not str(creds.get("server", "")).strip():
                raise HTTPException(400, "MT5 server is required, e.g. Exness-MT5Trial15 (copy it from the terminal)")
        return acc.test_connection(broker, creds, bool(body.get("sandbox")), body.get("type") == "real", cfg_for().instruments,
                                   (acc.load_account(root_dir) or {}).get("symbol_map") or {})

    # ------------------------------------------------------------------ runs
    @app.post("/api/run", dependencies=api)
    def run_swarm(body: dict = Body(...)):
        from swarm.pipeline import run_analysis
        acct = acc.load_account(root_dir)
        if acct is None:
            raise HTTPException(400, "Set up an account first (Account tab).")
        demo, no_llm = bool(body.get("demo")), bool(body.get("no_llm"))
        symbols = body.get("symbols") or []
        cfg0 = cfg_for(acct)
        bad = [s for s in symbols if s not in cfg0.instruments]
        if not symbols or bad:
            raise HTTPException(400, f"unknown/empty symbols: {bad or symbols}")
        live = bool(body.get("send_live")) and app.state.allow_live and acct["type"] == "real" and not demo and not no_llm
        if body.get("send_live") and not live:
            why = "server not started with --allow-live" if not app.state.allow_live else "needs Real account + live data + Claude agents"
            raise HTTPException(400, f"Live sending refused: {why}.")
        if not (demo or no_llm) and not os.getenv("ANTHROPIC_API_KEY"):
            raise HTTPException(400, "ANTHROPIC_API_KEY missing (.env). Use 'Rule-based agents' or Demo data to try without it.")

        def fn(progress):
            cfg = cfg_for(acct, live)
            llm = LLM(cfg, mock=demo or no_llm)
            run = run_analysis(cfg, llm, symbols, query=body.get("query", ""), demo=demo, equity=None, allow_live=live, progress=progress, account=acct)
            d = run_to_dict(run)
            save_run(root_dir, d)
            return {"run_id": d["id"]}
        return start_job("run", fn)

    @app.get("/api/jobs/{jid}", dependencies=api)
    def job(jid: str, since: int = 0):
        j = jobs.get(jid)
        if not j:
            raise HTTPException(404, "no such job")
        return {"id": jid, "kind": j["kind"], "status": j["status"], "log": j["log"][since:], "next": len(j["log"]),
                "result": j["result"], "error": j["error"]}

    @app.get("/api/runs/{run_id}", dependencies=api)
    def get_run(run_id: str):
        if run_id == "latest":
            rs = list_runs(root_dir)
            if not rs:
                raise HTTPException(404, "no runs yet")
            run_id = rs[0]["id"]
        d = load_run(root_dir, run_id)
        if d is None:
            raise HTTPException(404, "run not found")
        return d

    # ------------------------------------------------------------------ backtest lab
    @app.post("/api/backtest", dependencies=api)
    def run_backtest(body: dict = Body(...)):
        from swarm.tools.backtest import compute_backtest
        cfg = cfg_for()
        sym, demo = body.get("symbol"), bool(body.get("demo"))
        if sym not in cfg.instruments:
            raise HTTPException(400, "unknown symbol")
        return start_job("backtest", lambda progress: {"symbol": sym, "seconds": compute_backtest(cfg, sym, demo, progress)["compute_seconds"]})

    @app.get("/api/backtest/{symbol}", dependencies=api)
    def get_backtest(symbol: str, demo: bool = False):
        from swarm.tools.backtest import _cache_path, gate_checks
        import json
        cfg = cfg_for()
        p = _cache_path(root_dir, symbol, demo)
        if not p.exists():
            raise HTTPException(404, "no backtest yet for this symbol - run one")
        d = json.loads(p.read_text(encoding="utf-8"))
        rr = sorted(t["rr"] for t in d["trades"]) or [2.0]
        chk, m, mc = gate_checks(d["trades"], rr[len(rr) // 2], cfg.backtest, float(cfg.account.risk_per_trade_pct))
        d["strategy_gate"] = {"checks": chk, "passed": all(c["passed"] for c in chk), "assumed_rr": rr[len(rr) // 2]}
        d["thresholds"] = dict(cfg.backtest)
        d.pop("signature", None)
        return d

    @app.post("/api/validate", dependencies=api)
    def validate_trade(body: dict = Body(...)):
        """Trade simulator: run the per-trade Monte Carlo ladder on any entry/SL/TP you type (uses the cached 1h history of the symbol)."""
        import numpy as np
        from swarm.agents.trade_validator import TradeValidatorAgent
        from swarm.tools.backtest import _cache_path, fetch_history, load_price_cache, metrics, save_price_cache, select_sample
        from swarm.tools.risk import levels_sane
        import json
        cfg = cfg_for()
        sym, demo = body.get("symbol"), bool(body.get("demo"))
        if sym not in cfg.instruments:
            raise HTTPException(400, "unknown symbol")
        try:
            side, entry, sl, tp = body["side"], float(body["entry"]), float(body["sl"]), float(body["tp"])
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "need side, entry, sl, tp")
        if side not in ("LONG", "SHORT") or not levels_sane(side, entry, sl, tp):
            raise HTTPException(400, "levels are not coherent for this side (LONG: SL < entry < TP, SHORT: TP < entry < SL)")
        ladder = body.get("runs") or cfg.validator.runs_ladder
        if not isinstance(ladder, list) or not ladder or any(not isinstance(n, int) or not 5 <= n <= 20000 for n in ladder):
            raise HTTPException(400, "runs must be a list of integers between 5 and 20000")
        inst = cfg.instruments[sym]
        px = load_price_cache(root_dir, sym, demo)
        if px is None:
            df = fetch_history(sym, inst, int(cfg.backtest.history_bars), demo)
            save_price_cache(root_dir, sym, demo, df)
            px = load_price_cache(root_dir, sym, demo)
        price = float(body.get("price") or px["c"][-1])
        tr = np.maximum(px["h"][1:] - px["l"][1:], np.maximum(abs(px["h"][1:] - px["c"][:-1]), abs(px["l"][1:] - px["c"][:-1])))
        atr = float(tr[-14:].mean())
        ev = None
        p = _cache_path(root_dir, sym, demo)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            rr = abs(tp - entry) / abs(entry - sl)
            sample, basis = select_sample(d["trades"], side, rr, cfg.backtest)
            m = metrics([t["r"] for t in sample]) if sample else {}
            if m.get("n"):
                ev = {k: m.get(k) for k in ("n", "win_rate_pct", "win_rate_ci95", "expectancy_r", "expectancy_se_r", "profit_factor", "avg_win_r", "avg_loss_r")}
                ev["basis"] = basis
        cfg.validator.runs_ladder = ladder
        v = TradeValidatorAgent(cfg, LLM(cfg, mock=True)).evaluate(cfg, inst, {"side": side, "entry": entry, "sl": sl, "tp": tp}, price, atr, px, ev)
        return {"symbol": sym, "price": price, "atr_1h": round(atr, 4), "validation": v.to_dict()}

    # ------------------------------------------------------------------ journal / critic
    @app.get("/api/journal", dependencies=api)
    def journal(demo: bool = False):
        j = Journal(root_dir, demo)
        rows = j.all()
        slim = [{k: r.get(k) for k in ("id", "ts", "symbol", "status", "mode", "demo", "proposal", "verdict", "orders", "risk_pct", "lots", "outcome", "critic", "backtest", "validation", "account")} for r in rows]
        return {"trades": slim[-200:], "stats": j.stats(), "lessons": j.recent_lessons(30),
                "exposure": {"open_risk_pct": j.open_risk_pct(), "today_pnl_pct": j.today_pnl_pct()}}

    @app.post("/api/review", dependencies=api)
    def review(body: dict = Body(default={})):
        from swarm.review import review_trades
        demo, no_llm = bool(body.get("demo")), bool(body.get("no_llm"))
        cfg = cfg_for()
        if not (demo or no_llm) and not os.getenv("ANTHROPIC_API_KEY"):
            raise HTTPException(400, "ANTHROPIC_API_KEY missing - tick 'rule-based'")
        return start_job("review", lambda progress: review_trades(cfg, LLM(cfg, mock=demo or no_llm), demo=demo, progress=progress))

    return app


def run(host="127.0.0.1", port=8000, allow_live=False, config_path=None):
    import uvicorn
    app = make_app(config_path=config_path, allow_live=allow_live)
    print(f"\n  Trading Swarm dashboard:  http://{host}:{port}/?token={app.state.token}\n"
          f"  live order sending: {'ENABLED (--allow-live)' if allow_live else 'disabled (paper only)'}\n")
    if host not in ("127.0.0.1", "localhost"):
        print("  WARNING: you bound the dashboard to a non-local address. Anyone with the token can drive it.\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
