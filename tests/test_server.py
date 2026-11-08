import time
import pytest
import yaml
from fastapi.testclient import TestClient

from server import make_app

H = {"X-Token": "tok"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("srv")
    cfgp = root / "cfg.yaml"
    c = yaml.safe_load(open("config.yaml"))
    c["backtest"]["history_bars"] = 2500
    cfgp.write_text(yaml.safe_dump(c))
    return TestClient(make_app(root=str(root), config_path=str(cfgp), allow_live=False, token="tok"))


def wait(client, jid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}", headers=H).json()
        if j["status"] != "running":
            return j
        time.sleep(0.5)
    raise TimeoutError


def test_auth_required(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/").status_code == 200            # the page itself is public, the API is not


def test_account_flow_and_safety(client):
    s = client.get("/api/state", headers=H).json()
    assert s["configured"] is False and len(s["agents"]) == 12
    # real account needs a real broker + typed confirmation
    assert client.post("/api/account", headers=H, json={"type": "real", "broker": "paper"}).status_code == 400
    assert client.post("/api/account", headers=H, json={"type": "real", "broker": "binance", "credentials": {"api_key": "k", "api_secret": "SECRET123"}}).status_code == 400
    # run refused before an account exists
    assert client.post("/api/run", headers=H, json={"symbols": ["XAUUSD"], "demo": True}).status_code == 400
    ok = client.post("/api/account", headers=H, json={"type": "demo", "broker": "paper", "equity": 25000, "risk_per_trade_pct": 0.5})
    assert ok.status_code == 200
    s = client.get("/api/state", headers=H).json()
    assert s["configured"] and s["equity"] == 25000 and s["account"]["type"] == "demo"
    assert client.post("/api/broker/test", headers=H, json={"broker": "paper"}).json()["ok"] is True
    assert client.post("/api/account", headers=H, json={"type": "demo", "broker": "paper", "risk_per_trade_pct": 50}).status_code == 400


def test_secrets_never_returned_or_saved(client):
    client.post("/api/account", headers=H, json={"type": "real", "broker": "binance", "confirm_real": "REAL", "sandbox": True,
                                                  "credentials": {"api_key": "KEY_ABC", "api_secret": "SECRET_XYZ"}})
    blob = client.get("/api/state", headers=H).text
    assert "SECRET_XYZ" not in blob and "KEY_ABC" not in blob
    import pathlib
    root = pathlib.Path(client.app.state.__dict__.get("root", "."))
    # restore a demo account for following tests
    client.post("/api/account", headers=H, json={"type": "demo", "broker": "paper", "equity": 25000, "risk_per_trade_pct": 0.5})


def test_live_sending_is_refused_without_server_flag(client):
    client.post("/api/account", headers=H, json={"type": "real", "broker": "binance", "confirm_real": "REAL"})
    r = client.post("/api/run", headers=H, json={"symbols": ["XAUUSD"], "demo": False, "send_live": True})
    assert r.status_code == 400 and "Live sending refused" in r.json()["detail"]
    client.post("/api/account", headers=H, json={"type": "demo", "broker": "paper", "equity": 25000, "risk_per_trade_pct": 0.5})


def test_run_swarm_end_to_end_and_endpoints(client):
    r = client.post("/api/run", headers=H, json={"symbols": ["XAUUSD", "BTCUSD", "NAS100"], "demo": True})
    assert r.status_code == 200
    j = wait(client, r.json()["job_id"])
    assert j["status"] == "done", j.get("error")
    run = client.get("/api/runs/latest", headers=H).json()
    assert set(run["symbols"]) == {"XAUUSD", "BTCUSD", "NAS100"} and run["demo"] is True
    d = run["detail"]["XAUUSD"]
    assert "backtester" in d["reports"] or d["verdict"] is not None
    assert d["series"]["1h"]["candles"] and "technical" in d["reports"]
    # equity used for sizing comes from the dashboard account
    bt = client.get("/api/backtest/XAUUSD?demo=true", headers=H).json()
    assert bt["metrics"]["n"] > 0 and "strategy_gate" in bt and bt["mc"]["runs"] == 1000
    jr = client.get("/api/journal?demo=true", headers=H).json()
    assert "stats" in jr and "trades" in jr
    st = client.get("/api/state", headers=H).json()
    assert st["runs"] and st["runs"][0]["id"] == run["id"]


# ------------------------------------------------------------------ account size from the broker / agent 12 / feasibility
def _set_demo_paper(client, equity=100):
    assert client.post("/api/account", headers=H, json={"type": "demo", "broker": "paper", "equity": equity}).status_code == 200


def test_linked_broker_has_no_typed_equity_and_is_read_live(client, monkeypatch):
    from swarm.tools import accounts as acc
    monkeypatch.setattr(acc, "read_broker", lambda b, c=None, s=False, *a, **k: {"ok": True, "message": "fake mt5", "equity": 321.0, "balance": 320.0, "free_margin": 300.0,
                                                                     "leverage": 500, "currency": "USD", "account_kind": "demo", "open_positions": 0, "floating_pnl": 1.0})
    r = client.post("/api/account", headers=H, json={"type": "demo", "broker": "mt5", "equity": 99999, "credentials": {"login": "1", "password": "PW_SECRET", "server": "x"}})
    assert r.status_code == 200 and r.json()["account"]["equity"] is None            # the typed number is ignored for a broker account
    st = client.post("/api/account/refresh", headers=H).json()["account_state"]
    assert st["equity_usd"] == 321.0 and st["source"] == "broker" and st["kind"] == "demo"
    s = client.get("/api/state", headers=H)
    assert s.json()["equity"] == 321.0 and "PW_SECRET" not in s.text
    # run uses the broker's equity
    j = wait(client, client.post("/api/run", headers=H, json={"symbols": ["XAUUSD"], "demo": True}).json()["job_id"])
    assert j["status"] == "done", j.get("error")
    assert any("equity 321.00" in line for line in j["log"])
    # broker becomes unreadable -> the run stops (no fallback to a typed number)
    monkeypatch.setattr(acc, "read_broker", lambda b, c=None, s=False, *a, **k: {"ok": False, "message": "terminal closed", "equity": None})
    j = wait(client, client.post("/api/run", headers=H, json={"symbols": ["XAUUSD"], "demo": True}).json()["job_id"])
    assert j["status"] == "error" and "no fallback" in j["error"]
    _set_demo_paper(client, 25000)


def test_feasibility_flags_a_hundred_dollar_account(client):
    _set_demo_paper(client, 100)
    f = {x["symbol"]: x for x in client.get("/api/state", headers=H).json()["feasibility"]}
    assert f["XAUUSD"]["tradable"] is False and f["XAUUSD"]["risk_budget_usd"] == 0.5 and f["XAUUSD"]["risk_at_min_lot_usd"] > 0.5
    _set_demo_paper(client, 25000)
    f = {x["symbol"]: x for x in client.get("/api/state", headers=H).json()["feasibility"]}
    assert f["XAUUSD"]["tradable"] is True


def test_validate_endpoint_returns_every_ladder_step(client):
    _set_demo_paper(client, 25000)
    base = {"symbol": "XAUUSD", "demo": True, "side": "LONG"}
    px = client.post("/api/validate", headers=H, json={**base, "entry": 1, "sl": 2, "tp": 3})
    assert px.status_code == 400                                                    # incoherent levels
    out = client.post("/api/validate", headers=H, json={**base, "entry": 0.0, "sl": 0.0, "tp": 0.0})
    assert out.status_code == 400
    out = client.post("/api/validate", headers=H, json={**base, "price": 2000, "entry": 1995, "sl": 1985, "tp": 2015, "runs": [10, 100, 1000]})
    assert out.status_code == 200
    v = out.json()["validation"]
    assert [s["runs"] for s in v["sim"]["lenses"]["neutral"]["ladder"]] == [10, 100, 1000]
    assert v["verdict"] in ("GOOD", "MARGINAL", "BAD") and v["checks"]
    assert client.post("/api/validate", headers=H, json={**base, "price": 2000, "entry": 1995, "sl": 1985, "tp": 2015, "runs": [1]}).status_code == 400


def test_run_result_carries_validator_and_account(client):
    _set_demo_paper(client, 25000)
    j = wait(client, client.post("/api/run", headers=H, json={"symbols": ["XAUUSD", "BTCUSD", "NAS100"], "demo": True}).json()["job_id"])
    assert j["status"] == "done", j.get("error")
    run = client.get("/api/runs/latest", headers=H).json()
    assert run["account"]["equity_usd"] == 25000 and run["account"]["source"] == "paper-sim"
    for s, d in run["detail"].items():
        if d["verdict"] and d["verdict"]["approved"]:
            assert d["reports"]["trade_validator"]["approved"] is True
    jr = client.get("/api/journal?demo=true", headers=H).json()
    assert all("validation" in t for t in jr["trades"])
