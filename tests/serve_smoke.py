"""Starts the dashboard on :8765 with a throw-away data root, for tests/dashboard_smoke.js (jsdom).  python tests/serve_smoke.py"""
import os
import sys
import tempfile
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
if os.environ.get("SMOKE_MT5_STUB"):                      # pretend an Exness-MT5Trial15 terminal exists (login 12345678 / pw / Exness-MT5Trial15)
    from tests.test_mt5 import make_mt5
    sys.modules["MetaTrader5"] = make_mt5()
import uvicorn  # noqa: E402
import server  # noqa: E402

root = os.environ.get("SMOKE_ROOT") or os.path.join(tempfile.gettempdir(), "swarm_smoke_root")
os.makedirs(root, exist_ok=True)
c = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
c["backtest"]["history_bars"] = 2500                    # faster than production
cfg = os.path.join(root, "smoke_config.yaml")
open(cfg, "w").write(yaml.safe_dump(c))
uvicorn.run(server.make_app(root=root, config_path=cfg, allow_live=False, token="tok"), host="127.0.0.1", port=8765, log_level="warning")
