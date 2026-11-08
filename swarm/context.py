"""Shared blackboard passed between agents (one Ctx per symbol)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class AgentTrace:
    agent: str
    source: str          # llm | heuristic | heuristic-fallback | deterministic
    seconds: float
    note: str = ""


@dataclass
class Ctx:
    symbol: str
    inst: Any
    cfg: Any
    query: str
    demo: bool
    equity: float
    now: datetime
    world: dict                                  # shared: calendar, macro, headlines
    data: Any = None                             # MarketData
    smc: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    reports: dict = field(default_factory=dict)  # agent key -> pydantic model / dataclass
    trace: list = field(default_factory=list)
    lessons: str = ""
    account: dict = field(default_factory=dict)   # resolved account state (equity, currency, day/week P&L, drawdown) - see tools/accounts.resolve_account
    verdict: Any = None
    execution: Any = None
