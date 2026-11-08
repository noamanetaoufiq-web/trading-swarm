from .orchestrator import OrchestratorAgent
from .data_fetcher import DataFetcherAgent
from .news_sentiment import NewsSentimentAgent
from .technical import TechnicalAgent
from .macro import MacroAgent
from .scanner import ScannerAgent
from .inspector import InspectorAgent
from .social_onchain import SocialOnChainAgent
from .execution import ExecutionAgent
from .critic import CriticAgent
from .backtester import BacktesterAgent
from .trade_validator import TradeValidatorAgent

ALL = [OrchestratorAgent, DataFetcherAgent, NewsSentimentAgent, TechnicalAgent, MacroAgent,
       ScannerAgent, InspectorAgent, SocialOnChainAgent, ExecutionAgent, CriticAgent, BacktesterAgent, TradeValidatorAgent]


def build_agents(cfg, llm) -> dict:
    return {cls.key: cls(cfg, llm) for cls in ALL}
