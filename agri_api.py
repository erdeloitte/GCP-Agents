"""agri_api.py
Flask blueprint for the agricultural derivatives prototype (/api/agri/*).

Thin HTTP layer over agri.service.AgriService: parses and size-checks input,
resolves the demo identity, and maps ServiceError to JSON responses. All
calculations live in the agri package.

AI boundary: /api/agri/ai/explain sends an allow-listed subset of ALREADY-
COMPUTED deterministic results (agri/ai_payload.py) to the configured LLM
provider for a plain-language explanation, only after the user has seen the
payload (/ai/preview) and confirmed. It cannot call any write method, and its
output is never stored as a figure.
"""
import json
import logging

from flask import Blueprint, jsonify, request

import llm_providers
from agri import ai_payload
from agri import reference as ref
from agri.service import AgriService, ROLES, ServiceError
from llm_providers import LLMResult

logger = logging.getLogger(__name__)

agri_bp = Blueprint("agri", __name__, url_prefix="/api/agri")

MAX_BODY_BYTES = 64 * 1024
DEMO_HEADER = "X-Demo-User"

_service: AgriService | None = None


def get_service() -> AgriService:
    global _service
    if _service is None:
        _service = AgriService()
    return _service


def set_service(service: AgriService | None) -> None:
    """Swap the service (used by tests, or to inject a different repository)."""
    global _service
    _service = service


# ── Request helpers ──────────────────────────────────────────────────────────

def _user() -> str:
    """Demo identity. This is NOT authentication — it selects a demo role."""
    u = (request.headers.get(DEMO_HEADER) or "").strip()
    return u if u in ref.DEMO_USERS else ref.DEFAULT_DEMO_USER


def _body() -> dict:
    if request.content_length and request.content_length > MAX_BODY_BYTES:
        raise ServiceError("Request body too large.", status=413)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ServiceError("Expected a JSON object body.", status=400)
    return body


@agri_bp.errorhandler(ServiceError)
def _service_error(e: ServiceError):
    payload = {"error": e.message}
    if e.errors:
        payload["errors"] = e.errors
    if e.warnings:
        payload["warnings"] = e.warnings
    return jsonify(payload), e.status


# ── Read endpoints ───────────────────────────────────────────────────────────

@agri_bp.get("/session")
def session_info():
    user = _user()
    return jsonify({
        "user": user, "label": ref.DEMO_USERS[user], "role": ROLES.get(user),
        "demo_users": [{"id": k, "label": v, "role": ROLES.get(k)} for k, v in ref.DEMO_USERS.items()],
        "auth_notice": "Demo identity only — no real authentication in this prototype.",
        "iap_user": getattr(request, "iap_user_email", None),
    })


@agri_bp.get("/reference")
def reference_data():
    return jsonify(ref.reference_payload())


@agri_bp.get("/dashboard")
def dashboard():
    return jsonify(get_service().dashboard((request.args.get("portfolio") or "")[:120] or None))


@agri_bp.get("/trades")
def list_trades():
    keys = ("q", "commodity", "status", "portfolio", "instrument_type", "direction", "counterparty")
    filters = {k: (request.args.get(k) or "")[:120] for k in keys}
    return jsonify(get_service().list_trades(filters))


@agri_bp.get("/trades/<trade_id>")
def trade_detail(trade_id):
    return jsonify(get_service().trade_detail(trade_id))


@agri_bp.get("/risk")
def risk_view():
    return jsonify(get_service().risk_view())


@agri_bp.get("/risk/derivation")
def risk_derivation():
    dim = request.args.get("dim", "")
    key = request.args.get("key", "")
    if not key:
        raise ServiceError("key is required.")
    return jsonify(get_service().derivation(dim, key))


@agri_bp.get("/limits")
def limits():
    return jsonify(get_service().risk_view()["limits"])


@agri_bp.get("/exceptions")
def exceptions():
    return jsonify(get_service().exceptions())


@agri_bp.get("/market-data")
def market_data():
    return jsonify({"source": "SYNTHETIC DEMO DATA — not from an approved market-data provider",
                    "stale_threshold_hours": get_service().stale_hours,
                    "prices": get_service().market_data()})


@agri_bp.get("/audit")
def audit():
    entity = (request.args.get("entity_id") or "")[:120] or None
    return jsonify(get_service().audit(entity))


# ── Write endpoints (validated, role-checked, audited) ──────────────────────

@agri_bp.post("/trades/validate")
def validate_preview():
    body = _body()
    return jsonify(get_service().preview_validation(body.get("trade") or {}, is_new=not body.get("id")))


@agri_bp.post("/trades")
def create_trade():
    body = _body()
    trade, warnings = get_service().create_trade(body.get("trade") or {}, _user())
    return jsonify({"trade": trade, "warnings": warnings}), 201


@agri_bp.put("/trades/<trade_id>")
def amend_trade(trade_id):
    body = _body()
    trade, warnings = get_service().amend_trade(trade_id, body.get("trade") or {}, _user(),
                                                body.get("reason"))
    return jsonify({"trade": trade, "warnings": warnings})


