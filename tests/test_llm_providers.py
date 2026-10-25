"""Provider selection, Gemini endpoint routing, error classification and fallback.
No network: the SDK calls are replaced with fakes. Keys below are fake."""
import pytest

import agent_base
import llm_providers as lp

FAKE_VERTEX_KEY = "AQ.FAKEFAKEFAKEFAKEFAKEFAKE"
FAKE_PUBLIC_KEY = "AIzaFAKEFAKEFAKEFAKEFAKE00"


class FakeGoogleError(Exception):
    """Shape of google.genai.errors.ClientError (code + details)."""
    def __init__(self, code, reason=None, status="PERMISSION_DENIED"):
        super().__init__(f"{code} {status}")
        self.code = code
        err = {"code": code, "status": status, "message": f"blocked key={FAKE_VERTEX_KEY}"}
        if reason:
            err["details"] = [{"reason": reason, "metadata": {"consumer": "projects/123"}}]
        self.details = {"error": err}


class FakeAnthropicError(Exception):
    def __init__(self, status_code, etype):
        super().__init__(etype)
        self.status_code = status_code
        self.body = {"type": "error", "error": {"type": etype}}


class APITimeoutError(Exception):
    pass


@pytest.fixture(autouse=True)
def env(monkeypatch):
    lp.reset_state()
    for k in ("LLM_ENABLED", "LLM_PROVIDER_ORDER", "GEMINI_BACKEND", "GEMINI_MODEL", "CLAUDE_MODEL",
              "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_VERTEX_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKEFAKEFAKEFAKE")
    yield
    lp.reset_state()


@pytest.fixture
def calls(monkeypatch):
    """Record SDK calls; behaviour per (provider, backend) set via the returned dict."""
    log, behaviour = [], {}

    def gemini(prompt, temperature, max_tokens, cfg, backend):
        log.append(("gemini", backend))
        b = behaviour.get(("gemini", backend), "ok")
        if isinstance(b, Exception):
            raise b
        return f"gemini-{backend}"

    def claude(prompt, temperature, max_tokens, cfg):
        log.append(("claude", None))
        b = behaviour.get(("claude", None), "ok")
        if isinstance(b, Exception):
            raise b
        return "claude"

    monkeypatch.setattr(lp, "_gemini_call", gemini)
    monkeypatch.setattr(lp, "_claude_call", claude)
    return log, behaviour


# ── Configuration ────────────────────────────────────────────────────────────

def test_default_config():
    cfg = lp.load_config()
    assert cfg.enabled and cfg.order == ["gemini", "claude"] and cfg.gemini_backend == "auto"
    assert cfg.gemini_model == lp.DEFAULT_GEMINI_MODEL and cfg.claude_model == lp.DEFAULT_CLAUDE_MODEL


def test_order_and_invalid_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "claude, gpt, gemini")
    monkeypatch.setenv("GEMINI_BACKEND", "moon")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "abc")
    cfg = lp.load_config()
    assert cfg.order == ["claude", "gemini"] and cfg.gemini_backend == "auto" and cfg.timeout_s == 30
    assert len(cfg.invalid_settings) == 3


