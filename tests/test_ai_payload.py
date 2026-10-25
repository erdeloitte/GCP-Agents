"""The AI payload is an allow-list: nothing identifying may reach an LLM provider."""
import json

import pytest

from agri import ai_payload


def _sensitive_values(service):
    out = set()
    for t in service.repo.list_trades():
        out |= {t["id"], t["counterparty"], t["portfolio"], t.get("broker"), t.get("execution_ref"),
                t.get("notes"), t.get("created_by"), t.get("updated_by")}
    for l in service.repo.list_limits():
        out.add(l["id"])
        if l["dimension"] in ("counterparty", "portfolio"):
            out.add(l["key"])
    return {v for v in out if v and len(str(v)) > 3}


def _assert_clean(service, payload):
    blob = json.dumps(payload)
    leaked = [v for v in _sensitive_values(service) if v in blob]
    assert not leaked, leaked


def test_trade_payload_is_minimal(service):
    p = ai_payload.build(service, "trade", "AGD-0001")
    _assert_clean(service, p)
    assert {"commodity", "direction", "quantity", "valuation"} <= set(p["data"])
    assert p["data"]["valuation"]["assumptions"]          # methodology travels with the figures


def test_every_exception_and_limit_payload_is_clean(service):
    s = service.snapshot()
    for e in s["exceptions"]:
        _assert_clean(service, ai_payload.build(service, "exception", e["id"]))
    for l in s["limits"]:
        p = ai_payload.build(service, "limit", l["id"])
        _assert_clean(service, p)
        if l["dimension"] in ("counterparty", "portfolio"):
            assert p["data"]["key"].startswith("<")


def test_bad_topic_and_missing_entity(service):
    with pytest.raises(ValueError):
        ai_payload.build(service, "accounting", "x")
    with pytest.raises(LookupError):
        ai_payload.build(service, "limit", "NOPE")
