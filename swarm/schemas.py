"""Pydantic output contracts for every agent (validated JSON from the LLM)."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

Bias = Literal["BULLISH", "BEARISH", "NEUTRAL"]
Side = Literal["LONG", "SHORT", "NONE"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


# 2 - Data Fetcher
class DataReview(_M):
    quality_ok: bool = True
    issues: list[str] = []
    anomalies_noted: list[str] = []
    summary: str = ""


# 3 - News & Sentiment
class SentimentReport(_M):
    score: int = Field(0, ge=-10, le=10)
    freeze_trading: bool = False
    key_events: list[str] = []
    upcoming_risks: list[str] = []
    summary: str = ""


# 4 - Technical
class TechnicalReport(_M):
    bias: Bias = "NEUTRAL"
    htf_bias: Bias = "NEUTRAL"
    setup: Side = "NONE"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    entry_zone: Optional[list[float]] = None
    confidence: int = Field(0, ge=0, le=100)
    zone_type: Optional[str] = None      # OB | FVG | OTE | OTHER (the zone the entry is based on)
    key_levels: list[str] = []
    reasoning: list[str] = []
    invalidation: str = ""


# 5 - Macro
class MacroReport(_M):
    regime: Literal["RISK_ON", "RISK_OFF", "NEUTRAL"] = "NEUTRAL"
    asset_bias: Bias = "NEUTRAL"
    drivers: list[str] = []
    summary: str = ""


# 6 - Scanner
class ScanCandidate(_M):
    symbol: str
    score: int = Field(0, ge=0, le=100)
    signals: list[str] = []
    comment: str = ""


class ScannerReport(_M):
    candidates: list[ScanCandidate] = []
    market_notes: str = ""


# 7 - Inspector (LLM part: devil's advocate)
class InspectorAudit(_M):
    veto: bool = False
    false_signal_risk: int = Field(0, ge=0, le=100)
    concerns: list[str] = []
    summary: str = ""


# 8 - Social / on-chain
class SocialReport(_M):
    crowd_bias: Literal["EUPHORIC", "GREEDY", "NEUTRAL", "FEARFUL", "CAPITULATING"] = "NEUTRAL"
    contrarian_signal: Side = "NONE"
    strength: int = Field(0, ge=0, le=100)
    evidence: list[str] = []
    summary: str = ""


# 1 - Orchestrator final decision
class TradeProposal(_M):
    decision: Literal["TRADE", "WAIT", "NO_TRADE"] = "WAIT"
    symbol: str = ""
    direction: Side = "NONE"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    confidence: int = Field(0, ge=0, le=100)
    thesis: str = ""
    conflicts_resolved: list[str] = []
    invalidation: str = ""


class ExecutiveSummary(_M):
    headline: str = ""
    ranking: list[str] = []
    portfolio_risk_notes: list[str] = []
    bottom_line: str = ""


# 9 - Execution
class OrderDecision(_M):
    order_type: Literal["LIMIT", "MARKET", "STOP"] = "LIMIT"
    reason: str = ""


# 10 - Critic
class ParamFeedback(_M):
    parameter: str
    suggestion: str


class CriticReport(_M):
    trade_id: str = ""
    outcome: str = ""
    root_cause: Literal[
        "NORMAL_VARIANCE", "BAD_ENTRY", "BAD_STOP", "BAD_TARGET", "NEWS_SHOCK",
        "MACRO_MISREAD", "TECHNICAL_MISREAD", "SENTIMENT_MISREAD", "EXECUTION", "OTHER",
    ] = "OTHER"
    blamed_agents: list[str] = []
    lessons: list[str] = []
    parameter_feedback: list[ParamFeedback] = []


# 11 - Backtesting & Monte Carlo (LLM part: veto-only review)
class BacktestReview(_M):
    veto: bool = False
    overfit_risk: int = Field(0, ge=0, le=100)
    concerns: list[str] = []
    parameter_ideas: list[str] = []
    summary: str = ""


# 12 - Trade Validator (LLM part: veto-only review of the per-trade Monte Carlo numbers)
class ValidatorReview(_M):
    veto: bool = False
    concerns: list[str] = []
    summary: str = ""
