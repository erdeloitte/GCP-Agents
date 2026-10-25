"""llm_providers.py
Single entry point for LLM calls. The only provider is Anthropic Claude.

Configuration (environment or .env — never hard-code keys):
  LLM_ENABLED          true | false              (default true)
  ANTHROPIC_API_KEY    Anthropic key
  CLAUDE_MODEL         default "claude-haiku-4-5-20251001"
  LLM_TIMEOUT_SECONDS  default 30

Diagnostics expose an error code, HTTP status and a remediation hint — never
keys or raw provider payloads.
"""
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PROVIDER = "claude"
SERVICE = "api.anthropic.com"
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CHECK_PROMPT = "Reply with the single word OK."

_SECRET_RE = re.compile(r"(sk-ant-[0-9A-Za-z_\-]{10,})")


def redact(text: str) -> str:
    """Remove anything that looks like an API key."""
    return _SECRET_RE.sub("<redacted>", str(text or ""))


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class LLMConfig:
    enabled: bool
    claude_key: str = field(repr=False)
    claude_model: str
    timeout_s: float
    invalid_settings: list

    @property
    def configured(self) -> bool:
        return bool(self.claude_key)


def load_config() -> LLMConfig:
    """Read configuration at call time so .env / env changes are picked up."""
    invalid = []
    try:
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        if not 1 <= timeout <= 300:
            raise ValueError
    except ValueError:
        invalid.append("LLM_TIMEOUT_SECONDS must be a number between 1 and 300; using 30.")
        timeout = 30.0
    return LLMConfig(
        enabled=_env_bool("LLM_ENABLED", True),
        claude_key=os.getenv("ANTHROPIC_API_KEY", "").strip().strip("\"'"),
        claude_model=os.getenv("CLAUDE_MODEL", "").strip() or DEFAULT_CLAUDE_MODEL,
        timeout_s=timeout,
        invalid_settings=invalid,
    )


# ── Diagnostics ──────────────────────────────────────────────────────────────

@dataclass
class Diagnostic:
    provider: str
    code: str
    message: str
    hint: str = ""
    http_status: int | None = None
    model: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class EmptyResponse(Exception):
    """The provider answered but returned no text."""
    def __init__(self, finish_reason: str | None = None):
        super().__init__(finish_reason or "")
        self.finish_reason = finish_reason


def _error_type(exc) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return err.get("type")
    return None


def classify_error(exc: Exception, model: str) -> Diagnostic:
    """Map a provider exception to a stable, non-sensitive diagnostic."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) else None
    etype = _error_type(exc)
    d = Diagnostic(PROVIDER, "UNKNOWN", f"Unexpected error ({name}).", http_status=status, model=model)

    if isinstance(exc, EmptyResponse):
        d.code = "EMPTY_RESPONSE"
        d.message = "The model returned no text" + (f" (stop reason {exc.finish_reason})." if exc.finish_reason else ".")
        d.hint = "Retry; if it persists try another CLAUDE_MODEL."
    elif "Timeout" in name:
        d.code, d.message, d.hint = "TIMEOUT", "Request timed out.", "Retry, or raise LLM_TIMEOUT_SECONDS."
    elif "Connection" in name:
        d.code, d.message, d.hint = "NETWORK", "Could not reach the provider.", f"Check outbound access to {SERVICE}."
    elif status == 401 or etype == "authentication_error":
        d.code, d.message, d.hint = "INVALID_KEY", "The API key was rejected.", "Check ANTHROPIC_API_KEY is current."
    elif status == 404 or etype == "not_found_error":
        d.code, d.message = "MODEL_NOT_FOUND", f"Model '{model}' is not available."
        d.hint = "Set CLAUDE_MODEL to a model this key can use."
    elif status == 403 or etype == "permission_error":
        d.code, d.message = "PERMISSION_DENIED", "The key does not have permission for this model."
        d.hint = "Check the key's workspace and model access."
    elif status == 429 or etype == "rate_limit_error":
        d.code, d.message, d.hint = "RATE_LIMITED", "Quota or rate limit exceeded.", "Retry later or raise quota."
    elif (status and status >= 500) or etype in ("overloaded_error", "api_error"):
        d.code, d.message, d.hint = "PROVIDER_UNAVAILABLE", "The provider returned a server error.", "Retry later."
    elif status == 400:
        d.code, d.message = "BAD_REQUEST", "The provider rejected the request."
    return d


# ── Calls ────────────────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_last: dict | None = None     # last outcome (non-sensitive)


def _claude_call(prompt: str, temperature: float, max_tokens: int, cfg: LLMConfig) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=cfg.claude_key, timeout=cfg.timeout_s, max_retries=1)
    resp = client.messages.create(model=cfg.claude_model, max_tokens=max_tokens, temperature=temperature,
                                  messages=[{"role": "user", "content": prompt}])
    text = "".join(getattr(b, "text", "") for b in resp.content or []).strip()
    if not text:
        raise EmptyResponse(getattr(resp, "stop_reason", None))
    return text


@dataclass
class LLMResult:
    text: str | None
    provider: str | None = None
    model: str | None = None
    diagnostic: dict | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "provider": self.provider, "model": self.model, "diagnostic": self.diagnostic}


def _record(ok: bool, diag: dict | None) -> None:
    global _last
    with _state_lock:
        _last = {"ok": ok, "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "diagnostic": diag}


def generate(prompt: str, temperature: float = 0.3, max_tokens: int = 2000,
             trace_label: str = "LLM", cfg: LLMConfig | None = None) -> LLMResult:
    """Call Claude. Never raises; failures come back as a diagnostic."""
    cfg = cfg or load_config()
    if not cfg.enabled:
        return LLMResult(None, diagnostic=Diagnostic(PROVIDER, "DISABLED",
                                                     "LLM features are disabled (LLM_ENABLED=false).").to_dict())
    if not cfg.configured:
        return LLMResult(None, diagnostic=Diagnostic(PROVIDER, "NOT_CONFIGURED", "ANTHROPIC_API_KEY is not set.",
                                                     hint="Add ANTHROPIC_API_KEY to the environment.").to_dict())
    try:
        text = _claude_call(prompt, temperature, max_tokens, cfg)
    except Exception as e:
        diag = classify_error(e, cfg.claude_model).to_dict()
        diag["message"] = redact(diag["message"])
        _record(False, diag)
        logger.warning("[%s] claude failed: %s (HTTP %s)", trace_label, diag["code"], diag["http_status"])
        return LLMResult(None, diagnostic=diag)
    _record(True, None)
    logger.info("[%s] answered by claude (%s)", trace_label, cfg.claude_model)
    return LLMResult(text, provider=PROVIDER, model=cfg.claude_model)


def check(cfg: LLMConfig | None = None) -> dict:
    """Probe the provider with a fixed harmless prompt (no application data)."""
    r = generate(CHECK_PROMPT, temperature=0, max_tokens=10, trace_label="LLM_CHECK", cfg=cfg)
    return r.to_dict()


def status(cfg: LLMConfig | None = None) -> dict:
    """Configuration summary and last known outcome. Contains no secrets."""
    cfg = cfg or load_config()
    with _state_lock:
        last = dict(_last) if _last else None
    return {"enabled": cfg.enabled, "provider": PROVIDER, "configured": cfg.configured,
            "model": cfg.claude_model, "timeout_s": cfg.timeout_s, "last": last,
            "invalid_settings": cfg.invalid_settings}


def reset_state() -> None:
    """Forget the cached outcome (tests)."""
    global _last
    with _state_lock:
        _last = None
