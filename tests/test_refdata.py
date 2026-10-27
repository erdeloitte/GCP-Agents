"""Data Setup: validation, role and reason controls, dependency-safe delete,
deactivation effects on trade capture, audit, and the dashboard worklist."""
import pytest

from agri import refdata
from agri.service import ServiceError
from test_service import new_trade_form

RISK, TRADER = "demo.risk-reviewer", "demo.trader"
STRATEGY = {"code": "BASIS_HEDGE", "name": "Basis hedge", "description": "Hedges basis risk (demo)."}


def records(service, kind):
    return service.reference_data()["records"][kind]


# ── Validation ──────────────────────────────────────────────────────────────

def test_seed_records_are_synthetic_and_valid():
    for r in refdata.default_records():
        if refdata.KINDS[r["kind"]]["editable"]:
            data = {"name": r["name"], "description": r["description"] or "x", **r["attrs"]}
            _, errors = refdata.validate(r["kind"], data, r, set())
            assert not errors, (r, errors)
    names = [r["name"] for r in refdata.default_records() if r["kind"] in ("location", "counterparty", "broker")]
    assert all("Demo" in n or "Synthetic" in n or "Example" in n or "Sample" in n or "OTC" in n for n in names)


@pytest.mark.parametrize("data,field", [
    ({**STRATEGY, "code": ""}, "code"),
    ({**STRATEGY, "code": "bad code!"}, "code"),
    ({**STRATEGY, "name": ""}, "name"),
    ({**STRATEGY, "description": ""}, "description"),
    ({**STRATEGY, "name": "x" * 61}, "name"),
    ({**STRATEGY, "code": "ORIG_HEDGE"}, "code"),          # duplicate
    ({**STRATEGY, "status": "MAYBE"}, "status"),
])
def test_strategy_validation(service, data, field):
    with pytest.raises(ServiceError) as e:
        service.create_reference("strategy", data, RISK)
    assert field in e.value.errors


def test_read_only_kinds_cannot_be_created_or_edited(service):
    for kind in ("commodity", "unit", "price_source"):
        with pytest.raises(ServiceError):
            service.create_reference(kind, {"name": "X", "code": "XX"}, RISK)
    with pytest.raises(ServiceError):
        service.update_reference("unit", "t", {"name": "Tonne"}, RISK, "rename unit")


def test_counterparty_name_is_immutable_and_select_options_checked(service):
    rec = service.update_reference("counterparty", "Demo Clearing Member A",
                                   {"name": "Renamed", "counterparty_type": "Financial"}, RISK, "Reclassified")
    assert rec["name"] == "Demo Clearing Member A" and rec["attrs"]["counterparty_type"] == "Financial"
    with pytest.raises(ServiceError) as e:
        service.update_reference("counterparty", "Demo Clearing Member A",
                                 {"counterparty_type": "Bank"}, RISK, "Bad type")
    assert "counterparty_type" in e.value.errors


# ── Controls ────────────────────────────────────────────────────────────────

def test_changes_require_risk_role_and_reason(service):
    with pytest.raises(ServiceError) as e:
        service.create_reference("strategy", STRATEGY, TRADER)
    assert e.value.status == 403
    with pytest.raises(ServiceError) as e:
        service.update_reference("strategy", "ORIG_HEDGE", {"name": "X", "description": "Y"}, RISK, "")
    assert "reason" in e.value.errors


def test_create_edit_deactivate_are_audited(service):
    service.create_reference("strategy", STRATEGY, RISK)
    service.update_reference("strategy", "BASIS_HEDGE", {**STRATEGY, "description": "Updated (demo)."}, RISK,
                             "Clarified description")
    service.update_reference("strategy", "BASIS_HEDGE", {**STRATEGY, "description": "Updated (demo).",
                                                         "status": "INACTIVE"}, RISK, "No longer used")
    actions = [e["action"] for e in service.audit("strategy:BASIS_HEDGE")]
    assert actions == ["REFERENCE_DEACTIVATED", "REFERENCE_UPDATED", "REFERENCE_CREATED"]
    ev = service.audit("strategy:BASIS_HEDGE")[0]
    assert ev["changes"]["status"] == {"from": "ACTIVE", "to": "INACTIVE"}


