"""agri/service.py
Application service: the only place that changes state. Every write is
validated, permission-checked against the (demo) role, and audited.

Controls enforced here:
  - Maker-checker: a trade cannot be approved by the user who last changed it.
  - Overrides, limit changes and trade reviews require the RISK role.
  - Every controlled action requires a written reason.
  - AI code paths have no access to these methods (see agri_api.py).
"""
from datetime import datetime, timezone

from agri import reference as ref
from agri import refdata
from agri import risk
from agri.audit import make_event
from agri.models import ExceptionAction, TRADE_EDITABLE_FIELDS, Trade, utc_now_iso
from agri.monitoring import RULES, SEVERITY_ORDER, detect_exceptions, price_key

REF_LINK_FIELDS = {k: m["trade_field"] for k, m in refdata.KINDS.items() if m.get("trade_field")}
DATA_QUALITY_RULES = {"MISSING_DATA", "UNCONFIRMED", "CONFIRM_MISMATCH", "PRICE_OUT_OF_BAND"}
from agri.repository import InMemoryRepository, Repository
from agri.validation import validate_trade
from agri.valuation import DEFAULT_STALE_HOURS, INCLUDED_STATUSES, value_trade

ROLES = {"demo.trader": "TRADER", "demo.risk-reviewer": "RISK"}
MIN_REASON = 5
MAX_REASON = 500


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400, errors: dict | None = None,
                 warnings: list | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors or {}
        self.warnings = warnings or []


def _require_reason(reason: str | None) -> str:
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise ServiceError("A reason of at least 5 characters is required for this action.",
                           errors={"reason": "Reason is required."})
    if len(reason) > MAX_REASON:
        raise ServiceError("Reason is too long.", errors={"reason": f"Max {MAX_REASON} characters."})
    return reason


def _require_role(user: str, role: str, action: str) -> None:
    if ROLES.get(user) != role:
        raise ServiceError(f"'{action}' requires the {role} role (demo identity "
                           f"'demo.risk-reviewer'). Current demo identity: {user}.", status=403)


