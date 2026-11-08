"""MetaTrader 5 linking with ANY broker (login + password + server), tested against a stub of the `MetaTrader5` module that behaves like an
Exness-style server (Exness-MT5Trial15). The real package is Windows-only, so the real-terminal path itself is UNTESTED here."""
import os
import sys
import types
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

from swarm.config import load_config
from swarm.journal import Journal
from swarm.llm import LLM
from swarm.pipeline import run_analysis
from swarm.tools import accounts as acc
from swarm.tools import market_data as md
from swarm.tools.backtest import fetch_history
from tests.test_swarm import cfg, bt_cache_root, _test_cfg  # noqa: F401  (fixtures)

CREDS = {"login": "12345678", "password": "pw", "server": "Exness-MT5Trial15"}


def make_mt5(layout="standard", currency="USD", trade_mode=0, trade_allowed=True, equity=137.5, calls=None):
    """layout: standard (XAUUSDm / USTECm), pro (XAUUSD / USTEC), gold (GOLD), cent."""
    names = {"standard": ["XAUUSDm", "BTCUSDm", "USTECm", "EURUSDm", "XAUUSD_x100"], "cent": ["XAUUSDc", "BTCUSDc", "USTECc"],
             "pro": ["XAUUSD", "BTCUSD", "USTEC", "EURUSD"], "gold": ["GOLD", "BTCUSD", "NAS100.cash"]}[layout]
    calls = calls if calls is not None else []
    m = types.ModuleType("MetaTrader5")
    state = {"err": (1, "Success")}
    for i, n in enumerate(["M1", "M5", "M15", "H1", "H4", "D1"]):
        setattr(m, f"TIMEFRAME_{n}", i + 1)

    def initialize(*a, **kw):
        calls.append(("initialize", kw))
        if str(kw.get("login")) != CREDS["login"] or kw.get("password") != CREDS["password"] or kw.get("server") != CREDS["server"]:
            state["err"] = (-6, "Authorization failed")
            return False
        return True

    cent = layout == "cent"
    spec = {"XAU": dict(contract=100.0, tick=0.01, vmin=0.01, vmax=200.0, spread=0.18), "BTC": dict(contract=1.0, tick=0.01, vmin=0.01, vmax=20.0, spread=12.0),
            "UST": dict(contract=1.0, tick=0.01, vmin=0.1, vmax=100.0, spread=1.2)}

    def symbol_info(name):
        k = next((k for k in spec if name.upper().startswith(k)), None) or ("XAU" if name == "GOLD" else "UST" if name.startswith("NAS") else "XAU")
        sp = spec[k]
        mult = 100.0 if cent else 1.0
        return NS(name=name, visible=True, trade_contract_size=sp["contract"], trade_tick_size=sp["tick"], trade_tick_value=sp["contract"] * sp["tick"] * mult,
                  trade_tick_value_loss=sp["contract"] * sp["tick"] * mult, volume_min=sp["vmin"], volume_step=0.01, volume_max=sp["vmax"], point=sp["tick"],
                  digits=2, spread=int(sp["spread"] / sp["tick"]), currency_profit="USD", trade_mode=4)

    m.initialize = initialize
    m.shutdown = lambda: calls.append(("shutdown", {}))
    m.last_error = lambda: state["err"]
    m.account_info = lambda: NS(login=12345678, server="Exness-MT5Trial15", name="Noamane", company="Exness Technologies Ltd", currency=currency,
                                equity=equity, balance=equity - 2.0, margin_free=equity - 5.0, leverage=2000, profit=-2.0, trade_mode=trade_mode, trade_allowed=trade_allowed)
    m.positions_get = lambda *a, **k: (NS(ticket=1),)
    m.symbols_get = lambda *a, **k: [NS(name=n) for n in names]
    m.symbol_info = symbol_info
    m.symbol_select = lambda n, f=True: True
    m.symbol_info_tick = lambda n: (lambda sp: NS(bid=2000.0, ask=2000.0 + sp))(spec[next((k for k in spec if n.upper().startswith(k)), "XAU")]["spread"])

    def rates(name, tf, pos, n):
        calls.append(("rates", name, tf, n))
        t = 1_700_000_000 + 3600 * np.arange(n)
        c = 2000 + np.cumsum(np.sin(np.arange(n) / 7.0))
        return np.rec.fromarrays([t, c, c + 1, c - 1, c, np.full(n, 100)], names="time,open,high,low,close,tick_volume")

    m.copy_rates_from_pos = rates
    m.order_send = lambda req: NS(retcode=10009, comment="done")
    return m


