"""agri/audit.py
Append-only audit events with before/after snapshots and a field-level diff.
"""
import uuid

from agri.models import AuditEvent, utc_now_iso

# Fields that change on every save and would only add noise to a diff
_NOISE = {"updated_at", "version"}


def diff(before: dict | None, after: dict | None) -> dict:
    """Field-level changes {field: {"from": x, "to": y}}."""
    before, after = before or {}, after or {}
    out = {}
    for k in sorted(set(before) | set(after)):
        if k in _NOISE:
            continue
        if before.get(k) != after.get(k):
            out[k] = {"from": before.get(k), "to": after.get(k)}
    return out


def make_event(actor: str, action: str, entity_type: str, entity_id: str,
               reason: str = "", before: dict | None = None, after: dict | None = None,
               timestamp: str | None = None) -> dict:
    return AuditEvent(
        id=str(uuid.uuid4()),
        timestamp=timestamp or utc_now_iso(),
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason,
        before=before,
        after=after,
        changes=diff(before, after) if (before is not None and after is not None) else {},
    ).to_dict()
