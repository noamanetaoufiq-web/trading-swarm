"""Dashboard account setup: Demo vs Real, broker linking, connection tests.

SECURITY MODEL
  * Credentials are held IN MEMORY of the local server process (and exported to os.environ for brokers.submit()).
    They are NEVER written to disk by the dashboard and never returned by the API. Restart = re-enter (or put them in .env).
  * The saved account file (data/account.json) only holds non-secret metadata.
  * The server binds to 127.0.0.1 and every /api call needs the start-up token.
"""
from __future__ import annotations
import contextlib
import json
import os
import threading
import time
from pathlib import Path

BROKERS = {
    "paper": {"label": "Paper simulator (built-in)", "fields": [], "desc": "Orders are simulated in the journal. No broker needed."},
    "binance": {"label": "Binance USD-M Futures", "sandbox": True, "desc": "Crypto. API key needs read (+ trade only if you ever go live). Testnet supported.",
                "fields": [{"key": "api_key", "label": "API key"}, {"key": "api_secret", "label": "API secret", "secret": True}]},
    "mt5": {"label": "MetaTrader 5 (any broker / funded account)", "recommended": True,
            "desc": "Works with ANY broker or prop firm that gives you an MT5 login: account number + password + server, exactly as in your MT5 terminal (e.g. Exness-MT5Trial15). The swarm reads balance, equity, symbols, lot sizes and prices from the account itself. Needs the MetaTrader5 python package (Windows only) and the MT5 terminal installed on this PC.",
            "fields": [{"key": "login", "label": "Account number (login)", "placeholder": "e.g. 12345678"}, {"key": "password", "label": "Password (the trading password, not the investor one)", "secret": True},
                       {"key": "server", "label": "Server - exactly as shown in your MT5 terminal", "placeholder": "e.g. Exness-MT5Trial15"},
                       {"key": "path", "label": "terminal64.exe path (optional, fixes most IPC-timeout errors)", "optional": True, "placeholder": "C:\\Program Files\\MetaTrader 5\\terminal64.exe"}]},
    "ibkr": {"label": "Interactive Brokers (TWS / Gateway)", "desc": "Needs TWS or IB Gateway running with API enabled, and ib_insync installed. Paper ports: 7497 (TWS) / 4002 (GW); live: 7496 / 4001.",
             "fields": [{"key": "host", "label": "Host", "default": "127.0.0.1"}, {"key": "port", "label": "Port", "default": "7497"}, {"key": "client_id", "label": "Client ID", "default": "11"}]},
}
ENV_MAP = {"binance": {"api_key": "BINANCE_API_KEY", "api_secret": "BINANCE_API_SECRET"},
           "mt5": {"login": "MT5_LOGIN", "password": "MT5_PASSWORD", "server": "MT5_SERVER", "path": "MT5_PATH"}}

_CREDS: dict = {}
_EQUITY: dict = {}


def _path(root) -> Path:
    return Path(root) / "data" / "account.json"


def load_account(root):
    p = _path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_account(root, acct: dict):
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(acct, indent=2), encoding="utf-8")


def delete_account(root):
    p = _path(root)
    if p.exists():
        p.unlink()
    _CREDS.clear()
    _EQUITY.clear()


def set_credentials(broker: str, creds: dict):
    if not creds:
        return
    _CREDS[broker] = {k: str(v) for k, v in creds.items() if v not in (None, "")}
    for k, env in ENV_MAP.get(broker, {}).items():
        if k in _CREDS[broker]:
            os.environ[env] = _CREDS[broker][k]


def has_credentials(broker: str) -> bool:
    return broker in _CREDS and bool(_CREDS[broker])


def broker_equity(broker: str):
    return _EQUITY.get(broker)



# ----------------------------------------------------------------------------- MetaTrader 5 plumbing (one terminal session at a time)
_MT5_LOCK = threading.RLock()
_MT5_STATE = {"ready": False, "key": None, "keep": 0}
MT5_HINTS = {-6: "authorization failed: wrong account number / password, or the server name does not match the terminal exactly (copy it from MT5 > File > Login to Trade Account)",
             -10005: "IPC timeout: the terminal did not answer. Give the full path to terminal64.exe, close other MT5 windows of that terminal, make sure python and MT5 are both 64-bit, and log in manually once so the terminal knows the server",
             -10003: "IPC initialisation failed: the terminal could not be started (check the terminal64.exe path)",
             -2: "invalid parameters (account number must be digits only)", -8: "algo trading is disabled in the terminal"}
