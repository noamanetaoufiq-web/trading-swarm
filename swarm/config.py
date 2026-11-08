"""Config loader: YAML + .env, with attribute access (cfg.risk.min_rr)."""
from __future__ import annotations
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


class Cfg(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


def _wrap(x):
    if isinstance(x, dict):
        return Cfg({k: _wrap(v) for k, v in x.items()})
    if isinstance(x, list):
        return [_wrap(i) for i in x]
    return x


def load_config(path: str | None = None) -> Cfg:
    load_dotenv(ROOT / ".env")
    p = Path(path or os.getenv("SWARM_CONFIG") or ROOT / "config.yaml")
    with open(p, "r", encoding="utf-8") as f:
        cfg = _wrap(yaml.safe_load(f))
    if os.getenv("ANTHROPIC_MODEL"):
        cfg.llm.default_model = os.environ["ANTHROPIC_MODEL"]
    cfg.root = str(ROOT)
    return cfg
