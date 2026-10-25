"""HTTP-level tests for the /api/agri blueprint (no IAP, no LLM, no GCP)."""
import pytest
from flask import Flask

import agri_api
from agri.repository import InMemoryRepository
from agri.service import AgriService
from llm_providers import LLMResult


@pytest.fixture
def client(now, monkeypatch):
    agri_api.set_service(AgriService(repo=InMemoryRepository(now), clock=lambda: now))
    monkeypatch.setattr(agri_api, "_assistant", lambda prompt: LLMResult(text=None))   # AI unavailable
    app = Flask(__name__)
    app.register_blueprint(agri_bp := agri_api.agri_bp)
    assert agri_bp.url_prefix == "/api/agri"
    yield app.test_client()
    agri_api.set_service(None)


RISK = {"X-Demo-User": "demo.risk-reviewer"}


def test_read_endpoints(client):
    for path in ("/session", "/reference", "/dashboard", "/trades", "/risk", "/limits",
                 "/exceptions", "/market-data", "/audit", "/trades/AGD-0001",
                 "/risk/derivation?dim=commodity&key=CBOT_CORN"):
        assert client.get("/api/agri" + path).status_code == 200, path


def test_unknown_identity_falls_back_to_demo_trader(client):
    r = client.get("/api/agri/session", headers={"X-Demo-User": "admin"}).get_json()
    assert r["user"] == "demo.trader" and r["role"] == "TRADER"


def test_blotter_filters(client):
    rows = client.get("/api/agri/trades?commodity=ICE_SUGAR11&status=VALIDATED").get_json()
    assert rows and all(t["commodity"] == "ICE_SUGAR11" and t["status"] == "VALIDATED" for t in rows)
    assert client.get("/api/agri/trades?q=AGD-0005").get_json()[0]["id"] == "AGD-0005"


def test_capture_journey(client):
    ref = client.get("/api/agri/reference").get_json()
    md = client.get("/api/agri/market-data").get_json()["prices"]
    mark = next(p for p in md if p["commodity"] == "CBOT_CORN")
    y, m = mark["contract_month"].split("-")
    form = {"counterparty": ref["counterparties"][0], "portfolio": ref["portfolios"][0],
            "commodity": "CBOT_CORN", "instrument_type": "FUTURE", "direction": "SELL",
            "quantity": "3000", "contract_month": mark["contract_month"], "price": mark["price"],
            "trade_date": "2026-06-15", "maturity_date": f"{y}-{m}-14",
            "broker": "Demo Broker Beta", "execution_ref": "EX-API-1"}

    r = client.post("/api/agri/trades", json={"trade": form})
    assert r.status_code == 422 and "quantity" in r.get_json()["errors"]      # not a lot multiple

    form["quantity"] = "5000"
    r = client.post("/api/agri/trades", json={"trade": form})
    assert r.status_code == 201
    tid = r.get_json()["trade"]["id"]

    r = client.put(f"/api/agri/trades/{tid}", json={"trade": {"price": mark["price"] + 1}, "reason": "Fat-finger fix"})
    assert r.status_code == 200 and r.get_json()["trade"]["version"] == 2

    r = client.post(f"/api/agri/trades/{tid}/review", json={"decision": "APPROVE", "reason": "ok"})
    assert r.status_code == 403
    r = client.post(f"/api/agri/trades/{tid}/review", json={"decision": "APPROVE", "reason": "Checked vs confirm"},
                    headers=RISK)
    assert r.get_json()["trade"]["status"] == "VALIDATED"

    r = client.post(f"/api/agri/trades/{tid}/cancel", json={})
    assert r.status_code == 400 and "reason" in r.get_json()["errors"]
    assert client.post(f"/api/agri/trades/{tid}/cancel", json={"reason": "Client withdrew"}).status_code == 200

    actions = [e["action"] for e in client.get(f"/api/agri/audit?entity_id={tid}").get_json()]
    assert actions == ["TRADE_CANCELLED", "TRADE_APPROVED", "TRADE_AMENDED", "TRADE_CREATED"]