class AgriService:
    def __init__(self, repo: Repository | None = None, clock=None,
                 stale_hours: float = DEFAULT_STALE_HOURS):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.repo = repo or InMemoryRepository(self.clock())
        self.stale_hours = stale_hours

    # ── Read side ────────────────────────────────────────────────────────────
    def _price_map(self) -> dict:
        return {price_key(p["commodity"], p["contract_month"]): p for p in self.repo.list_prices()}

    def _exception_state(self, exc_id: str, actions: dict) -> tuple[str, dict | None]:
        acts = actions.get(exc_id) or []
        if not acts:
            return "OPEN", None
        last = acts[-1]
        return ("OVERRIDDEN" if last["action"] == "OVERRIDE" else "ACKNOWLEDGED"), last

    def snapshot(self) -> dict:
        """Compute the full deterministic state at the current clock time."""
        now = self.clock()
        today = now.date()
        trades = self.repo.list_trades()
        prices = self._price_map()
        actions = self.repo.exception_actions()

        def accepted(pk: str, mark: dict | None) -> bool:
            # An override only applies to the specific mark it was recorded against.
            return any(a["action"] == "OVERRIDE" and mark and a.get("context") == mark["as_of"]
                       for a in actions.get(f"STALE_PRICE:{pk}", []))

        valuations = {}
        for t in trades:
            pk = price_key(t["commodity"], t["contract_month"])
            mark = prices.get(pk)
            valuations[t["id"]] = value_trade(t, mark, now, self.stale_hours, accepted(pk, mark))

        aggregates = {d: risk.aggregate(trades, valuations, d, today) for d in risk.DIMENSIONS}
        limit_results = risk.evaluate_limits(self.repo.list_limits(), aggregates)
        exceptions = detect_exceptions(trades, valuations, self.repo.confirmations(),
                                       limit_results, today)
        for e in exceptions:
            e["state"], e["last_action"] = self._exception_state(e["id"], actions)
            e["detected_at"] = now.replace(microsecond=0).isoformat()
        return {"now": now, "today": today, "trades": trades, "prices": prices,
                "valuations": valuations, "aggregates": aggregates,
                "limits": limit_results, "exceptions": exceptions}

    def dashboard(self, portfolio: str | None = None, maturity_days: int = 30) -> dict:
        """Portfolio overview. `portfolio` scopes trades, positions and trade
        exceptions; limits are always evaluated firm-wide."""
        if portfolio and portfolio not in {r["code"] for r in self.repo.list_reference() if r["kind"] == "portfolio"}:
            raise ServiceError("Unknown portfolio.", status=400)
        s = self.snapshot()
        now, today = s["now"], s["today"]
        trades = [t for t in s["trades"] if not portfolio or t["portfolio"] == portfolio]
        ids = {t["id"] for t in trades}
        open_trades = [t for t in trades if s["valuations"][t["id"]]["status"] != "EXCLUDED"]
        open_vals = [s["valuations"][t["id"]] for t in open_trades]
        incl = [v for v in open_vals if v["status"] in INCLUDED_STATUSES]
        exceptions = [e for e in s["exceptions"] if not e.get("trade_id") or e["trade_id"] in ids]
        open_exc = [e for e in exceptions if e["state"] == "OPEN"]
        used_pks = {price_key(t["commodity"], t["contract_month"]) for t in open_trades}
        used_prices = [s["prices"][pk] for pk in used_pks if pk in s["prices"]]
        as_ofs = sorted(p["as_of"] for p in used_prices)
        maturities = []
        for t in open_trades:
            days = (datetime.fromisoformat(t["maturity_date"]).date() - today).days
            if days <= maturity_days:
                maturities.append({k: t[k] for k in ("id", "commodity", "instrument_type", "direction",
                                                     "quantity", "quantity_unit", "contract_month",
                                                     "maturity_date", "status")} | {"days": days})
        freshness = {
            "marks_in_use": len(used_pks),
            "missing_marks": len(used_pks) - len(used_prices),
            "stale_marks": sum(1 for p in used_prices
                               if (now - datetime.fromisoformat(p["as_of"])).total_seconds()
                               > self.stale_hours * 3600),
            "oldest_mark": as_ofs[0] if as_ofs else None,
            "newest_mark": as_ofs[-1] if as_ofs else None,
            "stale_threshold_hours": self.stale_hours,
            "source": "SYNTHETIC DEMO DATA",
        }
        reference = self.repo.list_reference()
        inactive = {(REF_LINK_FIELDS[r["kind"]], r["code"]) for r in reference
                    if r["kind"] in REF_LINK_FIELDS and r["status"] != refdata.ACTIVE}
        on_inactive = [t["id"] for t in open_trades
                       if any((f, t.get(f)) in inactive for f in REF_LINK_FIELDS.values())]
        names = {(r["kind"], r["code"]): r["name"] for r in reference}
        by_strategy = []
        for a in risk.aggregate(open_trades, s["valuations"], "strategy", today).values():
            by_strategy.append({**a, "name": names.get(("strategy", a["key"]), a["key"])})
        by_strategy.sort(key=lambda a: -a["gross_notional_usd"])
        return {
            "valued_at": now.replace(microsecond=0).isoformat(),
            "scope": {"portfolio": portfolio or None, "limits": "firm-wide"},
            "attention": self._attention(s, trades, open_trades, open_exc, freshness, on_inactive, today),
            "recent_activity": self._recent_activity(s, ids if portfolio else None),
            "by_strategy": by_strategy,
            "data_status": {"store": self.repo.describe(), "inactive_reference_in_use": len(on_inactive),
                            "reference_records": len(reference)},
            "kpis": {
                "open_trades": len(open_vals),
                "pending_review": sum(1 for t in trades if t["status"] == "PENDING_REVIEW"),
                "gross_tonnes": round(sum(abs(v["delta_t"] or 0) for v in open_vals), 1),
                "net_tonnes": round(sum(v["delta_t"] or 0 for v in open_vals), 1),
                "gross_notional_usd": round(sum(v["notional_usd"] for v in incl), 2),
                "mtm_usd": round(sum(v["mtm_usd"] for v in incl), 2),
                "mtm_included": len(incl),
                "mtm_excluded": len(open_vals) - len(incl),
                "limit_breaches": sum(1 for l in s["limits"] if l["status"] == "BREACH"),
                "limit_warnings": sum(1 for l in s["limits"] if l["status"] == "WARN"),
                "open_exceptions": len(open_exc),
                "critical_exceptions": sum(1 for e in open_exc if e["severity"] == "CRITICAL"),
            },
            "positions": risk.positions(open_trades, s["valuations"]),
            "by_commodity": list(risk.aggregate(trades, s["valuations"], "commodity", today).values()),
            "by_maturity": list(risk.aggregate(trades, s["valuations"], "maturity_bucket", today).values()),
            "limits": s["limits"],
            "exceptions": open_exc[:10],
            "exceptions_by_severity": {sev: sum(1 for e in open_exc if e["severity"] == sev)
                                       for sev in SEVERITY_ORDER},
            "maturities": sorted(maturities, key=lambda m: (m["days"], m["id"])),
            "maturity_window_days": maturity_days,
            "freshness": freshness,
        }

    @staticmethod
    def _attention(s, trades, open_trades, open_exc, freshness, on_inactive, today) -> list[dict]:
        """Deterministic worklist: what needs attention and where to go next."""
        def n_rules(rules):
            return sum(1 for e in open_exc if e["rule"] in rules)
        breaches = sum(1 for l in s["limits"] if l["status"] == "BREACH")
        warns = sum(1 for l in s["limits"] if l["status"] == "WARN")
        crit = [e for e in open_exc if e["severity"] in ("CRITICAL", "HIGH") and not e["rule"].startswith("LIMIT")]
        due = sum(1 for t in open_trades
                  if (datetime.fromisoformat(t["maturity_date"]).date() - today).days <= 7)
        inputs = freshness["stale_marks"] + freshness["missing_marks"]
        items = [
            ("limit_breach", "Limit breaches", breaches, "critical",
             "Exposure above a configured limit (firm-wide).", "#/risk", "Review limits"),
            ("exceptions", "Critical and high exceptions", len(crit),
             "critical" if any(e["severity"] == "CRITICAL" for e in crit) else "high",
             "Valuation, maturity and data exceptions needing a decision.", "#/exceptions", "Open queue"),
            ("pending_review", "Trades awaiting review", sum(1 for t in trades if t["status"] == "PENDING_REVIEW"),
             "action", "Four-eyes approval by the Risk role.", "#/blotter?status=PENDING_REVIEW", "Review trades"),
            ("market_inputs", "Stale or missing market inputs", inputs, "warning",
             "Dependent trades are excluded from MtM totals until resolved or overridden.",
             "#/market-data", "View marks"),
            ("data_quality", "Data-quality issues", n_rules(DATA_QUALITY_RULES), "warning",
             "Missing execution details, unmatched confirmations, off-market prices.", "#/monitoring", "Monitoring"),
            ("limit_warning", "Limits near threshold", warns, "warning",
             "Utilisation above the warning level.", "#/risk", "Review limits"),
            ("maturing", "Maturing within 7 days", due, "action",
             "Open trades to close out, roll or deliver.", "#/monitoring", "Maturity calendar"),
            ("inactive_reference", "Open trades on inactive reference data", len(on_inactive), "warning",
             "Trades reference a deactivated commodity, strategy, counterparty or other record.",
             "#/data-setup", "Data Setup"),
            ("unassigned", "Open trades without a strategy",
             sum(1 for t in open_trades if not t.get("strategy")), "info",
             "Shown as 'Unassigned' in exposure by strategy.", "#/blotter?strategy=UNASSIGNED", "View trades"),
        ]
        return [{"key": k, "label": lbl, "count": n, "severity": sev if n else "clear", "detail": d,
                 "href": href, "action": act} for k, lbl, n, sev, d, href, act in items]

    def _recent_activity(self, s, scope_ids: set | None, limit: int = 10) -> list[dict]:
        trades = {t["id"]: t for t in s["trades"]}
        out = []
        for e in reversed(self.repo.list_audit()):
            if e["entity_type"] == "trade" and scope_ids is not None and e["entity_id"] not in scope_ids:
                continue
            row = {k: e[k] for k in ("timestamp", "actor", "action", "entity_type", "entity_id", "reason")}
            t = trades.get(e["entity_id"]) if e["entity_type"] == "trade" else None
            if t:
                row["summary"] = {k: t[k] for k in ("direction", "quantity", "quantity_unit", "commodity",
                                                     "instrument_type", "contract_month", "status")}
            out.append(row)
            if len(out) == limit:
                break
        return out

    def list_trades(self, filters: dict | None = None) -> list[dict]:
        s = self.snapshot()
        f = {k: v for k, v in (filters or {}).items() if v}
        q = (f.pop("q", "") or "").lower()
        exc_count: dict[str, int] = {}
        for e in s["exceptions"]:
            if e.get("trade_id") and e["state"] == "OPEN":
                exc_count[e["trade_id"]] = exc_count.get(e["trade_id"], 0) + 1
        out = []
        for t in s["trades"]:
            if any(str(t.get(k) or "").upper() != ("" if v == "UNASSIGNED" else str(v).upper())
                   for k, v in f.items()
                   if k in ("commodity", "status", "portfolio", "instrument_type", "direction", "counterparty",
                            "strategy", "delivery_location", "broker")):
                continue
            if q and q not in " ".join(str(t.get(k, "")) for k in
                                       ("id", "counterparty", "portfolio", "commodity", "execution_ref",
                                        "reference_market", "notes", "broker", "strategy",
                                        "delivery_location")).lower():
                continue
            v = s["valuations"][t["id"]]
            out.append({**t, "valuation": {k: v[k] for k in
                                           ("status", "mtm_usd", "delta_t", "mark_price", "mark_as_of")},
                        "open_exceptions": exc_count.get(t["id"], 0)})
        return sorted(out, key=lambda x: x["id"], reverse=True)

    def trade_detail(self, trade_id: str) -> dict:
        s = self.snapshot()
        t = next((t for t in s["trades"] if t["id"] == trade_id), None)
        if not t:
            raise ServiceError(f"Trade {trade_id} not found.", status=404)
        return {
            "trade": t,
            "valuation": s["valuations"][trade_id],
            "maturity_bucket": risk.maturity_bucket(t["maturity_date"], s["today"]),
            "exceptions": [e for e in s["exceptions"] if e.get("trade_id") == trade_id],
            "audit": [e for e in self.repo.list_audit() if e["entity_id"] == trade_id][::-1],
            "confirmation": self.repo.confirmations().get(t.get("execution_ref") or "", None),
        }

    def risk_view(self) -> dict:
        s = self.snapshot()
        return {"valued_at": s["now"].replace(microsecond=0).isoformat(),
                "aggregates": s["aggregates"], "limits": s["limits"],
                "metrics": risk.METRICS, "dimensions": risk.DIMENSIONS}

    def derivation(self, dimension: str, key: str) -> dict:
        if dimension not in risk.DIMENSIONS:
            raise ServiceError("Unknown dimension.")
        s = self.snapshot()
        d = risk.derivation(s["trades"], s["valuations"], dimension, key, s["today"])
        d["limits"] = [l for l in s["limits"] if l["dimension"] == dimension and l["key"] == key]
        d["valued_at"] = s["now"].replace(microsecond=0).isoformat()
        return d

    def exceptions(self) -> list[dict]:
        return self.snapshot()["exceptions"]

    def market_data(self) -> list[dict]:
        now = self.clock()
        out = []
        for p in self.repo.list_prices():
            age = (now - datetime.fromisoformat(p["as_of"])).total_seconds() / 3600
            out.append({**p, "age_hours": round(age, 1), "stale": age > self.stale_hours})
        return sorted(out, key=lambda p: (p["commodity"], p["contract_month"]))

    def audit(self, entity_id: str | None = None, limit: int = 200) -> list[dict]:
        ev = self.repo.list_audit()
        if entity_id:
            ev = [e for e in ev if e["entity_id"] == entity_id]
        return ev[::-1][:limit]

    # ── Write side ───────────────────────────────────────────────────────────
    def _mark_for(self, data: dict) -> float | None:
        p = self._price_map().get(price_key(str(data.get("commodity", "")).upper(),
                                            str(data.get("contract_month", ""))))
        return p["price"] if p else None

    def _lookups(self, before: dict | None, data: dict) -> tuple[dict, list]:
        """Active Data Setup codes; an amendment may keep a value that has since been
        deactivated (with a warning) but may not switch to an inactive one."""
        lk = refdata.lookups(self.repo.list_reference())
        warnings = []
        if before:
            for f in lk:
                v = data.get(f, before.get(f))
                if v and v == before.get(f) and v not in lk[f]:
                    lk[f].add(v)
                    warnings.append(f"{f.replace('_', ' ').capitalize()} '{v}' is inactive in Data Setup; "
                                    "kept from the existing trade.")
        return lk, warnings

    def preview_validation(self, data: dict, is_new: bool = True, trade_id: str | None = None) -> dict:
        before = self.repo.get_trade(trade_id) if trade_id else None
        if before:
            data = {**{k: before.get(k) for k in TRADE_EDITABLE_FIELDS}, **data}
        lk, extra = self._lookups(before, data)
        r = validate_trade(data, self.clock().date(), self._mark_for(data), is_new=is_new, lookups=lk)
        return {"ok": r.ok, "errors": r.errors, "warnings": extra + r.warnings}

    def create_trade(self, data: dict, user: str) -> tuple[dict, list]:
        lk, _ = self._lookups(None, data)
        r = validate_trade(data, self.clock().date(), self._mark_for(data), is_new=True, lookups=lk)
        if not r.ok:
            raise ServiceError("Trade failed validation.", status=422, errors=r.errors, warnings=r.warnings)
        now = utc_now_iso()
        trade = Trade(id=self.repo.next_trade_id(), status="PENDING_REVIEW", version=1,
                      created_by=user, created_at=now, updated_by=user, updated_at=now,
                      **{k: r.clean[k] for k in TRADE_EDITABLE_FIELDS},
                      quantity_unit=r.clean["quantity_unit"], currency=r.clean["currency"],
                      price_unit=r.clean["price_unit"]).to_dict()
        self.repo.save_trade(trade)
        self.repo.append_audit(make_event(user, "TRADE_CREATED", "trade", trade["id"],
                                          "Manual capture", before={}, after=trade))
        return trade, r.warnings

    def amend_trade(self, trade_id: str, data: dict, user: str, reason: str) -> tuple[dict, list]:
        before = self.repo.get_trade(trade_id)
        if not before:
            raise ServiceError(f"Trade {trade_id} not found.", status=404)
        if before["status"] == "CANCELLED":
            raise ServiceError("Cancelled trades cannot be amended.", status=409)
        reason = _require_reason(reason)
        merged = {k: before.get(k) for k in TRADE_EDITABLE_FIELDS}
        merged.update({k: v for k, v in data.items() if k in TRADE_EDITABLE_FIELDS})
        lk, kept = self._lookups(before, merged)
        r = validate_trade(merged, self.clock().date(), self._mark_for(merged), is_new=False, lookups=lk)
        r.warnings = kept + r.warnings
        if not r.ok:
            raise ServiceError("Amendment failed validation.", status=422, errors=r.errors, warnings=r.warnings)
        after = {**before, **{k: r.clean[k] for k in TRADE_EDITABLE_FIELDS},
                 "quantity_unit": r.clean["quantity_unit"], "currency": r.clean["currency"],
                 "price_unit": r.clean["price_unit"]}
        if all(after.get(k) == before.get(k) for k in TRADE_EDITABLE_FIELDS):
            raise ServiceError("No changes to save.", status=409)
        after.update(status="PENDING_REVIEW", version=before["version"] + 1,
                     updated_by=user, updated_at=utc_now_iso(), reviewed_by="", reviewed_at="")
        self.repo.save_trade(after)
        self.repo.append_audit(make_event(user, "TRADE_AMENDED", "trade", trade_id, reason,
                                          before=before, after=after))
        return after, r.warnings

    def cancel_trade(self, trade_id: str, user: str, reason: str) -> dict:
        before = self.repo.get_trade(trade_id)
        if not before:
            raise ServiceError(f"Trade {trade_id} not found.", status=404)
        if before["status"] == "CANCELLED":
            raise ServiceError("Trade is already cancelled.", status=409)
        reason = _require_reason(reason)
        after = {**before, "status": "CANCELLED", "updated_by": user, "updated_at": utc_now_iso()}
        self.repo.save_trade(after)
        self.repo.append_audit(make_event(user, "TRADE_CANCELLED", "trade", trade_id, reason,
                                          before=before, after=after))
        return after

    def review_trade(self, trade_id: str, user: str, decision: str, reason: str) -> dict:
        _require_role(user, "RISK", "Trade review")
        before = self.repo.get_trade(trade_id)
        if not before:
            raise ServiceError(f"Trade {trade_id} not found.", status=404)
        if before["status"] != "PENDING_REVIEW":
            raise ServiceError(f"Only trades pending review can be reviewed (status {before['status']}).",
                               status=409)
        if user == before.get("updated_by"):
            raise ServiceError("Maker-checker: you cannot review a trade you captured or amended.", status=403)
        decision = (decision or "").upper()
        if decision not in ("APPROVE", "REJECT"):
            raise ServiceError("Decision must be APPROVE or REJECT.", errors={"decision": "Invalid."})
        reason = _require_reason(reason)
        after = {**before, "status": "VALIDATED" if decision == "APPROVE" else "REJECTED",
                 "reviewed_by": user, "reviewed_at": utc_now_iso()}
        self.repo.save_trade(after)
        action = "TRADE_APPROVED" if decision == "APPROVE" else "TRADE_REJECTED"
        self.repo.append_audit(make_event(user, action, "trade", trade_id, reason,
                                          before=before, after=after))
        return after

    def act_on_exception(self, exc_id: str, user: str, action: str, reason: str) -> dict:
        action = (action or "").upper()
        if action not in ("ACKNOWLEDGE", "OVERRIDE"):
            raise ServiceError("Action must be ACKNOWLEDGE or OVERRIDE.", errors={"action": "Invalid."})
        s = self.snapshot()
        exc = next((e for e in s["exceptions"] if e["id"] == exc_id), None)
        if not exc:
            raise ServiceError("Exception not found or no longer active.", status=404)
        reason = _require_reason(reason)
        context = ""
        if action == "OVERRIDE":
            if not exc["overridable"]:
                raise ServiceError(f"'{exc['title']}' cannot be overridden; resolve the underlying data.",
                                   status=409)
            _require_role(user, "RISK", "Override")
            if exc["rule"] == "STALE_PRICE":
                context = s["prices"][exc["price_key"]]["as_of"]
        rec = ExceptionAction(exception_id=exc_id, action=action, actor=user, reason=reason,
                              timestamp=utc_now_iso(), context=context).to_dict()
        self.repo.add_exception_action(rec)
        self.repo.append_audit(make_event(
            user, f"EXCEPTION_{'OVERRIDDEN' if action == 'OVERRIDE' else 'ACKNOWLEDGED'}",
            "exception", exc_id, reason,
            before={"state": exc["state"]},
            after={"state": "OVERRIDDEN" if action == "OVERRIDE" else "ACKNOWLEDGED",
                   **({"accepted_mark_as_of": context} if context else {})}))
        return rec

    def update_limit(self, limit_id: str, user: str, data: dict, reason: str) -> dict:
        _require_role(user, "RISK", "Limit change")
        before = self.repo.get_limit(limit_id)
        if not before:
            raise ServiceError(f"Limit {limit_id} not found.", status=404)
        reason = _require_reason(reason)
        errors = {}
        after = dict(before)
        try:
            v = float(data.get("limit_value"))
            if v <= 0 or v > 1e12:
                errors["limit_value"] = "Limit must be greater than zero."
            after["limit_value"] = v
        except (TypeError, ValueError):
            errors["limit_value"] = "Limit must be a number."
        if data.get("warn_pct") not in (None, ""):
            try:
                w = float(data["warn_pct"])
                if not 0.1 <= w < 1.0:
                    errors["warn_pct"] = "Warning threshold must be between 0.10 and 0.99."
                after["warn_pct"] = w
            except (TypeError, ValueError):
                errors["warn_pct"] = "Warning threshold must be a number."
        if errors:
            raise ServiceError("Limit change failed validation.", status=422, errors=errors)
        if after["limit_value"] == before["limit_value"] and after["warn_pct"] == before["warn_pct"]:
            raise ServiceError("No changes to save.", status=409)
        after.update(updated_by=user, updated_at=utc_now_iso())
        self.repo.save_limit(after)
        self.repo.append_audit(make_event(user, "LIMIT_CHANGED", "limit", limit_id, reason,
                                          before=before, after=after))
        return after

    # ── Data Setup (reference data) ─────────────────────────────────────────
    def _usage(self, kind: str, code: str, trades: list, limits: list) -> dict:
        meta = refdata.KINDS[kind]
        if kind == "unit":
            commodities = {k for k, sp in ref.COMMODITIES.items() if sp["tonnes_key"] == code}
            linked = [t for t in trades if t["commodity"] in commodities]
        elif meta.get("trade_field"):
            linked = [t for t in trades if t.get(meta["trade_field"]) == code]
        else:
            linked = []
        lims = [l["id"] for l in limits if meta.get("limit_dimension") == l["dimension"] and l["key"] == code]
        if kind == "commodity":
            lims = [l["id"] for l in limits if l["dimension"] == "commodity" and l["key"] == code]
        return {"trades": len(linked), "open_trades": sum(1 for t in linked if t["status"] in ref.RISK_STATUSES),
                "trade_ids": [t["id"] for t in linked][:50], "limits": lims}

    def reference_data(self) -> dict:
        trades, limits = self.repo.list_trades(), self.repo.list_limits()
        records = self.repo.list_reference() + refdata.derived_records(self.stale_hours)
        by_kind = {k: [] for k in refdata.ORDER}
        for r in records:
            if r["kind"] not in by_kind:
                continue
            row = {**r, "usage": self._usage(r["kind"], r["code"], trades, limits)}
            if r["kind"] == "commodity":
                row["spec"] = {k: v for k, v in ref.COMMODITIES[r["code"]].items()
                               if k in ("exchange", "code", "lot_size", "qty_unit", "price_unit", "currency",
                                        "listed_months", "price_band_pct")}
            by_kind[r["kind"]].append(row)
        for rows in by_kind.values():
            rows.sort(key=lambda r: (r["status"] != refdata.ACTIVE, r["name"].lower()))
        kinds = [{"kind": k, **{f: v for f, v in refdata.KINDS[k].items()}} for k in refdata.ORDER]
        return {"kinds": kinds, "records": by_kind, "persistence": self.repo.describe()}

    def _get_reference(self, kind: str, code: str) -> dict:
        if kind not in refdata.KINDS:
            raise ServiceError("Unknown reference data type.", status=404)
        rec = next((r for r in self.repo.list_reference() if r["kind"] == kind and r["code"] == code), None)
        if not rec:
            raise ServiceError(f"{refdata.KINDS[kind]['singular'].capitalize()} '{code}' not found.", status=404)
        return rec

    def create_reference(self, kind: str, data: dict, user: str) -> dict:
        _require_role(user, "RISK", "Data Setup changes")
        taken = {r["code"] for r in self.repo.list_reference() if r["kind"] == kind}
        clean, errors = refdata.validate(kind, data, None, taken)
        if errors:
            raise ServiceError("Record failed validation.", status=422, errors=errors)
        rec = {"kind": kind, "code": clean["code"], "name": clean.get("name") or clean["code"],
               "status": clean["status"], "description": clean.get("description", ""), "attrs": clean["attrs"],
               "system": False, "updated_by": user, "updated_at": utc_now_iso()}
        self.repo.save_reference(rec)
        self.repo.append_audit(make_event(user, "REFERENCE_CREATED", "reference", f"{kind}:{rec['code']}",
                                          (data.get("reason") or "").strip() or "Created in Data Setup",
                                          before={}, after=rec))
        return rec

    def update_reference(self, kind: str, code: str, data: dict, user: str, reason: str | None) -> dict:
        _require_role(user, "RISK", "Data Setup changes")
        before = self._get_reference(kind, code)
        reason = _require_reason(reason)
        clean, errors = refdata.validate(kind, data, before, set())
        if errors:
            raise ServiceError("Record failed validation.", status=422, errors=errors)
        after = {**before, "name": clean.get("name", before["name"]),
                 "description": clean.get("description", before.get("description", "")),
                 "status": clean["status"], "attrs": clean["attrs"]}
        if all(after[k] == before[k] for k in ("name", "description", "status", "attrs")):
            raise ServiceError("No changes to save.", status=409)
        after.update(updated_by=user, updated_at=utc_now_iso())
        self.repo.save_reference(after)
        action = ("REFERENCE_DEACTIVATED" if after["status"] == refdata.INACTIVE else "REFERENCE_ACTIVATED") \
            if after["status"] != before["status"] else "REFERENCE_UPDATED"
        self.repo.append_audit(make_event(user, action, "reference", f"{kind}:{code}", reason,
                                          before=before, after=after))
        return after

    def delete_reference(self, kind: str, code: str, user: str, reason: str | None) -> None:
        _require_role(user, "RISK", "Data Setup changes")
        before = self._get_reference(kind, code)
        reason = _require_reason(reason)
        meta = refdata.KINDS[kind]
        if not meta["deletable"] or before.get("system"):
            raise ServiceError(f"{meta['label']} cannot be deleted; deactivate instead.", status=409)
        u = self._usage(kind, code, self.repo.list_trades(), self.repo.list_limits())
        if u["trades"] or u["limits"]:
            parts = [f"{u['trades']} trade(s)"] if u["trades"] else []
            parts += [f"{len(u['limits'])} limit(s)"] if u["limits"] else []
            raise ServiceError(f"'{before['name']}' is used by {' and '.join(parts)} and cannot be deleted. "
                               "Deactivate it instead: existing trades keep it, new trades cannot use it.",
                               status=409, errors={"dependencies": ", ".join(u["trade_ids"][:10] + u["limits"])})
        self.repo.save_reference({**before, "deleted": True, "updated_by": user, "updated_at": utc_now_iso()})
        self.repo.append_audit(make_event(user, "REFERENCE_DELETED", "reference", f"{kind}:{code}", reason,
                                          before=before, after={}))

    def log_ai_request(self, user: str, topic: str, entity_id: str, provider: str | None,
                       fields_sent: list) -> None:
        """AI usage is audited too — as an assistive event with no state change."""
        sent = ", ".join(fields_sent) or "none"
        outcome = f"answered by {provider}" if provider else "no provider available"
        self.repo.append_audit(make_event(
            user, "AI_EXPLANATION_REQUESTED", topic, entity_id,
            f"Assistive explanation (no state change); {outcome}. Fields sent: {sent}"))

    def reset_demo(self, user: str) -> None:
        if not isinstance(self.repo, InMemoryRepository):
            raise ServiceError("Reset is only available for the in-memory demo store.", status=409)
        self.repo.reset(self.clock())
        self.repo.append_audit(make_event(user, "DEMO_DATA_RESET", "system", "demo",
                                          "Synthetic demo data reloaded"))


__all__ = ["AgriService", "ServiceError", "ROLES", "RULES"]
