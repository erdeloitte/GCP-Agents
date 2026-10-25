"""llm_providers.py
Single entry point for LLM calls (Gemini, Claude): explicit provider order,
Gemini endpoint routing, timeouts and non-sensitive diagnostics.

Configuration (environment or .env — never hard-code keys):
  LLM_ENABLED          true | false              (default true)
  LLM_PROVIDER_ORDER   comma list, e.g. "gemini,claude" (default). The first
                       configured provider is primary; the rest are fallbacks.
  GEMINI_API_KEY       Google key (AI Studio "AIza…" or Cloud "AQ." key)
  GEMINI_MODEL         default "gemini-3.5-flash"
  GEMINI_BACKEND       auto | public | vertex    (default auto)
                         public → generativelanguage.googleapis.com (Gemini API)
                         vertex → aiplatform.googleapis.com (Vertex AI express mode)
                         auto   → "AQ." keys try Vertex first and fall back to the
                                  public endpoint if the key is blocked for Vertex
                                  (API_KEY_SERVICE_BLOCKED / SERVICE_DISABLED);
                                  other keys use the public endpoint.
  ANTHROPIC_API_KEY    Anthropic key
  CLAUDE_MODEL         default "claude-haiku-4-5-20251001"
  LLM_TIMEOUT_SECONDS  default 30

Diagnostics expose an error code, HTTP status, the public API service name and
a remediation hint — never keys, raw provider payloads or project identifiers.
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

PROVIDERS = ("gemini", "claude")
DEFAULT_ORDER = ("gemini", "claude")
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"
GEMINI_SERVICES = {"public": "generativelanguage.googleapis.com",
                   "vertex": "aiplatform.googleapis.com"}
# Vertex failures that mean "this key may not use Vertex" rather than "the key is bad".
VERTEX_FALLBACK_CODES = {"API_KEY_SERVICE_BLOCKED", "SERVICE_DISABLED"}
CHECK_PROMPT = "Reply with the single word OK."
# Gemini "thinking" models spend part of max_output_tokens on internal reasoning;
# a small budget can be used up before any text is produced (finish reason MAX_TOKENS).
GEMINI_MIN_OUTPUT_TOKENS = 1024

_SECRET_RE = re.compile(r"(AIza[0-9A-Za-z_\-]{10,}|AQ\.[0-9A-Za-z_\-\.]{10,}|sk-ant-[0-9A-Za-z_\-]{10,})")


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
    order: list
    gemini_key: str = field(repr=False)
    gemini_model: str
    gemini_backend: str
    claude_key: str = field(repr=False)
    claude_model: str
    timeout_s: float
    invalid_settings: list

    def configured(self, provider: str) -> bool:
        return bool(self.gemini_key if provider == "gemini" else self.claude_key)


def load_config() -> LLMConfig:
    """Read configuration at call time so .env / env changes are picked up."""
    invalid = []
    raw_order = [p.strip().lower() for p in os.getenv("LLM_PROVIDER_ORDER", "").split(",") if p.strip()]
    order = [p for p in raw_order if p in PROVIDERS]
    if len(order) != len(raw_order):
        invalid.append("LLM_PROVIDER_ORDER contains unknown providers (allowed: gemini, claude).")
    order = list(dict.fromkeys(order)) or list(DEFAULT_ORDER)

    backend = os.getenv("GEMINI_BACKEND", "auto").strip().lower() or "auto"
    if backend not in ("auto", "public", "vertex"):
        invalid.append("GEMINI_BACKEND must be auto, public or vertex; using auto.")
        backend = "auto"
    try:
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        if not 1 <= timeout <= 300:
            raise ValueError
    except ValueError:
        invalid.append("LLM_TIMEOUT_SECONDS must be a number between 1 and 300; using 30.")
        timeout = 30.0

    return LLMConfig(
        enabled=_env_bool("LLM_ENABLED", True),
        order=order,
        gemini_key=os.getenv("GEMINI_API_KEY", "").strip().strip("\"'"),
        gemini_model=os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL,
        gemini_backend=backend,
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
    service: str | None = None
    backend: str | None = None
    model: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class EmptyResponse(Exception):
    """The provider answered but returned no text."""
    def __init__(self, finish_reason: str | None = None):
        super().__init__(finish_reason or "")
        self.finish_reason = finish_reason


class ProviderError(Exception):
    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.code)
        self.diagnostic = diagnostic


def _google_reason(details) -> tuple[str | None, str | None]:
    """Extract (reason, status) from a google-genai error payload."""
    if not isinstance(details, dict):
        return None, None
    err = details.get("error", details)
    reason = None
    for d in err.get("details") or []:
        if isinstance(d, dict) and d.get("reason"):
            reason = d["reason"]
            break
    return reason, err.get("status")


def _anthropic_type(exc) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return err.get("type")
    return None


_GEMINI_KEY_HINT = ("Check GEMINI_API_KEY is current and copied without quotes or spaces.")


def classify_error(provider: str, exc: Exception, model: str, backend: str | None = None) -> Diagnostic:
    """Map a provider exception to a stable, non-sensitive diagnostic."""
    name = type(exc).__name__
    status = getattr(exc, "code", None) if provider == "gemini" else getattr(exc, "status_code", None)
    status = status if isinstance(status, int) else None
    service = GEMINI_SERVICES.get(backend) if provider == "gemini" else "api.anthropic.com"
    d = Diagnostic(provider=provider, code="UNKNOWN", message=f"Unexpected error ({name}).",
                   http_status=status, service=service, backend=backend, model=model)

    if isinstance(exc, EmptyResponse):
        reason = (exc.finish_reason or "").rsplit(".", 1)[-1]
        d.code = "EMPTY_RESPONSE"
        d.message = "The model returned no text" + (f" (finish reason {reason})." if reason else ".")
        d.hint = ("The output budget was used up, e.g. by model reasoning; raise max output tokens."
                  if reason == "MAX_TOKENS" else
                  "The response may have been blocked by safety settings." if reason in ("SAFETY", "PROHIBITED_CONTENT")
                  else "Retry; if it persists try another model.")
        return d
    if "Timeout" in name:
        d.code, d.message, d.hint = "TIMEOUT", "Request timed out.", "Retry, or raise LLM_TIMEOUT_SECONDS."
        return d
    if "Connection" in name or "ConnectError" in name:
        d.code, d.message, d.hint = ("NETWORK", "Could not reach the provider.",
                                     "Check outbound network / proxy access to " + (service or "the provider") + ".")
        return d

    reason, gstatus = _google_reason(getattr(exc, "details", None)) if provider == "gemini" else (None, None)
    atype = _anthropic_type(exc) if provider == "claude" else None

    if reason == "API_KEY_SERVICE_BLOCKED":
        d.code = reason
        d.message = f"The API key is restricted and may not call {service}."
        d.hint = ("In Google Cloud console › APIs & Services › Credentials, allow this API in the key's "
                  "API restrictions, or use the other Gemini endpoint (GEMINI_BACKEND="
                  f"{'public' if backend == 'vertex' else 'vertex'}).")
    elif reason == "SERVICE_DISABLED":
        d.code = reason
        d.message = f"{service} is not enabled on the key's Google Cloud project."
        d.hint = "Enable the API on the project, or switch GEMINI_BACKEND."
    elif reason in ("API_KEY_INVALID", "API_KEY_EXPIRED") or status == 401 or atype == "authentication_error":
        d.code = "INVALID_KEY"
        d.message = "The API key was rejected."
        d.hint = _GEMINI_KEY_HINT if provider == "gemini" else "Check ANTHROPIC_API_KEY is current."
    elif reason == "BILLING_DISABLED":
        d.code, d.message, d.hint = reason, "Billing is not enabled for the key's project.", "Enable billing on the project."
    elif status == 404 or atype == "not_found_error":
        d.code = "MODEL_NOT_FOUND"
        d.message = f"Model '{model}' is not available" + (f" on {service}." if service else ".")
        d.hint = f"Set {'GEMINI_MODEL' if provider == 'gemini' else 'CLAUDE_MODEL'} to a model this key can use."
    elif status == 403 or gstatus == "PERMISSION_DENIED" or atype == "permission_error":
        d.code = "PERMISSION_DENIED"
        d.message = "The key does not have permission for this model or endpoint."
        d.hint = "Check the key's API restrictions and the project's IAM / model access."
    elif status == 429 or atype == "rate_limit_error":
        d.code, d.message, d.hint = "RATE_LIMITED", "Quota or rate limit exceeded.", "Retry later or raise quota."
    elif (status and status >= 500) or atype in ("overloaded_error", "api_error"):
        d.code, d.message, d.hint = "PROVIDER_UNAVAILABLE", "The provider returned a server error.", "Retry later."
    elif status == 400:
        d.code, d.message = "BAD_REQUEST", "The provider rejected the request."
    return d


# ── Provider calls ───────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_last: dict = {}                 # provider -> last outcome (non-sensitive)
_resolved_gemini_backend: dict = {}   # key fingerprint -> backend that worked (auto mode)


def _key_fp(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _gemini_backends(cfg: LLMConfig) -> list:
    if cfg.gemini_backend in ("public", "vertex"):
        return [cfg.gemini_backend]
    with _state_lock:
        resolved = _resolved_gemini_backend.get(_key_fp(cfg.gemini_key))
    if resolved:
        return [resolved]
    return ["vertex", "public"] if cfg.gemini_key.startswith("AQ.") else ["public"]


def _gemini_call(prompt: str, temperature: float, max_tokens: int, cfg: LLMConfig, backend: str) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=(backend == "vertex"), api_key=cfg.gemini_key,
                          http_options=types.HttpOptions(timeout=int(cfg.timeout_s * 1000)))
    resp = client.models.generate_content(
        model=cfg.gemini_model, contents=prompt,
        config={"temperature": temperature, "max_output_tokens": max(max_tokens, GEMINI_MIN_OUTPUT_TOKENS)})
    text = (resp.text or "").strip()
    if not text:
        cands = getattr(resp, "candidates", None) or []
        raise EmptyResponse(str(getattr(cands[0], "finish_reason", "") or "") if cands else None)
    return text


def _claude_call(prompt: str, temperature: float, max_tokens: int, cfg: LLMConfig) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=cfg.claude_key, timeout=cfg.timeout_s, max_retries=1)
    resp = client.messages.create(model=cfg.claude_model, max_tokens=max_tokens, temperature=temperature,
                                  messages=[{"role": "user", "content": prompt}])
    text = "".join(getattr(b, "text", "") for b in resp.content or []).strip()
    if not text:
        raise EmptyResponse(getattr(resp, "stop_reason", None))
    return text


def _run_gemini(prompt, temperature, max_tokens, cfg) -> tuple[str, dict]:
    notes = []
    backends = _gemini_backends(cfg)
    for i, backend in enumerate(backends):
        try:
            text = _gemini_call(prompt, temperature, max_tokens, cfg, backend)
        except Exception as e:
            diag = classify_error("gemini", e, cfg.gemini_model, backend)
            more = i + 1 < len(backends)
            if more and backend == "vertex" and diag.code in VERTEX_FALLBACK_CODES:
                notes.append(f"Vertex AI endpoint unavailable for this key ({diag.code}); "
                             "retried on the public Gemini API.")
                logger.warning("[LLM] gemini/vertex %s — retrying on public endpoint", diag.code)
                continue
            raise ProviderError(diag)
        if cfg.gemini_backend == "auto":
            with _state_lock:
                _resolved_gemini_backend[_key_fp(cfg.gemini_key)] = backend
        return text, {"backend": backend, "model": cfg.gemini_model, "notes": notes}
    raise ProviderError(Diagnostic("gemini", "UNKNOWN", "No Gemini endpoint attempted."))


def _run_claude(prompt, temperature, max_tokens, cfg) -> tuple[str, dict]:
    try:
        text = _claude_call(prompt, temperature, max_tokens, cfg)
    except Exception as e:
        raise ProviderError(classify_error("claude", e, cfg.claude_model))
    return text, {"backend": None, "model": cfg.claude_model, "notes": []}


RUNNERS = {"gemini": _run_gemini, "claude": _run_claude}


@dataclass
class LLMResult:
    text: str | None
    provider: str | None = None
    model: str | None = None
    backend: str | None = None
    attempts: list = field(default_factory=list)   # [{provider, ok, diagnostic?}]
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.text)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "provider": self.provider, "model": self.model, "backend": self.backend,
                "attempts": self.attempts, "notes": self.notes}


def _record(provider: str, ok: bool, diag: dict | None, backend: str | None = None) -> None:
    with _state_lock:
        _last[provider] = {"ok": ok, "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                           "backend": backend, "diagnostic": diag}


def generate(prompt: str, temperature: float = 0.3, max_tokens: int = 2000,
             trace_label: str = "LLM", order: list | None = None, cfg: LLMConfig | None = None) -> LLMResult:
    """Try providers in order; return the first success with a record of every attempt."""
    cfg = cfg or load_config()
    result = LLMResult(text=None)
    if not cfg.enabled:
        result.attempts.append({"provider": None, "ok": False, "diagnostic": Diagnostic(
            "none", "DISABLED", "LLM features are disabled (LLM_ENABLED=false).").to_dict()})
        return result
    for provider in order or cfg.order:
        if provider not in RUNNERS:
            continue
        if not cfg.configured(provider):
            env = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
            result.attempts.append({"provider": provider, "ok": False, "diagnostic": Diagnostic(
                provider, "NOT_CONFIGURED", f"{env} is not set.", hint=f"Add {env} to .env to enable.").to_dict()})
            continue
        try:
            text, meta = RUNNERS[provider](prompt, temperature, max_tokens, cfg)
        except ProviderError as e:
            diag = e.diagnostic.to_dict()
            diag["message"] = redact(diag["message"])
            result.attempts.append({"provider": provider, "ok": False, "diagnostic": diag})
            _record(provider, False, diag, diag.get("backend"))
            logger.warning("[%s] %s failed: %s (HTTP %s)", trace_label, provider, diag["code"], diag["http_status"])
            continue
        result.attempts.append({"provider": provider, "ok": True})
        result.text, result.provider = text, provider
        result.model, result.backend, result.notes = meta["model"], meta["backend"], meta["notes"]
        _record(provider, True, None, meta["backend"])
        logger.info("[%s] answered by %s (%s%s)", trace_label, provider, meta["model"],
                    f", {meta['backend']}" if meta["backend"] else "")
        return result
    return result


def check(cfg: LLMConfig | None = None) -> list:
    """Probe each configured provider independently with a fixed harmless prompt (no app data)."""
    cfg = cfg or load_config()
    out = []
    for provider in cfg.order:
        r = generate(CHECK_PROMPT, temperature=0, max_tokens=10, trace_label="LLM_CHECK",
                     order=[provider], cfg=cfg)
        a = r.attempts[-1] if r.attempts else {}
        out.append({"provider": provider, "ok": r.ok, "model": r.model, "backend": r.backend,
                    "notes": r.notes, "diagnostic": a.get("diagnostic")})
    return out


def status(cfg: LLMConfig | None = None) -> dict:
    """Configuration summary and last known outcome per provider. Contains no secrets."""
    cfg = cfg or load_config()
    with _state_lock:
        last = {k: dict(v) for k, v in _last.items()}
        resolved = _resolved_gemini_backend.get(_key_fp(cfg.gemini_key)) if cfg.gemini_key else None
    providers = []
    for p in cfg.order:
        providers.append({
            "provider": p, "configured": cfg.configured(p),
            "model": cfg.gemini_model if p == "gemini" else cfg.claude_model,
            "backend": (resolved or cfg.gemini_backend) if p == "gemini" else None,
            "last": last.get(p),
        })
    return {"enabled": cfg.enabled, "order": cfg.order, "timeout_s": cfg.timeout_s,
            "gemini_backend_setting": cfg.gemini_backend, "providers": providers,
            "invalid_settings": cfg.invalid_settings}


def reset_state() -> None:
    """Forget cached outcomes (tests / config reload)."""
    with _state_lock:
        _last.clear()
        _resolved_gemini_backend.clear()