DEFAULT_ALIASES = {"XAUUSD": ["XAUUSD", "GOLD"], "BTCUSD": ["BTCUSD", "BTCUSDT", "BITCOIN"],
                   "NAS100": ["USTEC", "NAS100", "US100", "USTECH", "NDX100", "NQ100", "US_TECH100", "NASDAQ"]}
SYMBOL_SUFFIXES = ["", "m", "z", "c", "r", "s", "i", ".m", ".z", ".c", ".r", ".s", ".i", ".a", ".pro", ".raw", ".ecn", ".std", ".cash", "+", "-", "#", "!", "_m", "_i", "_pro"]


def mt5_creds() -> dict:
    c = dict(_CREDS.get("mt5") or {})
    for k, env in ENV_MAP["mt5"].items():
        if not c.get(k) and os.environ.get(env):
            c[k] = os.environ[env]
    return c


@contextlib.contextmanager
def mt5_session(creds: dict | None = None):
    """Serialised access to the terminal: initialize(path, login, password, server) once, reuse while keep_mt5() is active, shutdown afterwards.
    Raises RuntimeError with a human hint on any failure."""
    creds = creds or mt5_creds()
    try:
        import MetaTrader5 as mt5
    except ImportError as e:
        raise RuntimeError("the MetaTrader5 python package is not available. It only exists for Windows: `pip install MetaTrader5`, with the MT5 terminal installed on the same PC (" + str(e) + ")") from e
    missing = [k for k in ("login", "password", "server") if not creds.get(k)]
    if missing:
        raise RuntimeError("MT5 needs account number, password and server (missing: " + ", ".join(missing) + ")")
    try:
        login = int(str(creds["login"]).strip())
    except ValueError as e:
        raise RuntimeError("the MT5 account number must be digits only") from e
    key = (login, creds["server"], creds.get("path") or "")
    with _MT5_LOCK:
        if not (_MT5_STATE["ready"] and _MT5_STATE["key"] == key):
            if _MT5_STATE["ready"]:
                mt5.shutdown()
                _MT5_STATE.update(ready=False, key=None)
            kw = {"path": creds["path"]} if creds.get("path") else {}
            if not mt5.initialize(login=login, password=creds["password"], server=creds["server"], timeout=60000, **kw):
                err = mt5.last_error()
                code = err[0] if isinstance(err, (tuple, list)) and err else err
                raise RuntimeError(f"MT5 initialize/login failed {err}: " + MT5_HINTS.get(code, "see https://www.mql5.com/en/docs/python_metatrader5/mt5lasterror_py"))
            _MT5_STATE.update(ready=True, key=key)
        try:
            yield mt5
        finally:
            if _MT5_STATE["keep"] <= 0:
                mt5.shutdown()
                _MT5_STATE.update(ready=False, key=None)


@contextlib.contextmanager
def keep_mt5():
    """Keep the terminal session open for a whole run (account read + candles + history) instead of re-logging in for every call."""
    with _MT5_LOCK:
        _MT5_STATE["keep"] += 1
    try:
        yield
    finally:
        with _MT5_LOCK:
            _MT5_STATE["keep"] -= 1
            if _MT5_STATE["keep"] <= 0 and _MT5_STATE["ready"]:
                try:
                    import MetaTrader5 as mt5
                    mt5.shutdown()
                except Exception:  # noqa: BLE001
                    pass
                _MT5_STATE.update(ready=False, key=None)


def resolve_mt5_symbol(names, aliases, locked=False):
    """Broker symbol names differ (Exness Standard: XAUUSDm / USTECm, Pro: XAUUSD / USTEC, others GOLD, XAUUSD.r ...).
    Returns (best_match_or_None, all_matches). `locked` = the user typed the exact name: no guessing."""
    low = {n.lower(): n for n in names}
    found = []
    for a in [x for x in aliases if x]:
        for suf in ([""] if locked else SYMBOL_SUFFIXES):
            n = low.get((a + suf).lower())
            if n and n not in found:
                found.append(n)
        if locked and found:
            break
    return (found[0] if found else None), found


