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
from agri import risk
from agri.audit import make_event
from agri.models import ExceptionAction, TRADE_EDITABLE_FIELDS, Trade, utc_now_iso
from agri.monitoring import RULES, SEVERITY_ORDER, detect_exceptions, price_key
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
        if portfolio and portfolio not in ref.PORTFOLIOS:
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
        return {
            "valued_at": now.replace(microsecond=0).isoformat(),
            "scope": {"portfolio": portfolio or None, "limits": "firm-wide"},
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
            "freshness": {
                "marks_in_use": len(used_pks),
                "missing_marks": len(used_pks) - len(used_prices),
                "stale_marks": sum(1 for p in used_prices
                                   if (now - datetime.fromisoformat(p["as_of"])).total_seconds()
                                   > self.stale_hours * 3600),
                "oldest_mark": as_ofs[0] if as_ofs else None,
                "newest_mark": as_ofs[-1] if as_ofs else None,
                "stale_threshold_hours": self.stale_hours,
                "source": "SYNTHETIC DEMO DATA",
            },
        }

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
            if any(str(t.get(k, "")).upper() != str(v).upper() for k, v in f.items()
                   if k in ("commodity", "status", "portfolio", "instrument_type", "direction", "counterparty")):
                continue
            if q and q not in " ".join(str(t.get(k, "")) for k in
                                       ("id", "counterparty", "portfolio", "commodity", "execution_ref",
                                        "reference_market", "notes", "broker")).lower():
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

    def preview_validation(self, data: dict, is_new: bool = True) -> dict:
        r = validate_trade(data, self.clock().date(), self._mark_for(data), is_new=is_new)
        return {"ok": r.ok, "errors": r.errors, "warnings": r.warnings}

    def create_trade(self, data: dict, user: str) -> tuple[dict, list]:
        r = validate_trade(data, self.clock().date(), self._mark_for(data), is_new=True)
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
        r = validate_trade(merged, self.clock().date(), self._mark_for(merged), is_new=False)
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
