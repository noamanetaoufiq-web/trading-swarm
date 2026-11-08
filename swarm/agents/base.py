"""BaseAgent: every agent has a role prompt + output schema + a deterministic heuristic fallback."""
from __future__ import annotations
import json
import logging
import time

from ..context import AgentTrace

log = logging.getLogger("swarm.agent")

PREAMBLE = """You are ONE specialised agent inside a 12-agent autonomous trading research pipeline (paper trading by default, human supervised).
Rules:
- Use ONLY the data given in the user message (plus web search results if the web_search tool is available). Never invent prices, levels, headlines, statistics or events. If data is missing or looks stale, say so in your output and lower your confidence.
- Be concise, numeric and specific. No filler, no disclaimers inside fields.
- Your reply is machine-parsed: output exactly ONE JSON object matching the requested schema and nothing else.
- You produce analysis, not financial advice."""


def dumps(o) -> str:
    return json.dumps(o, default=str, ensure_ascii=False, separators=(",", ":"))


class BaseAgent:
    key = "base"
    number = 0
    name = "Base"
    emoji = ""
    system_prompt = ""
    schema = None
    web_search = False

    def __init__(self, cfg, llm):
        self.cfg = cfg
        self.llm = llm

    def system(self) -> str:
        return PREAMBLE + "\n\n" + self.system_prompt

    def user_prompt(self, ctx) -> str:
        raise NotImplementedError

    def heuristic(self, ctx):
        raise NotImplementedError

    def run(self, ctx):
        t0, note = time.time(), ""
        if self.llm.is_mock:
            out, src = self.heuristic(ctx), "heuristic"
        else:
            try:
                out = self.llm.complete(self.key, self.system(), self.user_prompt(ctx), self.schema,
                                        web_search=bool(self.web_search and self.cfg.llm.web_search))
                src = "llm"
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] LLM failed (%s) -> heuristic fallback", self.key, e)
                out, src, note = self.heuristic(ctx), "heuristic-fallback", str(e)[:200]
        ctx.trace.append(AgentTrace(self.key, src, round(time.time() - t0, 2), note))
        return out