def mt5_profile(mt5, names, sym: str, inst, override: str | None = None) -> dict:
    """Everything the sizing maths needs about one instrument, read from the broker: real symbol name, contract, lot limits, tick value, spread."""
    aliases = ([override] if override else []) + [inst.get("mt5_symbol")] + list(inst.get("mt5_aliases") or []) + DEFAULT_ALIASES.get(sym, [sym])
    name, cands = resolve_mt5_symbol(names, aliases, locked=bool(override))
    if not name:
        return {"ok": False, "symbol": sym, "message": f"no symbol like {[a for a in aliases if a][:4]} on this server", "candidates": []}
    si = mt5.symbol_info(name)
    if si is not None and not getattr(si, "visible", True):
        mt5.symbol_select(name, True)
        si = mt5.symbol_info(name)
    if si is None:
        return {"ok": False, "symbol": sym, "broker_symbol": name, "message": f"symbol_info({name}) returned nothing", "candidates": cands}
    ts = float(si.trade_tick_size or si.point or 0)
    tv = float(getattr(si, "trade_tick_value_loss", 0) or si.trade_tick_value or 0)
    tick = mt5.symbol_info_tick(name)
    spread = float(tick.ask - tick.bid) if tick is not None and tick.ask and tick.bid and tick.ask >= tick.bid else float(si.spread or 0) * float(si.point or 0)
    per_unit = tv / ts if ts > 0 else 0.0
    warns = []
    if getattr(si, "trade_mode", 4) != 4:
        warns.append(f"{name}: trading mode {si.trade_mode} (not FULL): orders may be refused")
    if str(getattr(si, "currency_profit", "USD")).upper() == "USD" and si.trade_contract_size and per_unit / float(si.trade_contract_size) > 50:
        warns.append(f"{name}: tick value is ~100x the contract size -> looks like a CENT account (balances in cents)")
    if len(cands) > 1:
        warns.append(f"{sym}: several matching symbols {cands[:5]}; using {name}")
    return {"ok": per_unit > 0, "symbol": sym, "broker_symbol": name, "candidates": cands, "contract_size": float(si.trade_contract_size),
            "volume_min": float(si.volume_min), "volume_step": float(si.volume_step), "volume_max": float(si.volume_max), "tick_size": ts, "tick_value": tv,
            "per_unit": per_unit, "digits": int(si.digits), "spread_price": spread, "currency_profit": str(getattr(si, "currency_profit", "")),
            "trade_mode": int(getattr(si, "trade_mode", 4)), "warnings": warns, "message": "" if per_unit > 0 else "tick value / size unavailable"}


def _quant(x: float) -> float:
    """1 significant figure: keeps cache signatures stable while the live spread wiggles."""
    if x <= 0:
        return 0.0
    from math import floor, log10
    e = 10 ** floor(log10(x))
    return round(round(x / e) * e, 10)


def apply_broker_specs(cfg, st: dict) -> list:
    """Overwrite the hand-typed instrument specs with what the broker account reports (real symbol, lot limits, $ per price unit, spread).
    This is what makes the swarm broker-agnostic: nothing about contract size or lot size has to be known in advance."""
    notes = []
    syms = (st or {}).get("symbols") or {}
    unit_div = 100.0 if str((st or {}).get("currency", "")).upper() == "USC" else 1.0
    for sym, p in syms.items():
        inst = cfg.instruments.get(sym)
        if inst is None or not p.get("ok"):
            continue
        inst["mt5_symbol"] = p["broker_symbol"]
        inst["min_lot"], inst["lot_step"], inst["max_lot"] = p["volume_min"], p["volume_step"], p["volume_max"]
        inst["usd_per_point_per_lot"] = p["per_unit"] / unit_div
        sp, base = p.get("spread_price") or 0.0, float(inst.get("typical_spread") or 0.0)
        if sp > 0 and (base <= 0 or 0.2 * base <= sp <= 5 * base):
            inst["typical_spread"] = _quant(sp)
        inst["specs_source"] = "broker"
        if bool(cfg.account.get("use_broker_history", True)):
            inst["feed"], inst["feed_is_proxy"] = "mt5", False
        notes.append(f"{sym} -> {p['broker_symbol']}: lot {p['volume_min']}..{p['volume_max']} step {p['volume_step']}, ${inst['usd_per_point_per_lot']:.4g}/price-unit/lot, spread {inst['typical_spread']}")
    return notes


