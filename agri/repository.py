"""agri/repository.py
Persistence boundary for the prototype.

`Repository` is the interface the service layer depends on. The only
implementation is `InMemoryRepository`, seeded with synthetic demo data and
reset on every restart. It is intentionally NOT connected to BigQuery so demo
records can never be mistaken for live data.

To productionise, implement `Repository` against an approved system of
record (e.g. Cloud SQL / Firestore for trades and audit with append-only
controls, a governed market-data store for prices) and pass it to
`AgriService`. No calculation code needs to change.
"""
import copy
import threading
from abc import ABC, abstractmethod
from datetime import datetime

from agri import demo_data


class Repository(ABC):
    @abstractmethod
    def list_trades(self) -> list[dict]: ...
    @abstractmethod
    def get_trade(self, trade_id: str) -> dict | None: ...
    @abstractmethod
    def save_trade(self, trade: dict) -> None: ...
    @abstractmethod
    def next_trade_id(self) -> str: ...
    @abstractmethod
    def list_prices(self) -> list[dict]: ...
    @abstractmethod
    def list_limits(self) -> list[dict]: ...
    @abstractmethod
    def get_limit(self, limit_id: str) -> dict | None: ...
    @abstractmethod
    def save_limit(self, limit: dict) -> None: ...
    @abstractmethod
    def confirmations(self) -> dict: ...
    @abstractmethod
    def exception_actions(self) -> dict: ...
    @abstractmethod
    def add_exception_action(self, action: dict) -> None: ...
    @abstractmethod
    def append_audit(self, event: dict) -> None: ...
    @abstractmethod
    def list_audit(self) -> list[dict]: ...
    @abstractmethod
    def list_reference(self) -> list[dict]: ...
    @abstractmethod
    def save_reference(self, record: dict) -> None:
        """Insert or replace a reference record; record["deleted"] removes it."""


class InMemoryRepository(Repository):
    """Thread-safe in-memory store. Returns copies so callers cannot mutate state."""

    def __init__(self, now: datetime):
        self._lock = threading.RLock()
        self.reset(now)

    def reset(self, now: datetime) -> None:
        data = demo_data.build(now)
        with self._lock:
            self._trades = {t["id"]: t for t in data["trades"]}
            self._prices = data["prices"]
            self._limits = {l["id"]: l for l in data["limits"]}
            self._confirmations = {c["execution_ref"]: c for c in data["confirmations"]}
            self._actions: dict[str, list] = {}
            self._audit = sorted(data["audit"], key=lambda e: e["timestamp"])
            self._reference = {(r["kind"], r["code"]): r for r in data["reference"]}
            self._seq = len(self._trades)
            self.seeded_at = now

    def list_trades(self):
        with self._lock:
            return copy.deepcopy(list(self._trades.values()))

    def get_trade(self, trade_id):
        with self._lock:
            t = self._trades.get(trade_id)
            return copy.deepcopy(t) if t else None

    def save_trade(self, trade):
        with self._lock:
            self._trades[trade["id"]] = copy.deepcopy(trade)

    def next_trade_id(self):
        with self._lock:
            self._seq += 1
            return f"AGD-{self._seq:04d}"

    def list_prices(self):
        with self._lock:
            return copy.deepcopy(self._prices)

    def list_limits(self):
        with self._lock:
            return copy.deepcopy(list(self._limits.values()))

    def get_limit(self, limit_id):
        with self._lock:
            l = self._limits.get(limit_id)
            return copy.deepcopy(l) if l else None

    def save_limit(self, limit):
        with self._lock:
            self._limits[limit["id"]] = copy.deepcopy(limit)

    def confirmations(self):
        with self._lock:
            return copy.deepcopy(self._confirmations)

    def exception_actions(self):
        with self._lock:
            return copy.deepcopy(self._actions)

    def add_exception_action(self, action):
        with self._lock:
            self._actions.setdefault(action["exception_id"], []).append(copy.deepcopy(action))

    def append_audit(self, event):
        with self._lock:
            self._audit.append(copy.deepcopy(event))

    def list_audit(self):
        with self._lock:
            return copy.deepcopy(self._audit)

    def list_reference(self):
        with self._lock:
            return copy.deepcopy(list(self._reference.values()))

    def save_reference(self, record):
        with self._lock:
            key = (record["kind"], record["code"])
            if record.get("deleted"):
                self._reference.pop(key, None)
            else:
                self._reference[key] = copy.deepcopy({k: v for k, v in record.items() if k != "deleted"})

    def describe(self) -> dict:
        return {"store": "in-memory", "persistent": False,
                "note": getattr(self, "unavailable_note", "") +
                "Demo data is held in memory and resets when the service restarts. "
                "No database is connected for trades, limits or audit events."}
