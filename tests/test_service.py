"""Service-level tests on the seeded demo book: monitoring rules and controls."""
import pytest

from agri.monitoring import RULES
from agri.service import ServiceError
from agri.validation import validate_trade

TRADER, RISK = "demo.trader", "demo.risk-reviewer"


def new_trade_form(service):
    m = next(p for p in service.repo.list_prices() if p["commodity"] == "CBOT_CORN")
    y, mo = map(int, m["contract_month"].split("-"))
    return {
        "counterparty": "Demo Clearing Member A", "portfolio": "EU Grains Origination",
        "commodity": "CBOT_CORN", "instrument_type": "FUTURE", "direction": "BUY",
        "quantity": "5000", "contract_month": m["contract_month"], "price": str(m["price"]),
        "trade_date": service.clock().date().isoformat(), "maturity_date": f"{y}-{mo:02d}-14",
        "broker": "Demo Broker Alpha", "execution_ref": "EX-NEW-1", "strategy": "ORIG_HEDGE",
    }


def test_every_monitoring_rule_fires_on_demo_data(service):
    rules = {e["rule"] for e in service.exceptions()}
    assert rules == set(RULES)


def test_seed_trades_pass_validation_except_deliberate_import(service):
    s = service.snapshot()
    bad = [t["id"] for t in s["trades"]
           if not validate_trade(t, s["today"], None, is_new=False).ok]
    assert bad == ["AGD-0012"]  # imported trade with missing execution details


def test_dashboard_excludes_stale_and_missing_from_mtm(service):
    k = service.dashboard()["kpis"]
    assert k["mtm_excluded"] == 3          # two on stale coffee mark, one on missing cocoa mark
    assert k["limit_breaches"] == 1


def test_create_amend_review_flow_is_audited(service):
    trade, warnings = service.create_trade(new_trade_form(service), TRADER)
    assert trade["status"] == "PENDING_REVIEW" and warnings == []

    with pytest.raises(ServiceError) as e:
        service.review_trade(trade["id"], TRADER, "APPROVE", "looks fine")
    assert e.value.status == 403                                    # trader lacks the RISK role

    amended, _ = service.amend_trade(trade["id"], {"quantity": "10000"}, TRADER, "Client increased size")
    assert amended["version"] == 2 and amended["quantity"] == 10000.0

    approved = service.review_trade(trade["id"], RISK, "APPROVE", "Matches confirmation")
    assert approved["status"] == "VALIDATED" and approved["reviewed_by"] == RISK

    events = service.audit(trade["id"])
    assert [e["action"] for e in events] == ["TRADE_APPROVED", "TRADE_AMENDED", "TRADE_CREATED"]
    amend = events[1]
    assert amend["changes"]["quantity"] == {"from": 5000.0, "to": 10000.0}
    assert amend["reason"] == "Client increased size"


def test_maker_checker_blocks_self_review(service):
    trade, _ = service.create_trade(new_trade_form(service), TRADER)
    service.amend_trade(trade["id"], {"notes": "edited by reviewer"}, RISK, "Fix notes")
    with pytest.raises(ServiceError) as e:
        service.review_trade(trade["id"], RISK, "APPROVE", "self approval")
    assert "Maker-checker" in e.value.message


def test_invalid_trade_rejected_with_field_errors(service):
    f = new_trade_form(service) | {"quantity": "1234"}
    with pytest.raises(ServiceError) as e:
        service.create_trade(f, TRADER)
    assert e.value.status == 422 and "quantity" in e.value.errors


def test_amend_requires_reason_and_change(service):
    with pytest.raises(ServiceError):
        service.amend_trade("AGD-0001", {"notes": "x"}, TRADER, "")
    with pytest.raises(ServiceError) as e:
        service.amend_trade("AGD-0001", {}, TRADER, "nothing changed")
    assert e.value.status == 409


def test_cancel_removes_exposure(service):
    before = service.snapshot()["aggregates"]["commodity"]["CBOT_CORN"]["net_tonnes"]
    service.cancel_trade("AGD-0001", TRADER, "Booked in error")
    after = service.snapshot()["aggregates"]["commodity"]["CBOT_CORN"]["net_tonnes"]
    assert after == pytest.approx(before - 5080.2, abs=0.1)
    with pytest.raises(ServiceError):
        service.amend_trade("AGD-0001", {"notes": "x"}, TRADER, "after cancel")


def test_stale_price_override_includes_trades_and_is_audited(service):
    exc = next(e for e in service.exceptions() if e["rule"] == "STALE_PRICE")
    with pytest.raises(ServiceError):
        service.act_on_exception(exc["id"], TRADER, "OVERRIDE", "Accept stale mark")   # role check
    service.act_on_exception(exc["id"], RISK, "OVERRIDE", "Exchange holiday, mark confirmed")
    s = service.snapshot()
    stale_vals = [v for v in s["valuations"].values() if v["status"].startswith("STALE")]
    assert stale_vals and all(v["status"] == "STALE_ACCEPTED" for v in stale_vals)
    assert service.dashboard()["kpis"]["mtm_excluded"] == 1
    assert next(e for e in s["exceptions"] if e["id"] == exc["id"])["state"] == "OVERRIDDEN"
    assert service.audit(exc["id"])[0]["action"] == "EXCEPTION_OVERRIDDEN"


def test_non_overridable_exception(service):
    exc = next(e for e in service.exceptions() if e["rule"] == "MISSING_PRICE")
    with pytest.raises(ServiceError) as e:
        service.act_on_exception(exc["id"], RISK, "OVERRIDE", "please")
    assert e.value.status == 409
    service.act_on_exception(exc["id"], TRADER, "ACKNOWLEDGE", "Chasing data vendor")
    assert next(x for x in service.exceptions() if x["id"] == exc["id"])["state"] == "ACKNOWLEDGED"


def test_limit_change_controls(service):
    with pytest.raises(ServiceError):
        service.update_limit("LIM-001", TRADER, {"limit_value": 20000}, "raise it")
    with pytest.raises(ServiceError) as e:
        service.update_limit("LIM-001", RISK, {"limit_value": -1}, "bad value")
    assert "limit_value" in e.value.errors
    service.update_limit("LIM-001", RISK, {"limit_value": 20000}, "Approved by risk committee (demo)")
    lim = next(l for l in service.risk_view()["limits"] if l["id"] == "LIM-001")
    assert lim["status"] == "OK"
    ev = service.audit("LIM-001")[0]
    assert ev["changes"]["limit_value"] == {"from": 12000.0, "to": 20000.0}


@pytest.mark.parametrize("day_offset", range(0, 366, 7))
def test_demo_book_is_stable_on_any_date(day_offset):
    """Demo data is generated relative to 'now'; the story must hold all year."""
    from datetime import datetime, timedelta, timezone
    from agri.repository import InMemoryRepository
    from agri.service import AgriService
    n = datetime(2026, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=day_offset)
    svc = AgriService(repo=InMemoryRepository(n), clock=lambda: n)
    k = svc.dashboard()["kpis"]
    assert {e["rule"] for e in svc.exceptions()} == set(RULES)
    assert (k["limit_breaches"], k["limit_warnings"], k["mtm_excluded"]) == (1, 2, 3)