def read_broker(broker: str, creds: dict | None = None, sandbox: bool = False, instruments=None, symbol_map=None) -> dict:
    """Read the live account from the broker. Never raises.
    Returns {ok, message, equity, balance, free_margin, leverage, currency, account_kind, open_positions, floating_pnl}.
    MT5 / Binance / IBKR code paths are UNTESTED against real servers (no broker in the build sandbox)."""
    creds = creds or _CREDS.get(broker) or {}
    res = {"ok": False, "message": "", "equity": None, "balance": None, "free_margin": None, "leverage": None,
           "currency": "USD", "account_kind": None, "open_positions": None, "floating_pnl": None}
    try:
        if broker == "paper":
            res.update(ok=True, message="Paper simulator is always available.", account_kind="simulated")
        elif broker == "binance":
            import ccxt
            ex = ccxt.binanceusdm({"apiKey": creds.get("api_key"), "secret": creds.get("api_secret"), "enableRateLimit": True})
            if sandbox:
                ex.set_sandbox_mode(True)
            bal = ex.fetch_balance()
            tot = float(bal.get("total", {}).get("USDT") or 0.0)
            free = float(bal.get("free", {}).get("USDT") or 0.0)
            pos, upnl = None, None
            try:
                ps = [p for p in ex.fetch_positions() if abs(float(p.get("contracts") or 0)) > 0]
                pos, upnl = len(ps), sum(float(p.get("unrealizedPnl") or 0) for p in ps)
            except Exception:  # noqa: BLE001
                pass
            res.update(ok=True, message="Connected to Binance" + (" TESTNET" if sandbox else " (REAL funds)"), equity=tot, balance=tot - (upnl or 0.0),
                       free_margin=free, currency="USDT", account_kind="testnet" if sandbox else "real", open_positions=pos, floating_pnl=upnl)
        elif broker == "mt5":
            with mt5_session(creds if creds else None) as mt5:
                info = mt5.account_info()
                if info is None:
                    raise RuntimeError(f"logged in but account_info() is empty: {mt5.last_error()}")
                kind = {0: "demo", 1: "contest", 2: "real"}.get(getattr(info, "trade_mode", -1), "unknown")
                positions = mt5.positions_get() or []
                res.update(ok=True, message=f"Connected to MT5 account {info.login} on {info.server}", equity=float(info.equity), balance=float(info.balance),
                           free_margin=float(info.margin_free), leverage=int(info.leverage), currency=info.currency, account_kind=kind,
                           open_positions=len(positions), floating_pnl=float(info.profit), account_name=str(getattr(info, "name", "")),
                           server=str(info.server), company=str(getattr(info, "company", "")), trade_allowed=bool(getattr(info, "trade_allowed", True)))
                names = [x.name for x in (mt5.symbols_get() or [])]
                res["symbols"] = {sym: mt5_profile(mt5, names, sym, inst, (symbol_map or {}).get(sym)) for sym, inst in (instruments or {}).items()}
        elif broker == "ibkr":
            from ib_insync import IB
            ib = IB()
            port = int(creds.get("port", 7497))
            ib.connect(creds.get("host", "127.0.0.1"), port, clientId=int(creds.get("client_id", 11)), timeout=6)
            summ = {x.tag: x.value for x in ib.accountSummary()}
            kind = "paper" if port in (7497, 4002) else "real" if port in (7496, 4001) else "unknown"
            res.update(ok=True, message="Connected to IBKR", equity=float(summ.get("NetLiquidation", 0)), balance=float(summ.get("TotalCashValue", 0)),
                       free_margin=float(summ.get("AvailableFunds", 0)), currency=summ.get("Currency", "USD"), account_kind=kind,
                       open_positions=len(ib.positions()), floating_pnl=float(summ.get("UnrealizedPnL", 0)))
            ib.disconnect()
        else:
            res["message"] = f"unknown broker {broker}"
    except ImportError as e:
        res["message"] = f"Missing python package for {broker}: {e}. Install it (see README)."
    except RuntimeError as e:
        res["message"] = str(e)[:400]
    except Exception as e:  # noqa: BLE001
        res["message"] = f"{type(e).__name__}: {str(e)[:300]}"
    return res


