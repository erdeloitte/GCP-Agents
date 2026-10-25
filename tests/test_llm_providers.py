"""Claude provider: configuration, error classification and graceful failure.
No network: the SDK call is replaced with a fake. Keys below are fake."""
import pytest

import agent_base
import llm_providers as lp

FAKE_KEY = "sk-ant-FAKEFAKEFAKEFAKE"


class FakeAnthropicError(Exception):
    def __init__(self, status_code, etype):
        super().__init__(etype)
        self.status_code = status_code
        self.body = {"type": "error", "error": {"type": etype}}


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


@pytest.fixture(autouse=True)
def env(monkeypatch):
    lp.reset_state()
    for k in ("LLM_ENABLED", "CLAUDE_MODEL", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    yield
    lp.reset_state()


@pytest.fixture
def claude(monkeypatch):
    """Replace the SDK call; set state["raise"] to make it fail."""
    state = {"calls": [], "raise": None}

    def fake(prompt, temperature, max_tokens, cfg):
        state["calls"].append(prompt)
        if state["raise"]:
            raise state["raise"]
        return "answer"

    monkeypatch.setattr(lp, "_claude_call", fake)
    return state


def test_default_config():
    cfg = lp.load_config()
    assert cfg.enabled and cfg.configured and cfg.claude_model == lp.DEFAULT_CLAUDE_MODEL and cfg.timeout_s == 30


def test_invalid_timeout_and_quoted_key(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "abc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", f' "{FAKE_KEY}" ')
    cfg = lp.load_config()
    assert cfg.timeout_s == 30 and cfg.invalid_settings and cfg.claude_key == FAKE_KEY


def test_config_repr_and_status_hide_key():
    assert "FAKE" not in repr(lp.load_config()) and "FAKE" not in str(lp.status())


def test_success(claude):
    r = lp.generate("hi")
    assert r.ok and r.provider == "claude" and r.diagnostic is None
    assert lp.status()["last"]["ok"] is True


def test_failure_returns_diagnostic(claude):
    claude["raise"] = FakeAnthropicError(404, "not_found_error")
    r = lp.generate("hi")
    assert not r.ok and r.diagnostic["code"] == "MODEL_NOT_FOUND" and "CLAUDE_MODEL" in r.diagnostic["hint"]
    assert lp.status()["last"]["ok"] is False


def test_missing_key_is_reported_not_called(claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    r = lp.generate("hi")
    assert r.diagnostic["code"] == "NOT_CONFIGURED" and claude["calls"] == []


def test_disabled(claude, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    r = lp.generate("hi")
    assert r.diagnostic["code"] == "DISABLED" and claude["calls"] == []


def test_agent_call_llm_keeps_string_contract(claude):
    assert agent_base.call_llm("hi") == "answer"
    claude["raise"] = FakeAnthropicError(529, "overloaded_error")
    out = agent_base.call_llm("hi")
    assert out.startswith("ERROR:") and "PROVIDER_UNAVAILABLE" in out


def test_check_uses_fixed_prompt(claude):
    assert lp.check()["ok"] and claude["calls"] == [lp.CHECK_PROMPT]


@pytest.mark.parametrize("exc,code", [
    (FakeAnthropicError(401, "authentication_error"), "INVALID_KEY"),
    (FakeAnthropicError(403, "permission_error"), "PERMISSION_DENIED"),
    (FakeAnthropicError(404, "not_found_error"), "MODEL_NOT_FOUND"),
    (FakeAnthropicError(429, "rate_limit_error"), "RATE_LIMITED"),
    (FakeAnthropicError(500, "api_error"), "PROVIDER_UNAVAILABLE"),
    (FakeAnthropicError(400, "invalid_request_error"), "BAD_REQUEST"),
    (APITimeoutError(), "TIMEOUT"),
    (APIConnectionError(), "NETWORK"),
    (lp.EmptyResponse("end_turn"), "EMPTY_RESPONSE"),
    (ValueError("boom"), "UNKNOWN"),
])
def test_classify(exc, code):
    assert lp.classify_error(exc, "m").code == code


def test_redact():
    assert lp.redact(f"key {FAKE_KEY} here") == "key <redacted> here"
