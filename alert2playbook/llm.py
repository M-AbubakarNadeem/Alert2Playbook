"""Thin LLM layer: one structured call per agent, with token accounting.

Agents depend on the `StructuredLLM` protocol, not on Groq, so tests run the full
graph with a scripted stub and no network.
"""

from __future__ import annotations

import json
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
_KEYS = re.compile(r"\b(gsk|pcsk|lsv2|hf|sk|tgp_v1)_[A-Za-z0-9_\-]{8,}\b")
_DASH_KEYS = re.compile(r"\b(sk-ant|sk-or|sk-proj|sk|csk|xai)-[A-Za-z0-9_\-]{8,}")
_GOOGLE_KEYS = re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-.]{8,}", re.I)


def scrub_error(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Provider error messages can name the account (Groq includes the organisation
    ID). Remove account IDs and anything shaped like an API key before an error is
    shown to a visitor or saved anywhere. `secrets` are exact strings to remove as
    well, for keys that have no recognisable prefix."""
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    text = _ACCOUNT_IDS.sub(r"\1_<redacted>", text)
    text = _KEYS.sub(r"\1_<redacted>", text)
    text = _DASH_KEYS.sub(r"\1-<redacted>", text)
    text = _GOOGLE_KEYS.sub("AIza<redacted>", text)
    return _BEARER.sub(r"\1<redacted>", text)


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


# --------------------------------------------------------------------- visitor keys

# Paid and most free providers don't have Groq's tight per-minute output limit, and
# many of their models think before answering, so they get more room.
VISITOR_MAX_TOKENS = {"triage": 2000, "mapper": 1500, "writer": 6000, "critic": 2000}


class ProviderError(ValueError):
    """A problem with the visitor's provider. The message is safe to show."""


def _tool_for(schema: type[BaseModel]) -> tuple[dict, str]:
    tool = convert_to_openai_tool(schema)
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", schema.__name__).lower()
    tool["function"]["name"] = name
    return tool, name


def _json_block(text: str) -> str:
    """The first {...} object in a reply, ignoring ```json fences and chatter."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


class ProviderLLM:
    """Runs the agents on any OpenAI-compatible chat endpoint with a visitor's key.

    Models differ in what they accept, so each call steps down a ladder until one
    works: a forced call to the one tool, then "a tool call is required", then the
    model's own choice, then plain JSON in the reply. A step that works is
    remembered for the rest of the run. The key lives only in this object's HTTP
    client and is removed from every error message.
    """

    LADDER = ("forced", "required", "auto", "json")

    def __init__(self, provider, api_key: str, model: str):
        import httpx

        self.provider = provider
        self.model = model
        self.models = {role: model for role in ("triage", "mapper", "writer", "critic")}
        self.fallback_model = model
        self._secrets = (api_key,)
        self._caps = MAX_TOKENS if provider.id == "groq" else VISITOR_MAX_TOKENS
        self._opts: dict = {"step": 0}
        self._http = httpx.Client(
            base_url=provider.base_url,
            headers=provider.headers(api_key),
            timeout=httpx.Timeout(40.0, connect=6.0),
            follow_redirects=False,  # never let a redirect carry the key elsewhere
        )

    def __repr__(self) -> str:  # never show the key, even in a debugger or a log line
        return f"ProviderLLM({self.provider.id}, {self.model})"

    def close(self) -> None:
        self._http.close()

    # ---- one HTTP request
    def _body(self, role: str, tool: dict, name: str, messages: list[dict], step: str, schema) -> dict:
        body: dict = {"model": self.model, "messages": messages, "temperature": 0.1}
        if step == "json":
            hint = json.dumps(tool["function"]["parameters"])
            body["messages"] = [
                {**messages[0], "content": messages[0]["content"] + f"\n\nReply with one JSON object only, no other text. It must match this JSON schema:\n{hint}"},
                *messages[1:],
            ]
        else:
            body["tools"] = [tool]
            body["tool_choice"] = {"forced": {"type": "function", "function": {"name": name}}, "required": "required", "auto": "auto"}[step]
        cap = self._caps.get(role, 1500)
        body["max_completion_tokens" if self._opts.get("mct") or self.provider.id == "openai" else "max_tokens"] = cap
        if self._opts.get("no_temperature"):
            body.pop("temperature")
        if self.provider.id == "groq":
            body.update(_model_kwargs(self.model))
        return body

    def _post(self, body: dict):
        import httpx

        for attempt in range(3):
            try:
                r = self._http.post("chat/completions", json=body)
            except httpx.TimeoutException:
                raise ProviderError(f"{self.provider.label} took too long to answer. Try again, or pick a faster model.") from None
            except httpx.HTTPError:
                raise ProviderError(f"Couldn't reach {self.provider.label}. Try again in a moment.") from None
            if (r.status_code == 429 or r.status_code >= 500) and attempt < 2:
                try:
                    wait = float(r.headers.get("retry-after") or 2)
                except ValueError:
                    wait = 2.0
                time.sleep(min(max(wait, 0.5), 6.0))
                continue
            return r
        return r

    def _error_text(self, r) -> str:
        try:
            data = r.json()
            err = data.get("error") if isinstance(data, dict) else None
            msg = (err.get("message") if isinstance(err, dict) else err) or data.get("message") or ""
        except ValueError:
            msg = r.text
        return scrub_error(str(msg), self._secrets)[:240]

    def _parse(self, r, schema: type[T]) -> tuple[T | None, str, dict]:
        meta: dict = {}
        try:
            data = r.json()
            usage = data.get("usage") or {}
            meta = {"input_tokens": usage.get("prompt_tokens", 0) or 0, "output_tokens": usage.get("completion_tokens", 0) or 0}
            message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            raw = calls[0]["function"]["arguments"] if calls else _json_block(message.get("content") or "")
            parsed = schema.model_validate(raw) if isinstance(raw, dict) else schema.model_validate_json(raw)
            return parsed, "", meta
        except (ValidationError, ValueError, KeyError, IndexError, TypeError) as exc:
            return None, scrub_error(str(exc), self._secrets)[:300], meta

    def _attempt(self, role: str, schema: type[T], messages: list[dict]) -> tuple[T | None, str, dict]:
        tool, name = _tool_for(schema)
        label = self.provider.label
        while self._opts["step"] < len(self.LADDER):
            step = self.LADDER[self._opts["step"]]
            for _ in range(3):  # room to adapt to a rejected temperature or token setting
                r = self._post(self._body(role, tool, name, messages, step, schema))
                if r.status_code != 400:
                    break
                low = self._error_text(r).lower()
                if "temperature" in low and not self._opts.get("no_temperature"):
                    self._opts["no_temperature"] = True
                elif "max_tokens" in low and "max_completion_tokens" in low and not self._opts.get("mct"):
                    self._opts["mct"] = True
                else:
                    break
            if r.status_code == 200:
                return self._parse(r, schema)
            if r.status_code in (401, 403):
                raise ProviderError(f"{label} stopped accepting the key during the run.")
            if r.status_code == 404:
                raise ProviderError(f"{label} doesn't offer the model {self.model} to this key.")
            if r.status_code == 429:
                raise ProviderError(f"{label} is rate limiting this key. Wait a minute and try again.")
            if r.status_code == 400:
                self._opts["step"] += 1  # most likely this model can't do this kind of tool call; step down
                continue
            raise ProviderError(f"{label} returned an error ({r.status_code}): {self._error_text(r)}")
        raise ProviderError(f"The model {self.model} on {label} couldn't produce the structured answers this needs. Try a larger model.")

    def generate(self, role: str, schema: type[T], system: str, user: str) -> tuple[T, Usage]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        start = time.perf_counter()
        input_tokens = output_tokens = 0
        parsed, error = None, ""
        for attempt in range(2):
            msgs = messages if attempt == 0 else [*messages, {"role": "user", "content": f"Your previous answer was invalid ({error}). Answer again with every required field."}]
            parsed, error, meta = self._attempt(role, schema, msgs)
            input_tokens += meta.get("input_tokens", 0)
            output_tokens += meta.get("output_tokens", 0)
            if parsed is not None:
                break
        if parsed is None:
            raise ProviderError(f"The model {self.model} didn't return a valid {role} answer after a retry.")
        return parsed, Usage(model=self.model, ms=round((time.perf_counter() - start) * 1000), input_tokens=input_tokens, output_tokens=output_tokens)


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
