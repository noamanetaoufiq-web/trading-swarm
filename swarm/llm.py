"""Thin Anthropic wrapper: JSON-validated completions, retries, optional web search, usage counters."""
from __future__ import annotations
import json
import logging
import threading
import time
from typing import Type

from pydantic import BaseModel, ValidationError

log = logging.getLogger("swarm.llm")


def extract_json(text: str):
    """Return the LAST balanced, parseable JSON object found in text (or None)."""
    best, i, n = None, 0, len(text)
    while i < n:
        if text[i] == "{":
            depth, in_str, esc = 0, False, False
            for j in range(i, n):
                c = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                else:
                    if c == '"':
                        in_str = True
                    elif c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                best = json.loads(text[i:j + 1])
                                i = j
                            except Exception:
                                pass
                            break
        i += 1
    return best


class LLM:
    def __init__(self, cfg, mock: bool = False):
        self.cfg = cfg
        self.is_mock = mock or cfg.llm.backend == "mock"
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self._lock = threading.Lock()
        self.client = None
        if not self.is_mock:
            import anthropic  # lazy: demo mode works without the package/key
            self._anthropic = anthropic
            self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    def model_for(self, agent_key: str) -> str:
        return self.cfg.llm.models.get(agent_key, self.cfg.llm.default_model)

    def _call(self, model, system, user, max_tokens, web_search):
        a = self._anthropic
        kwargs = dict(model=model, max_tokens=max_tokens, system=system)
        if web_search:
            kwargs["tools"] = [{
                "type": "web_search_20250305", "name": "web_search",
                "max_uses": int(self.cfg.llm.web_search_max_uses),
            }]
        messages = [{"role": "user", "content": user}]
        last_err = None
        for attempt in range(4):
            try:
                hops = 0
                while True:
                    resp = self.client.messages.create(messages=messages, **kwargs)
                    with self._lock:
                        self.usage["calls"] += 1
                        self.usage["input_tokens"] += getattr(resp.usage, "input_tokens", 0) or 0
                        self.usage["output_tokens"] += getattr(resp.usage, "output_tokens", 0) or 0
                    if resp.stop_reason == "pause_turn" and hops < 3:
                        messages = messages + [{"role": "assistant", "content": resp.content}]
                        hops += 1
                        continue
                    break
                return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            except (a.APIConnectionError, a.RateLimitError, a.InternalServerError) as e:
                last_err = e
                time.sleep(2 * (2 ** attempt))
        raise RuntimeError(f"Anthropic API failed after retries: {last_err}")

    def complete(self, agent_key: str, system: str, user: str, schema: Type[BaseModel],
                 web_search: bool = False) -> BaseModel:
        model = self.model_for(agent_key)
        schema_txt = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        prompt = (f"{user}\n\nRespond with ONLY one JSON object (no markdown fences, no prose) "
                  f"that validates against this JSON Schema:\n{schema_txt}")
        text = self._call(model, system, prompt, int(self.cfg.llm.max_tokens), web_search)
        for attempt in range(2):
            data = extract_json(text)
            try:
                return schema.model_validate(data)
            except (ValidationError, TypeError) as e:
                if attempt == 1:
                    raise
                log.info("[%s] invalid JSON, retrying once: %s", agent_key, str(e)[:200])
                text = self._call(model, system,
                                  prompt + f"\n\nYour previous answer was invalid ({str(e)[:400]}). "
                                           "Return corrected JSON only.",
                                  int(self.cfg.llm.max_tokens), False)