@pytest.fixture
def mt5(monkeypatch):
    saved = {k: v for k, v in os.environ.items() if k.startswith("MT5_")}
    acc._CREDS.clear()
    acc._MT5_STATE.update(ready=False, key=None, keep=0)
    calls = []
    mod = make_mt5(calls=calls)
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    acc.set_credentials("mt5", CREDS)
    yield NS(mod=mod, calls=calls, use=lambda **kw: monkeypatch.setitem(sys.modules, "MetaTrader5", make_mt5(calls=calls, **kw)))
    acc._CREDS.clear()
    acc._MT5_STATE.update(ready=False, key=None, keep=0)
    for k in [k for k in os.environ if k.startswith("MT5_")]:
        del os.environ[k]
    os.environ.update(saved)


# ------------------------------------------------------------------ symbol names differ per broker / account type
def test_symbol_resolution_across_account_types():
    r = acc.resolve_mt5_symbol
    assert r({"XAUUSDm", "USTECm", "BTCUSDm"}, ["XAUUSD", "GOLD"])[0] == "XAUUSDm"                  # Exness Standard
    assert r({"XAUUSD", "USTEC"}, ["XAUUSD", "GOLD"])[0] == "XAUUSD"                                 # Exness Pro: no suffix
    assert r({"GOLD", "NAS100.cash"}, ["XAUUSD", "GOLD"])[0] == "GOLD"                               # other brokers
    assert r({"XAUUSD_x100"}, ["XAUUSD"])[0] is None                                                 # amplified contract is never guessed
    assert r({"XAUUSDm", "XAUUSD"}, ["XAUUSD"])[1] == ["XAUUSD", "XAUUSDm"]                          # ambiguity is reported
    assert r({"XAUUSDm", "XAUUSD"}, ["XAUUSDm"], locked=True)[0] == "XAUUSDm"                         # user override is exact
    assert r({"xauusdM"}, ["XAUUSD"])[0] == "xauusdM"                                                # case-insensitive


def test_exness_style_account_is_read_with_login_password_server(mt5):
    cfgx = load_config()
    res = acc.read_broker("mt5", None, False, cfgx.instruments)
    assert res["ok"] and res["equity"] == 137.5 and res["balance"] == 135.5 and res["account_kind"] == "demo" and res["currency"] == "USD"
    assert res["server"] == "Exness-MT5Trial15" and res["open_positions"] == 1 and res["floating_pnl"] == -2.0 and res["trade_allowed"] is True
    s = res["symbols"]
    assert s["XAUUSD"]["broker_symbol"] == "XAUUSDm" and s["BTCUSD"]["broker_symbol"] == "BTCUSDm" and s["NAS100"]["broker_symbol"] == "USTECm"
    assert s["XAUUSD"]["per_unit"] == pytest.approx(100.0) and s["XAUUSD"]["volume_min"] == 0.01 and s["NAS100"]["volume_min"] == 0.1
    init = [c for c in mt5.calls if c[0] == "initialize"][0][1]
    assert init["login"] == 12345678 and init["server"] == "Exness-MT5Trial15" and init["password"] == "pw" and "path" not in init
    assert mt5.calls[-1][0] == "shutdown"                                                            # no session left open


def test_terminal_path_is_passed_when_given(mt5):
    acc.set_credentials("mt5", {**CREDS, "path": r"C:\Program Files\MetaTrader 5\terminal64.exe"})
    assert acc.read_broker("mt5", None, False, {})["ok"]
    assert [c for c in mt5.calls if c[0] == "initialize"][-1][1]["path"].endswith("terminal64.exe")


def test_account_size_and_instrument_specs_come_from_the_broker(mt5, cfg):
    st = acc.resolve_account(cfg.root, {"broker": "mt5", "type": "demo", "label": "exness-demo"}, cfg, Journal(cfg.root, False))
    assert st["ok"] and st["equity_usd"] == 137.5 and st["currency_ok"] and st["source"] == "broker" and st["server"] == "Exness-MT5Trial15"
    notes = acc.apply_broker_specs(cfg, st)
    g = cfg.instruments["XAUUSD"]
    assert g.mt5_symbol == "XAUUSDm" and g.min_lot == 0.01 and g.lot_step == 0.01 and g.max_lot == 200.0 and g.usd_per_point_per_lot == pytest.approx(100.0)
    assert g.feed == "mt5" and g.feed_is_proxy is False and g.specs_source == "broker"
    assert cfg.instruments["NAS100"].mt5_symbol == "USTECm" and cfg.instruments["NAS100"].min_lot == 0.1
    assert 0.1 <= g.typical_spread <= 0.3 and len(notes) == 3


