"""Visitor keys: format checks, the provider check, what gets kept, and that a key
never leaks into an error. All provider traffic is faked, so no keys or network."""

from __future__ import annotations

import json

import httpx
import pytest

from alert2playbook import providers as P
from alert2playbook.llm import ProviderError, ProviderLLM, scrub_error
from alert2playbook.schemas import Review

GROQ_KEY = "gsk_" + "A1b2C3d4" * 6


# ---------------------------------------------------------------- format checks

def test_unknown_provider_is_refused():
    with pytest.raises(P.KeyError_):
        P.get_provider("my-own-server")
    with pytest.raises(P.KeyError_):
        P.get_provider("https://evil.example/v1")


def test_every_provider_address_is_fixed_https():
    for p in P.PROVIDERS.values():
        for url in (p.base_url, p.check, p.models):
            assert url.startswith("https://"), url


@pytest.mark.parametrize("bad", [
    GROQ_KEY + "\r\nX-Injected: 1",      # header injection
    GROQ_KEY + "\nHost: evil.example",
    "gsk_ abc def ghi jkl mno pqr stu",  # spaces
    'gsk_"quoted"0000000000000000',      # quotes
    "gsk_short",                         # too short
    "gsk_" + "a" * 400,                  # too long
    "gsk_/../../etc/passwd000000000",    # path characters
    "",
])
def test_malformed_keys_are_refused_before_any_network(bad):
    with pytest.raises(P.KeyError_):
        P.check_format(P.PROVIDERS["groq"], bad)


def test_key_must_match_the_chosen_provider():
    with pytest.raises(P.KeyError_, match="start with gsk_"):
        P.check_format(P.PROVIDERS["groq"], "sk-proj-" + "a" * 40)
    with pytest.raises(P.KeyError_, match="different provider"):
        P.check_format(P.PROVIDERS["openai"], "sk-ant-api03-" + "a" * 40)
    assert P.check_format(P.PROVIDERS["groq"], f"  {GROQ_KEY}  ") == GROQ_KEY


def test_model_names_are_checked():
    assert P.check_model("openai/gpt-oss-120b:free") == "openai/gpt-oss-120b:free"
    for bad in ["", "gpt 4", "x\r\ny", "a" * 200, "<script>"]:
        with pytest.raises(P.KeyError_):
            P.check_model(bad)


# ---------------------------------------------------------------- what is kept

def test_mask_keeps_only_the_ends():
    m = P.mask(GROQ_KEY)
    assert m.startswith("gsk_") and m.endswith(GROQ_KEY[-4:])
    assert GROQ_KEY[4:-4] not in m


def test_fingerprint_is_stable_and_depends_on_the_secret(monkeypatch):
    a = P.fingerprint(GROQ_KEY)
    assert a == P.fingerprint(GROQ_KEY) and len(a) == 12 and a not in GROQ_KEY
    monkeypatch.setenv("A2P_FINGERPRINT_SECRET", "another-secret-value")
    assert P.fingerprint(GROQ_KEY) != a


# ---------------------------------------------------------------- provider check

def fake_client(monkeypatch, handler):
    monkeypatch.setattr(P, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))


@pytest.mark.parametrize("status,ok", [(200, True), (401, False), (403, False), (400, False), (429, False), (500, False)])
def test_verify_key_trusts_only_a_200(monkeypatch, status, ok):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(status, json={"data": []})

    fake_client(monkeypatch, handler)
    if ok:
        P.verify_key(P.PROVIDERS["groq"], GROQ_KEY)
    else:
        with pytest.raises(P.KeyError_):
            P.verify_key(P.PROVIDERS["groq"], GROQ_KEY)
    assert seen["url"] == "https://api.groq.com/openai/v1/models"
    assert seen["auth"] == f"Bearer {GROQ_KEY}"


def test_a_redirect_is_never_followed(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    fake_client(monkeypatch, handler)
    with pytest.raises(P.KeyError_):
        P.verify_key(P.PROVIDERS["openai"], "sk-proj-" + "a" * 40)
    assert calls == ["https://api.openai.com/v1/models"]


def test_list_models_drops_non_chat_and_puts_defaults_first(monkeypatch):
    ids = ["whisper-large-v3", "meta-llama/llama-prompt-guard-2-86m", "zeta-chat", "llama-3.3-70b-versatile", "openai/gpt-oss-120b"]
    fake_client(monkeypatch, lambda r: httpx.Response(200, json={"data": [{"id": i} for i in ids]}))
    assert P.list_models(P.PROVIDERS["groq"], GROQ_KEY) == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "zeta-chat"]


