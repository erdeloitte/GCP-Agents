"""agri/bq_repository.py
BigQuery persistence for the prototype (dataset BQ_DATASET, tables agri_*).

Design
  - Append-only: every save appends a row; the current state of a trade or
    limit is its latest row. Nothing is updated or deleted, so the tables are
    also a full history.
  - Generations: "Reset demo data" starts a new generation and re-seeds it;
    earlier generations stay in BigQuery but are no longer read.
  - No query jobs: rows are written with streaming inserts (tabledata.insertAll)
    and read with tabledata.list, so the service account only needs data access
    on the dataset (BigQuery Data Editor), not bigquery.jobs.create.
  - Write-through cache: state is loaded once at start-up and kept in memory;
    every write goes to BigQuery first and only then into the cache. Run a
    single instance (Cloud Run --max-instances=1) so instances never diverge.
  - Market prices are a synthetic feed: one snapshot is published per day and
    stored in agri_market_prices.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from agri import demo_data
from agri.repository import InMemoryRepository

logger = logging.getLogger(__name__)

S, F, I, T, B = "STRING", "FLOAT64", "INT64", "TIMESTAMP", "BOOL"
SCHEMAS = {
    "agri_meta": [("generation", I), ("event", S), ("at", T), ("actor", S), ("note", S)],
    "agri_trades": [("generation", I), ("saved_at", T), ("id", S), ("version", I), ("status", S),
                    ("counterparty", S), ("portfolio", S), ("commodity", S), ("instrument_type", S),
                    ("option_type", S), ("strike", F), ("direction", S), ("quantity", F), ("quantity_unit", S),
                    ("contract_month", S), ("pricing_type", S), ("reference_market", S), ("price", F),
                    ("currency", S), ("price_unit", S), ("trade_date", S), ("effective_date", S),
                    ("maturity_date", S), ("broker", S), ("execution_ref", S), ("source", S), ("notes", S),
                    ("created_by", S), ("created_at", S), ("updated_by", S), ("updated_at", S),
                    ("reviewed_by", S), ("reviewed_at", S)],
    "agri_limits": [("generation", I), ("saved_at", T), ("id", S), ("dimension", S), ("key", S), ("metric", S),
                    ("limit_value", F), ("unit", S), ("warn_pct", F), ("owner", S), ("updated_by", S),
                    ("updated_at", S)],
    "agri_confirmations": [("generation", I), ("execution_ref", S), ("quantity", F), ("price", F),
                           ("received_at", S)],
    "agri_exception_actions": [("generation", I), ("exception_id", S), ("action", S), ("actor", S),
                               ("reason", S), ("timestamp", S), ("context", S)],
    "agri_audit_events": [("generation", I), ("id", S), ("timestamp", T), ("actor", S), ("action", S),
                          ("entity_type", S), ("entity_id", S), ("reason", S), ("changes_json", S),
                          ("before_json", S), ("after_json", S)],
    "agri_market_prices": [("generation", I), ("snapshot_id", S), ("published_at", T), ("commodity", S),
                           ("contract_month", S), ("price", F), ("price_unit", S), ("as_of", S), ("source", S)],
}
DESCRIPTIONS = {
    "agri_meta": "AgriRisk prototype: data generations (seed / reset events). SYNTHETIC DEMO DATA.",
    "agri_trades": "AgriRisk prototype: trade versions, append-only; latest row per id is current. SYNTHETIC DEMO DATA.",
    "agri_limits": "AgriRisk prototype: limit versions, append-only. SYNTHETIC DEMO DATA.",
    "agri_confirmations": "AgriRisk prototype: simulated broker confirmations. SYNTHETIC DEMO DATA.",
    "agri_exception_actions": "AgriRisk prototype: acknowledgements and overrides. SYNTHETIC DEMO DATA.",
    "agri_audit_events": "AgriRisk prototype: append-only audit trail. SYNTHETIC DEMO DATA.",
    "agri_market_prices": "AgriRisk prototype: synthetic market-mark snapshots (not from a market-data provider).",
}


class PersistenceError(Exception):
    """A write to the system of record failed; the change was not applied."""


class BigQueryStore:
    """Thin adapter over google-cloud-bigquery (data-plane calls only)."""

    def __init__(self, project: str | None, dataset: str, client=None):
        if client is None:
            from google.cloud import bigquery
            client = bigquery.Client(project=project)
        self.client = client
        self.dataset = f"{project or client.project}.{dataset}"

    def ensure_tables(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from google.cloud import bigquery

        def ensure(name):
            table = bigquery.Table(f"{self.dataset}.{name}",
                                   schema=[bigquery.SchemaField(c, t) for c, t in SCHEMAS[name]])
            table.description = DESCRIPTIONS[name]
            self.client.create_table(table, exists_ok=True)
        with ThreadPoolExecutor(max_workers=len(SCHEMAS)) as ex:
            list(ex.map(ensure, SCHEMAS))

    def read(self, name: str) -> list[dict]:
        return [dict(r) for r in self.client.list_rows(f"{self.dataset}.{name}")]

    def append(self, name: str, rows: list[dict]) -> None:
        if not rows:
            return
        try:
            errors = self.client.insert_rows_json(f"{self.dataset}.{name}", rows,
                                                  row_ids=[str(uuid.uuid4()) for _ in rows])
        except Exception as e:   # auth, network, quota …
            raise PersistenceError(f"BigQuery write to {name} failed ({type(e).__name__}).") from e
        if errors:
            raise PersistenceError(f"BigQuery rejected rows for {name}: {str(errors)[:300]}")


def _ts(v) -> str:
    return v.isoformat() if isinstance(v, datetime) else str(v)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BigQueryRepository(InMemoryRepository):
    """InMemoryRepository whose state is loaded from, and written through to, BigQuery."""

    def __init__(self, now: datetime, store, clock=None):
        import threading
        self._lock = threading.RLock()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.store = store
        self.store.ensure_tables()
        self._load_or_seed(now)

    # ── Loading ──────────────────────────────────────────────────────────────
    def _load_or_seed(self, now: datetime) -> None:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(SCHEMAS)) as ex:   # tables are read in parallel
            data = dict(zip(SCHEMAS, ex.map(self.store.read, SCHEMAS)))
        meta = data["agri_meta"]
        if not meta:
            self._seed(now, generation=1, actor=demo_data.SEED_ACTOR, note="Initial synthetic demo data")
            return
        gen = max(int(m["generation"]) for m in meta)
        cur = lambda rows: [r for r in rows if int(r["generation"]) == gen]   # noqa: E731

        def latest(rows, key):
            out = {}
            for r in sorted(rows, key=lambda r: (_ts(r["saved_at"]), r.get("version") or 0)):
                out[r[key]] = {k: v for k, v in r.items() if k not in ("generation", "saved_at")}
            return out

        trades = latest(cur(data["agri_trades"]), "id")
        limits = latest(cur(data["agri_limits"]), "id")
        confirmations = {r["execution_ref"]: {k: v for k, v in r.items() if k != "generation"}
                         for r in cur(data["agri_confirmations"])}
        actions: dict[str, list] = {}
        for a in sorted(cur(data["agri_exception_actions"]), key=lambda a: a["timestamp"]):
            actions.setdefault(a["exception_id"], []).append({k: v for k, v in a.items() if k != "generation"})
        audit = sorted((self._audit_from_row(r) for r in cur(data["agri_audit_events"])),
                       key=lambda e: e["timestamp"])
        with self._lock:
            self.generation = gen
            self._trades, self._limits = trades, limits
            self._confirmations, self._actions, self._audit = confirmations, actions, audit
            self._seq = max([int(t.split("-")[-1]) for t in trades if t.split("-")[-1].isdigit()] or [0])
            self.seeded_at = min((datetime.fromisoformat(_ts(m["at"])) for m in meta
                                  if int(m["generation"]) == gen), default=now)
        self._load_prices(now, data["agri_market_prices"])
        logger.info("Loaded AgriRisk generation %s from BigQuery: %s trades, %s audit events",
                    gen, len(trades), len(audit))

    def _load_prices(self, now: datetime, rows: list[dict]) -> None:
        """Use today's synthetic snapshot; publish one if none exists yet."""
        rows = [r for r in rows if int(r["generation"]) == self.generation]
        today = now.date().isoformat()
        snaps: dict[str, list] = {}
        for r in rows:
            snaps.setdefault(r["snapshot_id"], []).append(r)
        todays = [sid for sid in snaps if sid.startswith(today)]
        if todays:
            sid = max(todays)
            self._prices = [{k: v for k, v in r.items() if k not in ("generation", "snapshot_id", "published_at")}
                            for r in snaps[sid]]
            self._prices_date = now.date()
            return
        self._publish_prices(now)

    def _publish_prices(self, now: datetime) -> None:
        prices = demo_data.synthetic_marks(now, list(self._trades.values()))
        sid = f"{now.date().isoformat()}-{uuid.uuid4().hex[:8]}"
        self.store.append("agri_market_prices", [{**p, "generation": self.generation, "snapshot_id": sid,
                                                  "published_at": _now()} for p in prices])
        self._prices = prices
        self._prices_date = now.date()

    @staticmethod
    def _audit_from_row(r: dict) -> dict:
        load = lambda v: json.loads(v) if v else None   # noqa: E731
        return {"id": r["id"], "timestamp": _ts(r["timestamp"]), "actor": r["actor"], "action": r["action"],
                "entity_type": r["entity_type"], "entity_id": r["entity_id"], "reason": r["reason"] or "",
                "changes": load(r["changes_json"]) or {}, "before": load(r["before_json"]),
                "after": load(r["after_json"])}

    @staticmethod
    def _audit_to_row(e: dict, gen: int) -> dict:
        dump = lambda v: json.dumps(v, default=str) if v is not None else None   # noqa: E731
        return {"generation": gen, "id": e["id"], "timestamp": e["timestamp"], "actor": e["actor"],
                "action": e["action"], "entity_type": e["entity_type"], "entity_id": e["entity_id"],
                "reason": e.get("reason", ""), "changes_json": dump(e.get("changes") or {}),
                "before_json": dump(e.get("before")), "after_json": dump(e.get("after"))}

    # ── Seeding / reset ──────────────────────────────────────────────────────
    def _seed(self, now: datetime, generation: int, actor: str, note: str) -> None:
        data = demo_data.build(now)
        stamp = _now()
        st = self.store
        st.append("agri_trades", [{**t, "generation": generation, "saved_at": stamp} for t in data["trades"]])
        st.append("agri_limits", [{**l, "generation": generation, "saved_at": stamp} for l in data["limits"]])
        st.append("agri_confirmations", [{**c, "generation": generation} for c in data["confirmations"]])
        st.append("agri_audit_events", [self._audit_to_row(e, generation) for e in data["audit"]])
        # Meta last: a generation only becomes current once its data is written.
        st.append("agri_meta", [{"generation": generation, "event": "SEED", "at": stamp, "actor": actor,
                                 "note": note}])
        with self._lock:
            self.generation = generation
            self._trades = {t["id"]: t for t in data["trades"]}
            self._limits = {l["id"]: l for l in data["limits"]}
            self._confirmations = {c["execution_ref"]: c for c in data["confirmations"]}
            self._actions = {}
            self._audit = sorted(data["audit"], key=lambda e: e["timestamp"])
            self._seq = len(self._trades)
            self.seeded_at = now
        self._publish_prices(now)

    def reset(self, now: datetime) -> None:
        self._seed(now, generation=self.generation + 1, actor="demo reset",
                   note="Synthetic demo data reloaded; previous generation retained")

    def list_prices(self):
        """Publish a new synthetic snapshot when the day rolls over."""
        now = self.clock()
        with self._lock:
            if getattr(self, "_prices_date", None) != now.date():
                self._publish_prices(now)
        return super().list_prices()

    # ── Writes: BigQuery first, then cache ───────────────────────────────────
    def save_trade(self, trade):
        with self._lock:
            self.store.append("agri_trades", [{**trade, "generation": self.generation, "saved_at": _now()}])
            super().save_trade(trade)

    def save_limit(self, limit):
        with self._lock:
            self.store.append("agri_limits", [{**limit, "generation": self.generation, "saved_at": _now()}])
            super().save_limit(limit)

    def add_exception_action(self, action):
        with self._lock:
            self.store.append("agri_exception_actions", [{**action, "generation": self.generation}])
            super().add_exception_action(action)

    def append_audit(self, event):
        with self._lock:
            self.store.append("agri_audit_events", [self._audit_to_row(event, self.generation)])
            super().append_audit(event)

    def describe(self) -> dict:
        return {"store": "bigquery", "persistent": True, "dataset": self.store.dataset,
                "generation": self.generation, "tables": sorted(SCHEMAS),
                "note": f"Data is stored in BigQuery ({self.store.dataset}, tables agri_*), append-only. "
                        "All records are synthetic demo data."}