def test_connection(broker: str, creds: dict | None = None, sandbox: bool = False, expect_real: bool = False, instruments=None, symbol_map=None) -> dict:
    """Never raises. Returns {ok, message, equity, currency, account_kind, warnings, ms, symbols, ...}."""
    t0 = time.time()
    res = read_broker(broker, creds, sandbox, instruments, symbol_map)
    res["warnings"] = []
    if res["ok"]:
        if res["equity"] is not None:
            _EQUITY[broker] = res["equity"]
        kind = res["account_kind"]
        if expect_real and kind in ("demo", "paper", "testnet", "contest"):
            res["warnings"].append(f"You selected a REAL account but the broker reports a {kind} account.")
        if not expect_real and kind == "real":
            res["warnings"].append("You selected DEMO but the broker reports a REAL account.")
        if res.get("trade_allowed") is False:
            res["warnings"].append("This login cannot trade (investor / read-only password?). The swarm can still read the account and analyse, but no order can be sent.")
        for p in (res.get("symbols") or {}).values():
            res["warnings"] += p.get("warnings", [])
            if not p.get("ok"):
                res["warnings"].append(f"{p.get('symbol')}: {p.get('message')}")
        if broker != "mt5" and str(res.get("currency", "USD")).upper() not in USD_LIKE:
            res["warnings"].append(f"Account currency is {res['currency']}: the sizing maths assumes USD. Set account.allow_non_usd + fx_to_usd in config.yaml or use a USD account.")
    res["ms"] = int((time.time() - t0) * 1000)
    return res


# ----------------------------------------------------------------------------- account state used by every agent
USD_LIKE = ("USD", "USDT", "USDC", "BUSD")
_LAST: dict = {}          # label -> last resolved account state (what the dashboard shows between runs)


def _slug(x: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(x))[:40] or "acct"


class EquityLog:
    """Snapshots of account equity (JSONL). Used for the equity-based daily / weekly loss and the drawdown-from-peak guard.
    Snapshots start when the swarm first sees the account, so the very first reading of a day cannot know about losses made before it."""

    def __init__(self, root, label):
        self.path = Path(root) / "data" / "equity" / f"{_slug(label)}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def rows(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
        return out

    def record(self, equity: float, now=None):
        from datetime import datetime, timezone
        now = now or datetime.now(timezone.utc)
        rows = self.rows()
        if rows and rows[-1].get("equity") == round(equity, 4) and (now.timestamp() - rows[-1]["t"]) < 600:
            return  # no spam
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": now.timestamp(), "ts": now.isoformat(timespec="seconds"), "equity": round(equity, 4)}) + "\n")

    def stats(self, equity: float, now=None) -> dict:
        from datetime import datetime, timedelta, timezone
        now = now or datetime.now(timezone.utc)
        rows = self.rows()
        day0 = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        wk0 = day0 - timedelta(days=day0.weekday())
        first_after = lambda t0: next((r["equity"] for r in rows if r["t"] >= t0.timestamp()), equity)
        d0, w0 = first_after(day0), first_after(wk0)
        peak = max([r["equity"] for r in rows] + [equity])
        pct = lambda a, b: round((a - b) / b * 100, 3) if b else 0.0
        return {"day_start_equity": round(d0, 2), "week_start_equity": round(w0, 2), "peak_equity": round(peak, 2),
                "day_pnl_pct": pct(equity, d0), "week_pnl_pct": pct(equity, w0), "drawdown_pct": round(max(0.0, (peak - equity) / peak * 100), 3) if peak else 0.0,
                "snapshots": len(rows)}


def last_state(label: str):
    return _LAST.get(label)


