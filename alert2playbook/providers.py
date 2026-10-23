"""Model providers a visitor can bring their own API key for.

Every provider here has a fixed address in this file. A visitor picks a provider by
its id and never supplies a URL, so the server only ever sends a key to the company
that issued it. A key is used in memory for one request and is never written down.

Checks, in order:
  1. the provider id is on this list
  2. the key is a plain token (letters, digits, - _ .) of a sane length, so it can't
     smuggle anything into an HTTP header
  3. the key looks like that provider's keys, where the provider has a known prefix
  4. the provider itself accepts the key (one cheap request that costs no tokens)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from typing import Callable

import httpx

# A key is a plain token. No spaces, line breaks, quotes, slashes or colons, which
# is what stops a key from being used to inject extra HTTP headers.
KEY_CHARSET = re.compile(r"^[A-Za-z0-9_\-.]{20,256}$")
MODEL_ID = re.compile(r"^[A-Za-z0-9_\-.:/@]{1,120}$")
CHECK_TIMEOUT = httpx.Timeout(8.0, connect=5.0)

# Model ids that are clearly not chat models.
_NOT_CHAT = re.compile(
    r"embed|whisper|tts|transcri|audio|realtime|dall-e|image|moderation|guard|rerank|speech|playai|search|davinci|babbage|computer-use|sora|vision-preview",
    re.I,
)


class KeyError_(ValueError):
    """A visitor-facing problem with a key. The message is safe to show."""


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str                      # OpenAI-compatible chat endpoint root
    signup_url: str
    free_tier: bool
    key_prefix: tuple[str, ...] = ()   # known prefixes; empty means no fixed prefix
    not_prefix: tuple[str, ...] = ()   # prefixes that belong to a different provider
    preferred: tuple[str, ...] = ()    # default model order, first one available wins
    check_url: str = ""                # where to confirm the key; defaults to {base}/models
    models_url: str = ""               # where to list models; defaults to check_url
    models_need_key: bool = True
    has_guard: bool = False            # Llama Prompt Guard 2 is only on Groq
    parse: Callable[[object], list[str]] = field(default=lambda data: [m["id"] for m in data.get("data", [])], compare=False)

    def headers(self, key: str) -> dict[str, str]:
        h = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        if self.id == "anthropic":
            h.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        return h

    @property
    def check(self) -> str:
        return self.check_url or f"{self.base_url}/models"

    @property
    def models(self) -> str:
        return self.models_url or self.check


def _openrouter_models(data) -> list[str]:
    out = []
    for m in data.get("data", []):
        params = m.get("supported_parameters") or []
        if "tools" in params:
            out.append(m["id"])
    # Free models first, since that is what most visitors will have.
    return sorted(out, key=lambda i: (not i.endswith(":free"), i))


def _mistral_models(data) -> list[str]:
    return [m["id"] for m in data.get("data", []) if (m.get("capabilities") or {}).get("function_calling")]


def _cohere_models(data) -> list[str]:
    return [m["name"] for m in data.get("models", []) if "chat" in (m.get("endpoints") or [])]


def _gemini_models(data) -> list[str]:
    return [m["id"].removeprefix("models/") for m in data.get("data", []) if "gemini" in m.get("id", "")]


def _together_models(data) -> list[str]:
    items = data if isinstance(data, list) else data.get("data", [])
    return [m["id"] for m in items if m.get("type") == "chat"]


PROVIDERS: dict[str, Provider] = {p.id: p for p in [
    Provider("groq", "Groq", "https://api.groq.com/openai/v1", "https://console.groq.com/keys", True,
             key_prefix=("gsk_",), has_guard=True,
             preferred=("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3.8-27b", "openai/gpt-oss-20b")),
    Provider("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "https://aistudio.google.com/apikey", True,
             key_prefix=("AIza",), parse=_gemini_models,
             preferred=("gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro")),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "https://openrouter.ai/keys", True,
             key_prefix=("sk-or-",), check_url="https://openrouter.ai/api/v1/key",
             models_url="https://openrouter.ai/api/v1/models", models_need_key=False, parse=_openrouter_models,
             preferred=("openai/gpt-oss-120b:free", "meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-4.1-mini")),
    Provider("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "https://cloud.cerebras.ai", True,
             key_prefix=("csk-",), preferred=("gpt-oss-120b", "llama-3.3-70b", "qwen-3-32b")),
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1", "https://console.mistral.ai/api-keys", True,
             parse=_mistral_models, preferred=("mistral-medium-latest", "mistral-small-latest", "mistral-large-latest")),
    Provider("cohere", "Cohere", "https://api.cohere.ai/compatibility/v1", "https://dashboard.cohere.com/api-keys", True,
             check_url="https://api.cohere.com/v1/models?endpoint=chat", parse=_cohere_models,
             preferred=("command-a-03-2025", "command-r-plus", "command-r")),
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "https://platform.openai.com/api-keys", False,
             key_prefix=("sk-",), not_prefix=("sk-ant-", "sk-or-"),
             preferred=("gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1", "gpt-4o")),
    Provider("anthropic", "Anthropic", "https://api.anthropic.com/v1", "https://console.anthropic.com/settings/keys", False,
             key_prefix=("sk-ant-",), preferred=("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3-5-haiku-latest")),
    Provider("deepseek", "DeepSeek", "https://api.deepseek.com", "https://platform.deepseek.com/api_keys", False,
             key_prefix=("sk-",), not_prefix=("sk-ant-", "sk-or-"), preferred=("deepseek-chat",)),
    Provider("xai", "xAI (Grok)", "https://api.x.ai/v1", "https://console.x.ai", False,
             key_prefix=("xai-",), preferred=("grok-3-mini", "grok-3", "grok-4")),
    Provider("together", "Together AI", "https://api.together.xyz/v1", "https://api.together.ai/settings/api-keys", False,
             parse=_together_models, preferred=("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo")),
]}


def public_list() -> list[dict]:
    """What the page needs to draw the provider menu. No secrets here."""
    return [{"id": p.id, "label": p.label, "free_tier": p.free_tier, "signup_url": p.signup_url} for p in PROVIDERS.values()]


def get_provider(provider_id: str) -> Provider:
    p = PROVIDERS.get(str(provider_id or "").strip().lower())
    if not p:
        raise KeyError_("That provider isn't supported.")
    return p


def check_format(provider: Provider, key: str) -> str:
    """Steps 1 to 3. Returns the cleaned key or raises KeyError_."""
    key = (key or "").strip()
    if not KEY_CHARSET.fullmatch(key):
        raise KeyError_("That doesn't look like an API key. Paste the key on its own, with nothing around it.")
    if provider.not_prefix and key.startswith(provider.not_prefix):
        raise KeyError_(f"That looks like a key from a different provider, not {provider.label}.")
    if provider.key_prefix and not key.startswith(provider.key_prefix):
        raise KeyError_(f"{provider.label} keys start with {' or '.join(provider.key_prefix)}. Check you picked the right provider.")
    return key


def check_model(model: str) -> str:
    model = (model or "").strip()
    if not MODEL_ID.fullmatch(model):
        raise KeyError_("That model name isn't valid.")
    return model


def _client() -> httpx.Client:
    # follow_redirects stays off: a redirect could send the key somewhere else.
    return httpx.Client(timeout=CHECK_TIMEOUT, follow_redirects=False)


def verify_key(provider: Provider, key: str) -> None:
    """Step 4: ask the provider whether it accepts the key. Costs no tokens."""
    try:
        with _client() as c:
            r = c.get(provider.check, headers=provider.headers(key))
    except httpx.HTTPError:
        raise KeyError_(f"Couldn't reach {provider.label} to check the key. Try again in a moment.") from None
    # 400 counts too: Gemini and xAI answer a bad key with 400, and a plain request
    # for the model list has nothing else that could be wrong.
    if r.status_code in (400, 401, 403):
        raise KeyError_(f"{provider.label} didn't accept that key. Check it was copied in full and hasn't been revoked.")
    if r.status_code == 429:
        raise KeyError_(f"{provider.label} is rate limiting this key right now. Wait a minute and try again.")
    if r.status_code != 200:
        raise KeyError_(f"{provider.label} answered with an error ({r.status_code}) while checking the key.")


def list_models(provider: Provider, key: str) -> list[str]:
    """Chat models this key can use, preferred ones first. Assumes the key is verified."""
    try:
        with _client() as c:
            r = c.get(provider.models, headers=provider.headers(key) if provider.models_need_key else {"Accept": "application/json"})
        r.raise_for_status()
        ids = provider.parse(r.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        ids = []
    ids = [i for i in dict.fromkeys(ids) if i and MODEL_ID.fullmatch(i) and not _NOT_CHAT.search(i)]
    preferred = [m for m in provider.preferred if m in ids]
    rest = sorted(set(ids) - set(preferred))
    out = preferred + rest
    # Some providers don't list models with this key; offer the known-good defaults.
    return out[:150] or list(provider.preferred)


# --------------------------------------------------------------------- what we keep
# The key itself is never stored. To tell keys apart on the dashboard we keep a
# masked form and a fingerprint: an HMAC with a server secret, so the fingerprint
# can't be turned back into the key or matched against a list of known keys.

def _secret() -> bytes:
    s = os.environ.get("A2P_FINGERPRINT_SECRET", "")
    return s.encode() if s else b"local-dev-only-not-secret"


def fingerprint(key: str) -> str:
    return hmac.new(_secret(), key.encode(), hashlib.sha256).hexdigest()[:12]


def mask(key: str) -> str:
    """First 4 and last 4 characters, the way providers show keys in their own consoles."""
    return f"{key[:4]}{'•' * 8}{key[-4:]}" if len(key) >= 12 else "•" * 8