# ---------------------------------------------------------------- errors never carry a key

def test_scrub_error_removes_every_key_shape():
    keys = [GROQ_KEY, "sk-proj-" + "x" * 30, "sk-ant-api03-" + "y" * 30, "sk-or-v1-" + "z" * 30,
            "AIza" + "q" * 35, "csk-" + "w" * 30, "xai-" + "e" * 30, "tgp_v1_" + "r" * 30]
    text = "failed: " + " ".join(keys) + " Authorization: Bearer abcdefghijklmnop"
    out = scrub_error(text)
    for k in keys:
        assert k not in out
    assert "abcdefghijklmnop" not in out


def test_scrub_error_removes_a_key_with_no_prefix():
    plain = "Q" * 40  # Cohere and Mistral keys have no fixed prefix
    assert plain not in scrub_error(f"bad key {plain}", (plain,))


# ---------------------------------------------------------------- running on a visitor's provider

def provider_llm(handler):
    llm = ProviderLLM(P.PROVIDERS["groq"], GROQ_KEY, "openai/gpt-oss-120b")
    llm._http = httpx.Client(base_url=llm.provider.base_url, transport=httpx.MockTransport(handler),
                             headers=llm.provider.headers(GROQ_KEY), follow_redirects=False)
    return llm


def tool_reply(args: dict) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"tool_calls": [{"function": {"name": "review", "arguments": json.dumps(args)}}]}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 7},
    })


def test_provider_llm_reads_a_tool_call():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return tool_reply({"verdict": "approve"})

    review, usage = provider_llm(handler).generate("critic", Review, "system", "user")
    assert review.verdict == "approve" and usage["input_tokens"] == 50
    assert sent[0]["tool_choice"] == {"type": "function", "function": {"name": "review"}}


def test_provider_llm_steps_down_when_tool_choice_is_refused():
    choices = []

    def handler(request):
        body = json.loads(request.content)
        choices.append(body.get("tool_choice", "json"))
        if isinstance(body.get("tool_choice"), dict):
            return httpx.Response(400, json={"error": {"message": "tool_choice object not supported"}})
        return tool_reply({"verdict": "approve"})

    review, _ = provider_llm(handler).generate("critic", Review, "s", "u")
    assert review.verdict == "approve"
    assert choices == [{"type": "function", "function": {"name": "review"}}, "required"]


def test_provider_llm_falls_back_to_plain_json():
    def handler(request):
        body = json.loads(request.content)
        if "tools" in body:
            return httpx.Response(400, json={"error": {"message": "tools are not supported for this model"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": 'Sure:\n```json\n{"verdict": "approve"}\n```'}}]})

    review, _ = provider_llm(handler).generate("critic", Review, "s", "u")
    assert review.verdict == "approve"


def test_provider_llm_drops_temperature_when_a_model_refuses_it():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append("temperature" in body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'temperature'"}})
        return tool_reply({"verdict": "approve"})

    provider_llm(handler).generate("critic", Review, "s", "u")
    assert seen == [True, False]


def test_provider_llm_errors_never_contain_the_key(monkeypatch):
    import alert2playbook.llm as llm_module

    monkeypatch.setattr(llm_module.time, "sleep", lambda s: None)  # skip the retry waits

    def handler(request):
        return httpx.Response(500, json={"error": {"message": f"internal error for key {GROQ_KEY}"}})

    with pytest.raises(ProviderError) as info:
        provider_llm(handler).generate("critic", Review, "s", "u")
    assert GROQ_KEY not in str(info.value)
    assert GROQ_KEY not in repr(provider_llm(handler))


def test_provider_llm_reports_a_revoked_key_plainly():
    with pytest.raises(ProviderError, match="stopped accepting"):
        provider_llm(lambda r: httpx.Response(401, json={})).generate("critic", Review, "s", "u")