def test_delete_blocked_when_in_use_and_allowed_when_unused(service):
    with pytest.raises(ServiceError) as e:
        service.delete_reference("strategy", "ORIG_HEDGE", RISK, "Clean up")
    assert e.value.status == 409 and "Deactivate" in e.value.message and "AGD-" in e.value.errors["dependencies"]
    with pytest.raises(ServiceError) as e:
        service.delete_reference("portfolio", "Softs Trading", RISK, "Clean up")   # trades and a limit
    assert "limit" in e.value.message
    with pytest.raises(ServiceError):
        service.delete_reference("commodity", "CBOT_CORN", RISK, "Clean up")
    service.delete_reference("location", "DEMO_PORT_NS", RISK, "Never used")       # unused seed
    assert all(r["code"] != "DEMO_PORT_NS" for r in records(service, "location"))
    assert service.audit("location:DEMO_PORT_NS")[0]["action"] == "REFERENCE_DELETED"


def test_usage_counts(service):
    corn = next(r for r in records(service, "commodity") if r["code"] == "CBOT_CORN")
    assert corn["usage"]["trades"] == 6 and corn["usage"]["limits"] == ["LIM-001"]
    bushel = next(r for r in records(service, "unit") if r["code"] == "bu_corn")
    assert bushel["usage"]["trades"] == 6 and bushel["attrs"]["tonnes_per_unit"] == 0.0254012


# ── Effects on trades ───────────────────────────────────────────────────────

def test_deactivated_reference_blocks_new_trades_but_not_existing(service):
    service.update_reference("strategy", "ORIG_HEDGE", {"name": "Origination hedge", "description": "x",
                                                        "status": "INACTIVE"}, RISK, "Retired")
    with pytest.raises(ServiceError) as e:
        service.create_trade(new_trade_form(service), TRADER)
    assert "strategy" in e.value.errors
    t = service.repo.get_trade("AGD-0001")
    assert t["strategy"] == "ORIG_HEDGE"
    amended, warnings = service.amend_trade("AGD-0001", {"notes": "Desk note"}, TRADER, "Add note")
    assert amended["strategy"] == "ORIG_HEDGE" and any("inactive" in w for w in warnings)
    with pytest.raises(ServiceError):
        service.amend_trade("AGD-0006", {"strategy": "ORIG_HEDGE"}, TRADER, "Switch to retired strategy")


def test_new_counterparty_usable_for_capture(service):
    service.create_reference("counterparty", {"name": "New Demo Merchant", "counterparty_type": "Commercial"},
                             RISK)
    trade, _ = service.create_trade({**new_trade_form(service), "counterparty": "New Demo Merchant"}, TRADER)
    assert trade["counterparty"] == "New Demo Merchant"


# ── Dashboard worklist ──────────────────────────────────────────────────────

def test_attention_worklist(service):
    d = service.dashboard()
    items = {i["key"]: i for i in d["attention"]}
    assert items["limit_breach"]["count"] == 1 and items["limit_breach"]["severity"] == "critical"
    assert items["pending_review"]["count"] == d["kpis"]["pending_review"]
    assert items["market_inputs"]["count"] == 2
    assert items["unassigned"]["count"] == 1                   # the imported trade AGD-0012
    assert items["inactive_reference"]["count"] == 0 and items["inactive_reference"]["severity"] == "clear"
    assert all(i["href"].startswith("#/") for i in d["attention"])
    service.update_reference("counterparty", "Demo Clearing Member B", {"counterparty_type": "Clearing member",
                                                                        "status": "INACTIVE"}, RISK, "Offboarded")
    assert {i["key"]: i for i in service.dashboard()["attention"]}["inactive_reference"]["count"] > 0


def test_recent_activity_and_strategy_exposure(service):
    service.cancel_trade("AGD-0002", TRADER, "Client withdrew")
    d = service.dashboard()
    first = d["recent_activity"][0]
    assert first["action"] == "TRADE_CANCELLED" and first["summary"]["commodity"] == "CBOT_CORN"
    keys = {a["key"] for a in d["by_strategy"]}
    assert {"ORIG_HEDGE", "SOFTS_DIR", "Unassigned"} <= keys
    assert next(a for a in d["by_strategy"] if a["key"] == "ORIG_HEDGE")["name"] == "Origination hedge"