@agri_bp.post("/trades/<trade_id>/cancel")
def cancel_trade(trade_id):
    body = _body()
    return jsonify({"trade": get_service().cancel_trade(trade_id, _user(), body.get("reason"))})


@agri_bp.post("/trades/<trade_id>/review")
def review_trade(trade_id):
    body = _body()
    return jsonify({"trade": get_service().review_trade(trade_id, _user(), body.get("decision"),
                                                       body.get("reason"))})


@agri_bp.post("/exceptions/<path:exc_id>/action")
def exception_action(exc_id):
    body = _body()
    return jsonify({"action": get_service().act_on_exception(exc_id, _user(), body.get("action"),
                                                             body.get("reason"))})


@agri_bp.put("/limits/<limit_id>")
def update_limit(limit_id):
    body = _body()
    return jsonify({"limit": get_service().update_limit(limit_id, _user(), body, body.get("reason"))})


@agri_bp.post("/demo/reset")
def reset_demo():
    get_service().reset_demo(_user())
    return jsonify({"ok": True})


# ── Assistive AI (explanations only) ────────────────────────────────────────

AI_NOTICE = ("AI-generated explanation of figures already calculated by the deterministic engine. "
             "Not a source of record; verify against the figures shown.")
DATA_POLICY = ("Only the allow-listed fields shown in the preview are sent to the external provider. "
               "Identifiers, counterparties, portfolios, brokers, execution references, notes and user "
               "names are never sent. All data in this prototype is synthetic.")


def _assistant(prompt: str) -> LLMResult:
    """Call the configured providers (with fallback). Never raises."""
    try:
        return llm_providers.generate(prompt, temperature=0.2, max_tokens=600, trace_label="AGRI_EXPLAIN")
    except Exception as e:  # never let AI failure break the core
        logger.warning("AI explain failed: %s", type(e).__name__)
        return LLMResult(text=None)


def _payload(body: dict) -> tuple[str, str, dict]:
    topic = str(body.get("topic") or "")
    entity_id = str(body.get("id") or "")[:120]
    try:
        return topic, entity_id, ai_payload.build(get_service(), topic, entity_id)
    except ValueError as e:
        raise ServiceError(str(e))
    except LookupError as e:
        raise ServiceError(str(e), status=404)


def _field_paths(data, prefix="") -> list:
    out = []
    for k, v in data.items():
        if isinstance(v, dict):
            out += _field_paths(v, f"{prefix}{k}.")
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            out += _field_paths(v[0], f"{prefix}{k}[].")
        else:
            out.append(prefix + k)
    return out


def _ai_config() -> dict:
    st = llm_providers.status()
    return {"enabled": st["enabled"], "order": st["order"],
            "configured": [p["provider"] for p in st["providers"] if p["configured"]]}


@agri_bp.get("/ai/status")
def ai_status():
    return jsonify({**llm_providers.status(), "data_policy": DATA_POLICY, "never_sent": ai_payload.NEVER_SENT})


@agri_bp.post("/ai/check")
def ai_check():
    """Probe each provider with a fixed prompt. Sends no application data."""
    return jsonify({"results": llm_providers.check(), "prompt": llm_providers.CHECK_PROMPT,
                    "status": llm_providers.status()})


@agri_bp.post("/ai/preview")
def ai_preview():
    """Show exactly what would be sent, and to whom, before the user consents."""
    _, _, payload = _payload(_body())
    return jsonify({"payload": payload, "fields": _field_paths(payload["data"]),
                    "never_sent": ai_payload.NEVER_SENT, "data_policy": DATA_POLICY, **_ai_config()})


@agri_bp.post("/ai/explain")
def ai_explain():
    body = _body()
    if body.get("confirm") is not True:
        raise ServiceError("Confirm the data preview before sending to an AI provider.", status=428)
    topic, entity_id, payload = _payload(body)
    prompt = (
        "You are an assistant inside a PROTOTYPE agricultural derivatives risk tool. "
        "Explain the deterministic system output below in plain English for a risk analyst, "
        "in at most 5 sentences. Rules: do NOT calculate, estimate or restate any new numbers "
        "beyond those given; do NOT recommend approving trades, changing limits or applying "
        "overrides; point out missing or stale inputs if present; mention that valuation uses "
        "the simplified methodology listed in the assumptions. All data is synthetic.\n\n"
        f"SYSTEM OUTPUT ({topic}):\n{json.dumps(payload['data'], default=str)}"
    )
    result = _assistant(prompt)
    get_service().log_ai_request(_user(), topic, entity_id, result.provider, _field_paths(payload["data"]))
    return jsonify({
        "assistive": True, "authoritative": False, "available": result.ok,
        "text": result.text or AI_UNAVAILABLE,
        "notice": AI_NOTICE, **result.to_dict(),
    })


AI_UNAVAILABLE = ("No AI provider could answer — see the diagnostics below. "
                  "All deterministic results are unaffected.")