def test_quoted_key_is_cleaned(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", f'  "{FAKE_PUBLIC_KEY}" ')
    assert lp.load_config().gemini_key == FAKE_PUBLIC_KEY


def test_config_repr_hides_keys():
    assert "FAKE" not in repr(lp.load_config())


# ── Selection and fallback ───────────────────────────────────────────────────

def test_primary_provider_answers(calls):
    log, _ = calls
    r = lp.generate("hi")
    assert r.ok and r.provider == "gemini" and r.backend == "vertex" and log == [("gemini", "vertex")]


def test_vertex_blocked_falls_back_to_public_endpoint_and_is_remembered(calls):
    log, behaviour = calls
    behaviour[("gemini", "vertex")] = FakeGoogleError(403, "API_KEY_SERVICE_BLOCKED")
    r = lp.generate("hi")
    assert r.ok and r.provider == "gemini" and r.backend == "public"
    assert r.notes and "API_KEY_SERVICE_BLOCKED" in r.notes[0]
    assert log == [("gemini", "vertex"), ("gemini", "public")]
    log.clear()
    lp.generate("again")
    assert log == [("gemini", "public")]          # auto-resolved backend cached
    assert lp.status()["providers"][0]["backend"] == "public"


def test_public_keys_never_try_vertex(calls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_PUBLIC_KEY)
    log, _ = calls
    assert lp.generate("hi").backend == "public" and log == [("gemini", "public")]


def test_explicit_backend_is_respected(calls, monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "vertex")
    log, behaviour = calls
    behaviour[("gemini", "vertex")] = FakeGoogleError(403, "API_KEY_SERVICE_BLOCKED")
    r = lp.generate("hi")
    assert r.provider == "claude"                 # no silent switch to public when pinned
    assert ("gemini", "public") not in log
    assert r.attempts[0]["diagnostic"]["code"] == "API_KEY_SERVICE_BLOCKED"


def test_falls_back_to_claude_with_diagnostic(calls):
    _, behaviour = calls
    behaviour[("gemini", "vertex")] = FakeGoogleError(404, status="NOT_FOUND")
    r = lp.generate("hi")
    assert r.ok and r.provider == "claude"
    d = r.attempts[0]["diagnostic"]
    assert d["code"] == "MODEL_NOT_FOUND" and d["provider"] == "gemini" and "GEMINI_MODEL" in d["hint"]
    assert lp.status()["providers"][0]["last"]["ok"] is False


def test_order_can_put_claude_first(calls, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "claude,gemini")
    log, _ = calls
    assert lp.generate("hi").provider == "claude" and log == [("claude", None)]


def test_missing_key_is_reported_not_called(calls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    log, _ = calls
    r = lp.generate("hi")
    assert r.provider == "claude" and r.attempts[0]["diagnostic"]["code"] == "NOT_CONFIGURED"
    assert log == [("claude", None)]


def test_all_fail(calls):
    _, behaviour = calls
    behaviour[("gemini", "vertex")] = FakeGoogleError(403, "API_KEY_SERVICE_BLOCKED")
    behaviour[("gemini", "public")] = FakeGoogleError(429, status="RESOURCE_EXHAUSTED")
    behaviour[("claude", None)] = FakeAnthropicError(401, "authentication_error")
    r = lp.generate("hi")
    assert not r.ok and [a["diagnostic"]["code"] for a in r.attempts] == ["RATE_LIMITED", "INVALID_KEY"]


def test_disabled(calls, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    log, _ = calls
    r = lp.generate("hi")
    assert not r.ok and r.attempts[0]["diagnostic"]["code"] == "DISABLED" and log == []


def test_agent_call_llm_keeps_string_contract(calls, monkeypatch):
    _, behaviour = calls
    assert agent_base.call_llm("hi", provider="claude") == "claude"
    behaviour[("gemini", "vertex")] = FakeGoogleError(403, "SERVICE_DISABLED")
    behaviour[("gemini", "public")] = FakeGoogleError(403, "SERVICE_DISABLED")
    behaviour[("claude", None)] = FakeAnthropicError(529, "overloaded_error")
    lp.reset_state()
    out = agent_base.call_llm("hi")
    assert out.startswith("ERROR:") and "SERVICE_DISABLED" in out and "PROVIDER_UNAVAILABLE" in out


def test_check_probes_each_provider_with_fixed_prompt(calls, monkeypatch):
    prompts = []
    monkeypatch.setattr(lp, "_claude_call", lambda p, *a: prompts.append(p) or "OK")
    res = lp.check()
    assert [r["provider"] for r in res] == ["gemini", "claude"] and all(r["ok"] for r in res)
    assert prompts == [lp.CHECK_PROMPT]


# ── Classification and redaction ─────────────────────────────────────────────

@pytest.mark.parametrize("exc,code", [
    (FakeGoogleError(403, "API_KEY_SERVICE_BLOCKED"), "API_KEY_SERVICE_BLOCKED"),
    (FakeGoogleError(403, "SERVICE_DISABLED"), "SERVICE_DISABLED"),
    (FakeGoogleError(400, "API_KEY_INVALID", "INVALID_ARGUMENT"), "INVALID_KEY"),
    (FakeGoogleError(403, "BILLING_DISABLED"), "BILLING_DISABLED"),
    (FakeGoogleError(403), "PERMISSION_DENIED"),
    (FakeGoogleError(404, status="NOT_FOUND"), "MODEL_NOT_FOUND"),
    (FakeGoogleError(503, status="UNAVAILABLE"), "PROVIDER_UNAVAILABLE"),
    (APITimeoutError("t"), "TIMEOUT"),
])
def test_classify_gemini(exc, code):
    d = lp.classify_error("gemini", exc, "m", "vertex")
    assert d.code == code
    assert "FAKE" not in str(d.to_dict()) and "projects/123" not in str(d.to_dict())


def test_empty_response_reports_finish_reason(calls):
    _, behaviour = calls
    behaviour[("gemini", "vertex")] = lp.EmptyResponse("FinishReason.MAX_TOKENS")
    r = lp.generate("hi")
    d = r.attempts[0]["diagnostic"]
    assert r.provider == "claude" and d["code"] == "EMPTY_RESPONSE" and "MAX_TOKENS" in d["message"]
    assert "output" in d["hint"].lower()


def test_gemini_output_budget_has_floor(monkeypatch):
    seen = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen.update(config)
            return type("R", (), {"text": "OK", "candidates": []})()

    class FakeClient:
        def __init__(self, **kw):
            self.models = FakeModels()

    import google.genai
    monkeypatch.setattr(google.genai, "Client", FakeClient)
    assert lp._gemini_call("hi", 0, 10, lp.load_config(), "public") == "OK"
    assert seen["max_output_tokens"] == lp.GEMINI_MIN_OUTPUT_TOKENS


def test_blocked_diagnostic_names_service_and_remedy():
    d = lp.classify_error("gemini", FakeGoogleError(403, "API_KEY_SERVICE_BLOCKED"), "m", "vertex")
    assert d.service == "aiplatform.googleapis.com" and "GEMINI_BACKEND=public" in d.hint


@pytest.mark.parametrize("exc,code", [
    (FakeAnthropicError(401, "authentication_error"), "INVALID_KEY"),
    (FakeAnthropicError(403, "permission_error"), "PERMISSION_DENIED"),
    (FakeAnthropicError(404, "not_found_error"), "MODEL_NOT_FOUND"),
    (FakeAnthropicError(429, "rate_limit_error"), "RATE_LIMITED"),
])
def test_classify_claude(exc, code):
    assert lp.classify_error("claude", exc, "m").code == code


def test_redact():
    s = f"key {FAKE_VERTEX_KEY} and {FAKE_PUBLIC_KEY} and sk-ant-abcdefghijklmnop"
    assert lp.redact(s) == "key <redacted> and <redacted> and <redacted>"
