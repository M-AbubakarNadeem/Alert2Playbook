"""Thin LLM layer: one structured call per agent, with token accounting.

Agents depend on the `StructuredLLM` protocol, not on Groq, so tests run the full
graph with a scripted stub and no network.
"""

from __future__ import annotations

import re
import time
from typing import Protocol, TypeVar

from groq import BadRequestError
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class Usage(dict):
    """{"model", "ms", "input_tokens", "output_tokens"} for one call."""


_ACCOUNT_IDS = re.compile(r"\b(org|proj|team|user)_[A-Za-z0-9]{8,}\b")
_KEYS = re.compile(r"\b(gsk|pcsk|lsv2|hf|sk)_[A-Za-z0-9_\-]{8,}\b")


def scrub_error(text: str) -> str:
    """Provider error messages can name the account (Groq includes the organisation
    ID). Remove account IDs and anything shaped like an API key before an error is
    shown to a visitor or saved to a results file."""
    return _KEYS.sub(r"\1_<redacted>", _ACCOUNT_IDS.sub(r"\1_<redacted>", text))


class StructuredLLM(Protocol):
    def generate(self, role: str, schema: type[T], system: str, user: str) -> tuple[T, Usage]: ...


# Output cap per agent. Groq rejects a request outright when its possible output
# exceeds the model's output-tokens-per-minute limit (1,000 for Qwen on the free
# tier), and without a cap the possible output is most of the context window.
# Observed outputs: triage ~200-350 tokens, mapper ~100, writer ~1,200-1,450,
# critic ~40-400 (reasoning tokens included).
MAX_TOKENS = {"triage": 800, "mapper": 600, "writer": 3000, "critic": 700}


def _model_kwargs(model: str) -> dict:
    # Reasoning models burn output tokens on hidden thinking. For extraction and
    # review a short reasoning budget is enough and keeps latency low.
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    if model.startswith("qwen/"):
        return {"reasoning_format": "hidden"}
    return {}


class GroqLLM:
    def __init__(self, api_key: str, models: dict[str, str], fallback_model: str, temperature: float = 0.1):
        from langchain_groq import ChatGroq

        def chat(model: str):
            # Free-tier per-minute limits are hit often; Groq says how long to wait
            # and the client honours it, so a few retries cost seconds, not failures.
            return ChatGroq(model=model, api_key=api_key, temperature=temperature, max_retries=5, timeout=60, **_model_kwargs(model))

        self.models = models
        self.fallback_model = fallback_model
        self._chats = {model: chat(model) for model in {*models.values(), fallback_model}}

    def _attempt(self, model: str, role: str, schema: type[T], messages: list) -> tuple[T | None, str, dict]:
        # The schema is offered as one forced tool. Its name is snake_case because
        # gpt-oss lower-cases tool names and Groq rejects a call to an unknown tool.
        tool = convert_to_openai_tool(schema)
        name = re.sub(r"(?<!^)(?=[A-Z])", "_", schema.__name__).lower()
        tool["function"]["name"] = name
        bound = self._chats[model].bind_tools(
            [tool], tool_choice={"type": "function", "function": {"name": name}}, max_tokens=MAX_TOKENS.get(role, 1000)
        )
        try:
            reply = bound.with_config(run_name=role, tags=["alert2playbook", role]).invoke(messages)
        except BadRequestError as exc:  # Groq rejects malformed tool calls with a 400
            return None, str(exc)[:400], {}
        meta = reply.usage_metadata or {}
        try:
            return schema.model_validate(reply.tool_calls[0]["args"]), "", meta
        except (IndexError, ValidationError) as exc:
            return None, str(exc)[:400], meta

    def generate(self, role: str, schema: type[T], system: str, user: str) -> tuple[T, Usage]:
        """Primary model, one corrective retry, then a stronger fallback model."""
        messages = [SystemMessage(system), HumanMessage(user)]
        primary = self.models[role]
        plan = [(primary, messages), (primary, None), (self.fallback_model if self.fallback_model != primary else primary, messages)]
        start = time.perf_counter()
        input_tokens = output_tokens = 0
        error = ""
        for model, msgs in plan:
            if msgs is None:
                msgs = [*messages, HumanMessage(f"Your previous answer was invalid ({error}). Call the tool again with every required field.")]
            parsed, error, meta = self._attempt(model, role, schema, msgs)
            input_tokens += meta.get("input_tokens", 0)
            output_tokens += meta.get("output_tokens", 0)
            if parsed is not None:
                break
        else:
            raise ValueError(f"{role}: no valid structured output after retry and fallback: {error}")

        usage = Usage(
            model=model,
            ms=round((time.perf_counter() - start) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        if model != primary:
            usage["fallback_from"] = primary
        return parsed, usage


def prompt_guard_score(api_key: str, model: str, text: str) -> float | None:
    """Probability that `text` contains a prompt-injection or jailbreak attempt,
    from Llama Prompt Guard 2. The classifier reads 512 tokens, so long alerts
    are scored in windows and the worst window counts."""
    from groq import Groq

    client = Groq(api_key=api_key, timeout=10, max_retries=1)
    windows = [text[i : i + 1800] for i in range(0, min(len(text), 7200), 1800)] or [""]
    worst = 0.0
    for window in windows:
        resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": window}])
        try:
            worst = max(worst, float(resp.choices[0].message.content.strip()))
        except (TypeError, ValueError):
            return None
    return worst