def test_cent_account_is_consistent(mt5, cfg):
    mt5.use(layout="cent", currency="USC", equity=5000.0)                 # 5000 US cents = $50
    st = acc.resolve_account(cfg.root, {"broker": "mt5", "type": "demo", "label": "cent"}, cfg, Journal(cfg.root, False))
    assert st["equity_usd"] == 50.0 and st["currency_ok"]
    acc.apply_broker_specs(cfg, st)
    assert cfg.instruments["XAUUSD"].usd_per_point_per_lot == pytest.approx(100.0)       # tick value read in cents / 100: same units as the equity
    assert cfg.instruments["XAUUSD"].mt5_symbol == "XAUUSDc"


def test_eur_account_needs_no_fx_guess(mt5, cfg):
    mt5.use(currency="EUR", equity=1000.0)
    st = acc.resolve_account(cfg.root, {"broker": "mt5", "type": "demo", "label": "eur"}, cfg, Journal(cfg.root, False))
    assert st["currency_ok"] and st["equity_usd"] == 1000.0 and st["unit_currency"] == "EUR" and any("EUR" in w for w in st["warnings"])


def test_read_only_investor_login_is_flagged(mt5, cfg):
    mt5.use(trade_allowed=False)
    t = acc.test_connection("mt5", None, False, False, cfg.instruments)
    assert t["ok"] and any("cannot trade" in w for w in t["warnings"])


def test_real_vs_demo_kind_mismatch_warns(mt5, cfg):
    mt5.use(trade_mode=2)                                                  # broker says REAL
    t = acc.test_connection("mt5", None, False, False, cfg.instruments)
    assert any("REAL" in w for w in t["warnings"])
    mt5.use(trade_mode=0)
    t = acc.test_connection("mt5", None, False, True, cfg.instruments)
    assert any("demo" in w for w in t["warnings"])


# ------------------------------------------------------------------ failures are explained
def test_wrong_password_gives_a_clear_hint(mt5):
    acc.set_credentials("mt5", {**CREDS, "password": "nope"})
    r = acc.read_broker("mt5", None, False, {})
    assert not r["ok"] and "authorization failed" in r["message"] and "server" in r["message"]


def test_missing_fields_and_non_numeric_login(mt5):
    acc._CREDS["mt5"] = {"login": "abc", "password": "x", "server": "S"}
    assert "digits only" in acc.read_broker("mt5", None, False, {})["message"]
    acc._CREDS["mt5"] = {"login": "1", "password": "x"}
    assert "missing: server" in acc.read_broker("mt5", None, False, {})["message"]


def test_package_missing_on_non_windows(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)
    r = acc.read_broker("mt5", CREDS, False, {})
    assert not r["ok"] and "Windows" in r["message"]


# ------------------------------------------------------------------ one login per run, broker prices
def test_session_is_reused_inside_keep_and_closed_after(mt5):
    with acc.keep_mt5():
        with acc.mt5_session():
            pass
        with acc.mt5_session():
            pass
        assert [c[0] for c in mt5.calls].count("initialize") == 1 and "shutdown" not in [c[0] for c in mt5.calls]
    assert mt5.calls[-1][0] == "shutdown"
    n = [c[0] for c in mt5.calls].count("initialize")
    with acc.mt5_session():
        pass
    assert [c[0] for c in mt5.calls].count("initialize") == n + 1 and mt5.calls[-1][0] == "shutdown"


def test_candles_and_history_come_from_the_broker_with_fallback(mt5, cfg, monkeypatch):
    inst = cfg.instruments["XAUUSD"]
    inst["mt5_symbol"], inst["feed"] = "XAUUSDm", "mt5"
    df = md.fetch_ohlcv("XAUUSD", inst, "15m", 300)
    assert len(df) == 300 and list(df.columns) == ["open", "high", "low", "close", "volume"] and str(df.index.tz) == "UTC"
    assert ("rates", "XAUUSDm", 3, 300) in mt5.calls
    assert md.fetch_spread(inst) == pytest.approx(0.18)
    h = fetch_history("XAUUSD", inst, 500, demo=False)
    assert h.attrs["feed_used"] == "mt5" and len(h) >= 400
    # terminal unreachable -> public feed, flagged as a proxy
    acc.set_credentials("mt5", {**CREDS, "password": "bad"})
    monkeypatch.setattr(md, "_yahoo", lambda t, tf, n: pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]}, index=pd.to_datetime(["2026-01-01"], utc=True)))
    assert len(md.fetch_ohlcv("XAUUSD", inst, "1h", 10)) == 1
    import swarm.tools.backtest as B
    monkeypatch.setattr(B, "_fetch_public_history", lambda s, i, b: pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]}, index=pd.to_datetime(["2026-01-01"], utc=True)))
    h2 = B.fetch_history("XAUUSD", inst, 500, demo=False)
    assert h2.attrs["feed_used"] == "yahoo/ccxt" and "mt5 feed failed" in h2.attrs["fallback_reason"] and B.feed_info(inst, h2)["proxy"] is True


