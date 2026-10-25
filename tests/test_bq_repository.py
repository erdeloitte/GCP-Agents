"""BigQueryRepository against an in-memory fake of the BigQuery data-plane calls."""
import copy

import pytest

from agri.bq_repository import SCHEMAS, BigQueryRepository, PersistenceError
from agri.service import AgriService


class FakeStore:
    def __init__(self):
        self.dataset = "proj.ds"
        self.tables = {}
        self.fail = False

    def ensure_tables(self):
        for name in SCHEMAS:
            self.tables.setdefault(name, [])

    def read(self, name):
        return copy.deepcopy(self.tables[name])

    def append(self, name, rows):
        if self.fail:
            raise PersistenceError("simulated outage")
        cols = {c for c, _ in SCHEMAS[name]}
        for r in rows:
            unknown = set(r) - cols
            assert not unknown, f"{name}: columns not in schema {unknown}"
        self.tables[name].extend(copy.deepcopy(rows))


@pytest.fixture
def store():
    return FakeStore()


def svc(store, now):
    return AgriService(repo=BigQueryRepository(now, store, clock=lambda: now), clock=lambda: now)


def test_first_start_seeds_every_table(store, now):
    s = svc(store, now)
    assert all(store.tables[t] for t in SCHEMAS if t != "agri_exception_actions")
    assert store.tables["agri_meta"][0]["generation"] == 1
    assert s.repo.describe()["persistent"] is True


def test_restart_reloads_identical_state(store, now):
    a = svc(store, now)
    b = svc(store, now)
    assert a.repo.list_trades() == b.repo.list_trades()
    assert a.dashboard()["kpis"] == b.dashboard()["kpis"]
    assert len(store.tables["agri_meta"]) == 1                      # not re-seeded
    assert len({r["snapshot_id"] for r in store.tables["agri_market_prices"]}) == 1   # same-day snapshot reused


def test_changes_survive_restart(store, now, monkeypatch):
    import agri.bq_repository as bqr
    a = svc(store, now)
    t, _ = a.create_trade({**_capture_form(a), "execution_ref": "EX-BQ-1"}, "demo.trader")
    a.cancel_trade("AGD-0001", "demo.trader", "Client withdrew")
    b = svc(store, now)
    assert b.repo.get_trade(t["id"])["execution_ref"] == "EX-BQ-1"
    assert b.repo.get_trade("AGD-0001")["status"] == "CANCELLED"
    assert b.repo.next_trade_id() == f"AGD-{int(t['id'][-4:]) + 1:04d}"
    actions = [e["action"] for e in b.audit("AGD-0001")]
    assert actions[0] == "TRADE_CANCELLED" and b.audit("AGD-0001")[0]["changes"]["status"]["to"] == "CANCELLED"


def test_override_survives_restart(store, now):
    a = svc(store, now)
    exc = next(e for e in a.exceptions() if e["rule"] == "STALE_PRICE")
    a.act_on_exception(exc["id"], "demo.risk-reviewer", "OVERRIDE", "Desk confirmed level")
    b = svc(store, now)
    assert next(e for e in b.exceptions() if e["id"] == exc["id"])["state"] == "OVERRIDDEN"


def test_failed_write_changes_nothing(store, now):
    a = svc(store, now)
    before = a.repo.get_trade("AGD-0001")
    store.fail = True
    with pytest.raises(PersistenceError):
        a.cancel_trade("AGD-0001", "demo.trader", "Client withdrew")
    assert a.repo.get_trade("AGD-0001") == before


def test_reset_starts_new_generation_and_keeps_history(store, now):
    a = svc(store, now)
    a.cancel_trade("AGD-0001", "demo.trader", "Client withdrew")
    rows_before = len(store.tables["agri_trades"])
    a.reset_demo("demo.risk-reviewer")
    assert a.repo.get_trade("AGD-0001")["status"] != "CANCELLED"
    assert len(store.tables["agri_trades"]) > rows_before          # nothing deleted
    b = svc(store, now)
    assert b.repo.generation == 2 and b.repo.get_trade("AGD-0001")["status"] != "CANCELLED"


def _capture_form(s):
    mark = next(p for p in s.market_data() if p["commodity"] == "CBOT_CORN" and not p["stale"])
    y, m = mark["contract_month"].split("-")
    return {"counterparty": "Demo Clearing Member A", "portfolio": "EU Grains Origination",
            "commodity": "CBOT_CORN", "instrument_type": "FUTURE", "direction": "BUY", "quantity": "5000",
            "contract_month": mark["contract_month"], "price": mark["price"], "trade_date": s.clock().date().isoformat(),
            "maturity_date": f"{y}-{m}-14", "broker": "Demo Broker Alpha"}
