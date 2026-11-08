"""Run -> JSON-safe dict for the dashboard (and persisted under data/runs/)."""
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path

from .tools.news_feeds import event_to_dict

CHART_TFS = ("15m", "1h", "4h")
BARS = 120


def _dump(o):
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    return o


def _series(c):
    out = {}
    for tf in CHART_TFS:
        df = c.data.frames.get(tf) if c.data else None
        if df is None or len(df) < 20:
            continue
        tail = df.tail(BARS)
        smc = c.smc.get(tf, {})
        out[tf] = {"offset": len(df) - len(tail),
                   "candles": [[t.strftime("%m-%d %H:%M"), round(float(r.open), 4), round(float(r.high), 4), round(float(r.low), 4), round(float(r.close), 4)]
                               for t, r in tail.iterrows()],
                   "fvg": smc.get("fvg", []), "order_blocks": smc.get("order_blocks", []),
                   "liquidity_pools": smc.get("liquidity_pools", []), "sweeps": smc.get("sweeps", []),
                   "structure": smc.get("structure", {}), "premium_discount": smc.get("premium_discount")}
    return out


def run_to_dict(run: dict) -> dict:
    syms = {}
    for s, c in run["ctxs"].items():
        syms[s] = {
            "reports": {k: _dump(v) for k, v in c.reports.items()},
            "verdict": c.verdict.to_dict() if c.verdict else None,
            "execution": c.execution,
            "trace": [asdict(t) for t in c.trace],
            "market": ({"last_price": c.data.last_price, "spread": c.data.spread, "atr": c.data.atr, "levels": c.data.levels,
                        "quality": c.data.quality} if c.data else None),
            "series": _series(c) if c.data else {},
            "asset_class": c.inst.asset_class,
        }
    w = run["world"]
    return {"id": run["stamp"] + ("_demo" if run["demo"] else ""), "ts": run["ts"].isoformat(timespec="seconds"), "demo": run["demo"],
            "symbols": run["symbols"], "summary": run["summary"].model_dump(), "items": run["items"], "usage": run["usage"],
            "journal_stats": run["journal_stats"], "lessons": run["lessons"], "plan": run["plan"],
            "scan": run["scan"].model_dump() if run["scan"] else None, "scan_trace": [asdict(t) for t in run["scan_trace"]],
            "account": {k: v for k, v in (run.get("account") or {}).items() if k != "label"},
            "world": {"macro": w["macro"], "calendar": [event_to_dict(e) for e in w["calendar"][:25]]},
            "detail": syms}


def save_run(root, d: dict):
    p = Path(root) / "data" / "runs"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{d['id']}.json").write_text(json.dumps(d, default=str), encoding="utf-8")


def list_runs(root) -> list[dict]:
    p = Path(root) / "data" / "runs"
    if not p.exists():
        return []
    out = []
    for f in sorted(p.glob("*.json"), reverse=True)[:40]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d["id"], "ts": d["ts"], "demo": d["demo"], "symbols": d["symbols"],
                        "approved": sum(1 for i in d["items"] if i["approved"])})
        except Exception:  # noqa: BLE001
            continue
    return out


def load_run(root, run_id: str):
    p = Path(root) / "data" / "runs" / f"{run_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