def test_live_order_goes_through_the_same_session_and_stays_gated(mt5, monkeypatch):
    from swarm.tools import brokers
    payload = {"broker": "mt5", "request": {"symbol": "XAUUSDm", "type": 0, "price": None, "volume": 0.01}}
    assert brokers.submit(payload, "paper", False)["status"] != "LIVE_SENT"
    assert brokers.submit(payload, "live", True)["status"] == "LIVE_BLOCKED"                           # env gate still closed
    monkeypatch.setenv("LIVE_TRADING", "YES")
    r = brokers.submit(payload, "live", True)
    assert r["status"] == "LIVE_SENT" and r["retcode"] == 10009 and mt5.calls[-1][0] == "shutdown"


# ------------------------------------------------------------------ whole pipeline on a broker account
def test_pipeline_sizes_on_the_broker_account_and_uses_its_symbols(mt5, cfg):
    run = run_analysis(cfg, LLM(cfg, mock=True), ["XAUUSD", "NAS100"], demo=True, progress=lambda *_: None,
                       account={"broker": "mt5", "type": "demo", "label": "exness-demo"})
    for c in run["ctxs"].values():
        assert c.equity == 137.5 and c.account["server"] == "Exness-MT5Trial15"
    assert run["ctxs"]["XAUUSD"].inst.mt5_symbol == "XAUUSDm" and run["ctxs"]["NAS100"].inst.mt5_symbol == "USTECm"
    assert [c[0] for c in mt5.calls].count("initialize") == 1                                         # one login for the whole run
    assert mt5.calls[-1][0] == "shutdown"


# ------------------------------------------------------------------ dashboard API
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server
    cfgp = tmp_path / "config.yaml"
    import yaml
    c = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")))
    c["backtest"]["history_bars"] = 2500
    cfgp.write_text(yaml.safe_dump(c))
    return TestClient(server.make_app(root=str(tmp_path), config_path=str(cfgp), allow_live=False, token="tok"))


H = {"X-Token": "tok"}


def test_dashboard_links_an_mt5_account_by_login_password_server(mt5, client):
    bad = client.post("/api/broker/test", headers=H, json={"broker": "mt5", "type": "demo", "credentials": {**CREDS, "login": "abc"}})
    assert bad.status_code == 400
    assert client.post("/api/broker/test", headers=H, json={"broker": "mt5", "type": "demo", "credentials": {**CREDS, "server": " "}}).status_code == 400
    t = client.post("/api/broker/test", headers=H, json={"broker": "mt5", "type": "demo", "credentials": CREDS}).json()
    assert t["ok"] and t["equity"] == 137.5 and t["symbols"]["XAUUSD"]["broker_symbol"] == "XAUUSDm" and "pw" not in str(t)
    r = client.post("/api/account", headers=H, json={"type": "demo", "broker": "mt5", "credentials": CREDS})
    assert r.status_code == 200 and r.json()["account"]["equity"] is None and "pw" not in r.text
    st = client.post("/api/account/refresh", headers=H).json()["account_state"]
    assert st["equity_usd"] == 137.5 and st["symbols"]["NAS100"]["broker_symbol"] == "USTECm" and "pw" not in str(st)
    state = client.get("/api/state", headers=H)
    assert state.json()["equity"] == 137.5 and "pw" not in state.text
    f = {x["symbol"]: x for x in state.json()["feasibility"]}
    assert f["XAUUSD"]["min_lot"] == 0.01 and f["XAUUSD"]["tradable"] is False                          # $137.5 at 0.5% risk cannot fund a normal gold stop


def test_symbol_override_endpoint(mt5, client):
    client.post("/api/account", headers=H, json={"type": "demo", "broker": "mt5", "credentials": CREDS})
    assert client.post("/api/account/symbols", headers=H, json={"symbol_map": {"XAUUSD": "bad name!"}}).status_code == 400
    assert client.post("/api/account/symbols", headers=H, json={"symbol_map": {"NOPE": "X"}}).status_code == 400
    assert client.post("/api/account/symbols", headers=H, json={"symbol_map": {"XAUUSD": "XAUUSDm"}}).json()["account"]["symbol_map"] == {"XAUUSD": "XAUUSDm"}
    st = client.post("/api/account/refresh", headers=H).json()["account_state"]
    assert st["symbols"]["XAUUSD"]["broker_symbol"] == "XAUUSDm"