def resolve_account(root, acct: dict | None, cfg, journal=None, record: bool = True, equity_override: float | None = None, demo: bool = False) -> dict:
    """THE single place that decides how big the account is. Order of truth:
         linked broker (MT5 / Binance / IBKR)  ->  read live at the start of every run (demo AND real accounts)
         paper simulator                       ->  starting capital (config / dashboard / --equity) + realised paper P&L
    Never silently falls back from a linked broker to a typed number: if the broker cannot be read, ok=False and the run must stop."""
    from datetime import datetime, timezone
    broker = (acct or {}).get("broker", "paper")
    label = (acct or {}).get("label") or f"paper{'_demo' if demo else ''}"
    st = {"broker": broker, "type": (acct or {}).get("type", "demo"), "label": label, "ok": True, "message": "", "warnings": [],
          "currency": "USD", "kind": "simulated", "equity": None, "balance": None, "free_margin": None, "leverage": None,
          "open_positions": None, "floating_pnl": None, "source": "paper-sim", "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "currency_ok": True, "equity_usd": None}
    if broker == "paper":
        start = float(equity_override or (acct or {}).get("equity") or cfg.account.equity)
        realised = journal.realized_usd() if journal is not None else 0.0
        eq = round(max(0.0, start + realised), 2)
        st.update(equity=eq, balance=eq, free_margin=eq, start_capital=start, realised_paper_pnl=round(realised, 2),
                  message=f"paper simulator: start ${start:,.2f} + realised ${realised:,.2f}")
    else:
        r = read_broker(broker, None, bool((acct or {}).get("sandbox")), cfg.instruments, (acct or {}).get("symbol_map") or {})
        if not r["ok"]:
            st.update(ok=False, message=r["message"] or "cannot read the broker account", source="broker")
            _LAST[label] = st
            return st
        st.update(equity=r["equity"], balance=r["balance"], free_margin=r["free_margin"], leverage=r["leverage"], currency=r["currency"],
                  kind=r["account_kind"], open_positions=r["open_positions"], floating_pnl=r["floating_pnl"], source="broker", message=r["message"],
                  symbols=r.get("symbols") or {}, account_name=r.get("account_name"), server=r.get("server"), company=r.get("company"), trade_allowed=r.get("trade_allowed", True))
        for p in st["symbols"].values():
            st["warnings"] += p.get("warnings", [])
        if r.get("trade_allowed") is False:
            st["warnings"].append("login is read-only (investor password?): analysis works, orders cannot be sent")
        _EQUITY[broker] = r["equity"]
        want_real = (acct or {}).get("type") == "real"
        if want_real and st["kind"] in ("demo", "paper", "testnet", "contest"):
            st["warnings"].append(f"linked as REAL but the broker reports a {st['kind']} account")
        if not want_real and st["kind"] == "real":
            st["warnings"].append("linked as DEMO but the broker reports a REAL account")
    cur = str(st["currency"]).upper()
    broker_sized = any(p.get("ok") for p in (st.get("symbols") or {}).values())
    if broker_sized:
        # lot value comes from the broker's own tick value in ACCOUNT currency -> equity and risk are in the same unit, no FX guess needed
        div = 100.0 if cur == "USC" else 1.0
        st["equity_usd"] = float(st["equity"]) / div
        st["unit_currency"] = "USD" if cur == "USC" else cur
        if cur not in USD_LIKE and cur != "USC":
            st["warnings"].append(f"account currency {cur}: lot values are read in {cur} from the broker, so sizing is consistent (amounts shown in {cur})")
    elif cur in USD_LIKE:
        st["equity_usd"] = float(st["equity"])
    elif bool(cfg.account.get("allow_non_usd")):
        st["equity_usd"] = float(st["equity"]) * float(cfg.account.get("fx_to_usd", 1.0))
        st["warnings"].append(f"account in {cur}: converted at fx_to_usd={cfg.account.get('fx_to_usd', 1.0)}")
    else:
        st["currency_ok"] = False
        st["equity_usd"] = float(st["equity"])
        st["warnings"].append(f"account currency is {cur}: sizing assumes USD -> trades are refused (config account.allow_non_usd)")
    elog = EquityLog(root, label)
    if st["equity"] is not None:
        if record:
            elog.record(float(st["equity_usd"]))
        st.update(elog.stats(float(st["equity_usd"])))
    _LAST[label] = st
    return st
