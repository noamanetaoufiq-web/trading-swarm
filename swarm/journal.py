"""JSONL trade journal + lessons store (the Critic's memory). Paper trades are reviewed against later candles."""
from __future__ import annotations
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class Journal:
    def __init__(self, root: str, demo: bool = False):
        suffix = "_demo" if demo else ""
        self.path = Path(root) / "data" / f"journal{suffix}.jsonl"
        self.lessons_path = Path(root) / "data" / f"lessons{suffix}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- raw io
    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def append(self, rec: dict):
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")

    def update(self, trade_id: str, patch: dict):
        with self._lock:
            rows = self.all()
            for r in rows:
                if r["id"] == trade_id:
                    r.update(patch)
            with open(self.path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")

    # ---- exposure / pnl (used by the Inspector)
    def open_risk_pct(self) -> float:
        return round(sum(r.get("risk_pct", 0) for r in self.all() if r.get("status") in ("PENDING", "OPEN")), 3)

    def today_pnl_pct(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        pnl = 0.0
        for r in self.all():
            o = r.get("outcome") or {}
            if r.get("status") == "CLOSED" and str(o.get("closed_at", ""))[:10] == today:
                pnl += o.get("r_multiple", 0) * r.get("risk_pct", 0)
        return round(pnl, 3)

    def realized_usd(self) -> float:
        """Realised P&L of closed paper/live trades in USD (R-multiple x the dollars risked at entry). Feeds the paper-simulator equity."""
        tot = 0.0
        for r in self.all():
            o = r.get("outcome") or {}
            if r.get("status") == "CLOSED":
                tot += float(o.get("r_multiple", 0)) * float((r.get("verdict") or {}).get("risk_usd", 0) or 0)
        return round(tot, 2)

    # ---- stats
    def stats(self) -> dict:
        closed = [r for r in self.all() if r.get("status") == "CLOSED"]
        if not closed:
            return {"closed_trades": 0}
        rs = [r["outcome"]["r_multiple"] for r in closed]
        wins = [x for x in rs if x > 0]
        return {"closed_trades": len(rs), "win_rate_pct": round(len(wins) / len(rs) * 100, 1),
                "avg_r": round(sum(rs) / len(rs), 2), "total_r": round(sum(rs), 2),
                "expectancy_r": round(sum(rs) / len(rs), 2)}

    # ---- lessons (Critic output fed back to Orchestrator + Technical prompts)
    def add_lessons(self, rec: dict):
        with open(self.lessons_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")

    def recent_lessons(self, n: int = 8) -> str:
        if not self.lessons_path.exists():
            return ""
        rows = [json.loads(l) for l in open(self.lessons_path, encoding="utf-8") if l.strip()][-n:]
        lines = []
        for r in rows:
            for l in r.get("lessons", []):
                lines.append(f"- [{r.get('symbol', '?')}/{r.get('root_cause', '?')}] {l}")
        return "\n".join(lines[-n:])