def test_exception_action_with_path_id(client):
    exc = next(e for e in client.get("/api/agri/exceptions").get_json() if e["rule"] == "STALE_PRICE")
    r = client.post(f"/api/agri/exceptions/{exc['id']}/action",
                    json={"action": "OVERRIDE", "reason": "Confirmed with desk"}, headers=RISK)
    assert r.status_code == 200


def test_bad_bodies_rejected(client):
    assert client.post("/api/agri/trades", data="not json", content_type="application/json").status_code == 400
    big = {"trade": {"notes": "x" * (70 * 1024)}}
    assert client.post("/api/agri/trades", json=big).status_code == 413
    assert client.get("/api/agri/trades/NOPE").status_code == 404
    assert client.get("/api/agri/risk/derivation?dim=colour&key=x").status_code == 400


def test_dashboard_portfolio_scope(client):
    ref = client.get("/api/agri/reference").get_json()
    pf = ref["portfolios"][0]
    d = client.get(f"/api/agri/dashboard?portfolio={pf}").get_json()
    assert d["scope"] == {"portfolio": pf, "limits": "firm-wide"}
    trades = {t["id"] for t in client.get(f"/api/agri/trades?portfolio={pf}").get_json()}
    assert all(e["trade_id"] in trades for e in d["exceptions"] if e.get("trade_id"))
    assert client.get("/api/agri/dashboard?portfolio=Nope").status_code == 400


def test_ai_explain_requires_confirmation(client):
    r = client.post("/api/agri/ai/explain", json={"topic": "trade", "id": "AGD-0001"})
    assert r.status_code == 428


def test_ai_preview_lists_minimised_payload(client):
    r = client.post("/api/agri/ai/preview", json={"topic": "trade", "id": "AGD-0001"}).get_json()
    blob = str(r["payload"])
    trade = client.get("/api/agri/trades/AGD-0001").get_json()["trade"]
    for sensitive in ("AGD-0001", trade["counterparty"], trade["portfolio"], trade["execution_ref"]):
        assert sensitive and sensitive not in blob
    assert "valuation.mtm_usd" in r["fields"] and "counterparty" not in r["fields"]
    assert r["never_sent"]


def test_ai_explain_degrades_gracefully_and_changes_nothing(client):
    before = client.get("/api/agri/dashboard").get_json()
    r = client.post("/api/agri/ai/explain", json={"topic": "limit", "id": "LIM-001", "confirm": True}).get_json()
    assert r["available"] is False and r["authoritative"] is False and r["assistive"] is True
    after = client.get("/api/agri/dashboard").get_json()
    assert before["kpis"] == after["kpis"] and before["limits"] == after["limits"]


def test_ai_explain_output_is_labelled_and_audited(client, monkeypatch):
    sent = {}

    def fake(prompt):
        sent["prompt"] = prompt
        return LLMResult(text="The corn limit is breached.", provider="claude", model="m")
    monkeypatch.setattr(agri_api, "_assistant", fake)
    r = client.post("/api/agri/ai/explain", json={"topic": "trade", "id": "AGD-0001", "confirm": True}).get_json()
    assert r["available"] is True and r["authoritative"] is False and r["provider"] == "claude"
    assert "AGD-0001" not in sent["prompt"]
    ev = client.get("/api/agri/audit?entity_id=AGD-0001").get_json()[0]
    assert ev["action"] == "AI_EXPLANATION_REQUESTED" and "claude" in ev["reason"]


def test_ai_explain_failure_carries_diagnostic(client, monkeypatch):
    monkeypatch.setattr(agri_api, "_assistant", lambda p: LLMResult(
        text=None, diagnostic={"provider": "claude", "code": "RATE_LIMITED", "message": "x"}))
    r = client.post("/api/agri/ai/explain", json={"topic": "trade", "id": "AGD-0001", "confirm": True}).get_json()
    assert r["available"] is False and r["diagnostic"]["code"] == "RATE_LIMITED"


def test_ai_status_has_no_secrets(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKEFAKEFAKEFAKE")
    r = client.get("/api/agri/ai/status")
    assert "FAKE" not in r.get_data(as_text=True)
    assert r.get_json()["configured"] is True and r.get_json()["provider"] == "claude"
