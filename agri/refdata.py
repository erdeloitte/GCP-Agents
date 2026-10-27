"""agri/refdata.py
Reference data managed in Data Setup: definitions, synthetic seed records,
validation and dependency rules.

A record is {kind, code, name, status, description, attrs, system, updated_by,
updated_at}. `code` is the value stored on trades (for counterparties,
portfolios and brokers that is the display name, as before).

What is editable is deliberate:
  - strategies, locations, counterparties, portfolios, brokers: create, edit,
    activate / deactivate, delete only when nothing references them;
  - commodities: category, description and status only — contract
    specifications (lot size, units, listed months) drive deterministic
    validation and valuation and stay code-managed in agri/reference.py;
  - units of measure and price sources: read-only views of the conversion
    table and the synthetic feed.
All seed records are synthetic demo data.
"""
import re

from agri import reference as ref

ACTIVE, INACTIVE = "ACTIVE", "INACTIVE"
STATUSES = [ACTIVE, INACTIVE]
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_\-]{1,29}$")

# field: name, label, type (text | textarea | select), required, options, max, immutable
KINDS: dict[str, dict] = {
    "commodity": {
        "label": "Commodities", "singular": "commodity",
        "about": "Tradable commodities and their contract specifications. Specifications drive validation and "
                 "valuation, so they are code-managed; category, description and status can be changed here.",
        "creatable": False, "editable": True, "deletable": False, "trade_field": "commodity",
        "fields": [
            {"name": "category", "label": "Category", "type": "select", "required": True,
             "options": ["Grains", "Oilseeds", "Softs", "Other"]},
            {"name": "description", "label": "Description", "type": "textarea", "max": 300},
        ],
    },
    "strategy": {
        "label": "Strategies", "singular": "strategy",
        "about": "Trading or hedging strategies that trades can be tagged with. Used for exposure by strategy.",
        "creatable": True, "editable": True, "deletable": True, "trade_field": "strategy", "code_input": True,
        "fields": [
            {"name": "name", "label": "Name", "type": "text", "required": True, "max": 60},
            {"name": "description", "label": "Description", "type": "textarea", "required": True, "max": 300},
        ],
    },
    "unit": {
        "label": "Units of measure", "singular": "unit",
        "about": "Quantity units and their conversion to metric tonnes. Conversion factors drive the deterministic "
                 "exposure figures, so they are read-only in the prototype.",
        "creatable": False, "editable": False, "deletable": False,
    },
    "location": {
        "label": "Delivery locations", "singular": "location",
        "about": "Delivery points for physical forwards. Only forwards carry a delivery location.",
        "creatable": True, "editable": True, "deletable": True, "trade_field": "delivery_location",
        "code_input": True,
        "fields": [
            {"name": "name", "label": "Name", "type": "text", "required": True, "max": 80},
            {"name": "location_type", "label": "Type", "type": "select", "required": True,
             "options": ["Port", "Silo", "Warehouse", "Inland terminal"]},
            {"name": "region", "label": "Region", "type": "text", "required": True, "max": 60},
            {"name": "description", "label": "Description", "type": "textarea", "max": 300},
        ],
    },
    "counterparty": {
        "label": "Counterparties", "singular": "counterparty",
        "about": "Trading counterparties and clearing members. The name is stored on trades and cannot be changed "
                 "once created.",
        "creatable": True, "editable": True, "deletable": True, "trade_field": "counterparty",
        "limit_dimension": "counterparty", "name_is_code": True,
        "fields": [
            {"name": "name", "label": "Name", "type": "text", "required": True, "max": 120, "immutable": True},
            {"name": "counterparty_type", "label": "Type", "type": "select", "required": True,
             "options": ["Clearing member", "Commercial", "Financial"]},
            {"name": "description", "label": "Description", "type": "textarea", "max": 300},
        ],
    },
    "portfolio": {
        "label": "Portfolios", "singular": "portfolio",
        "about": "Portfolios / business units that own trades and limits. The name is stored on trades and cannot "
                 "be changed once created.",
        "creatable": True, "editable": True, "deletable": True, "trade_field": "portfolio",
        "limit_dimension": "portfolio", "name_is_code": True,
        "fields": [
            {"name": "name", "label": "Name", "type": "text", "required": True, "max": 120, "immutable": True},
            {"name": "description", "label": "Description", "type": "textarea", "max": 300},
        ],
    },
    "broker": {
        "label": "Brokers", "singular": "broker",
        "about": "Executing brokers. Exchange-traded trades require a broker.",
        "creatable": True, "editable": True, "deletable": True, "trade_field": "broker", "name_is_code": True,
        "fields": [
            {"name": "name", "label": "Name", "type": "text", "required": True, "max": 120, "immutable": True},
            {"name": "broker_type", "label": "Type", "type": "select", "required": True,
             "options": ["Exchange broker", "OTC / direct"]},
            {"name": "description", "label": "Description", "type": "textarea", "max": 300},
        ],
    },
    "price_source": {
        "label": "Price sources", "singular": "price source",
        "about": "Market-data sources used for valuation. Only the synthetic demo feed exists; no market-data "
                 "provider is connected.",
        "creatable": False, "editable": False, "deletable": False,
    },
}
ORDER = ["commodity", "strategy", "unit", "location", "counterparty", "portfolio", "broker", "price_source"]

UNIT_NAMES = {"t": "Metric tonne", "bu_corn": "Bushel — corn (56 lb)", "bu_wheat": "Bushel — wheat (60 lb)",
              "bu_soy": "Bushel — soybeans (60 lb)", "lb": "Pound"}
COMMODITY_CATEGORY = {"CBOT_CORN": "Grains", "CBOT_WHEAT": "Grains", "EURONEXT_MILLING_WHEAT": "Grains",
                      "CBOT_SOYBEANS": "Oilseeds", "ICE_SUGAR11": "Softs", "ICE_COFFEE_C": "Softs",
                      "ICE_LDN_COCOA": "Softs"}

# Synthetic seed records (code, name, description, status, attrs)
_STRATEGIES = [
    ("ORIG_HEDGE", "Origination hedge", "Hedges price risk on physical grain origination (demo).", ACTIVE),
    ("CRUSH_HEDGE", "Crush margin hedge", "Locks in soybean crush margins (demo).", ACTIVE),
    ("SOFTS_DIR", "Softs directional", "Discretionary softs positions within limits (demo).", ACTIVE),
    ("TREASURY_OVL", "Treasury overlay", "Portfolio-level overlay managed by Treasury (demo).", ACTIVE),
    ("CAL_SPREAD", "Calendar spread", "Retired example strategy kept for history (demo).", INACTIVE),
]
PORTFOLIO_STRATEGY = {"EU Grains Origination": "ORIG_HEDGE", "Oilseeds Hedging": "CRUSH_HEDGE",
                      "Softs Trading": "SOFTS_DIR", "Treasury Overlay": "TREASURY_OVL"}
_LOCATIONS = [
    ("DEMO_PORT_NS", "Demo Port — North Sea", "Port", "Northern Europe (demo)"),
    ("DEMO_SILO_EU", "Demo Silo — Inland EU", "Silo", "Central Europe (demo)"),
    ("DEMO_PORT_SA", "Demo Port — South Atlantic", "Port", "South America (demo)"),
    ("DEMO_WH_WA", "Demo Warehouse — West Africa", "Warehouse", "West Africa (demo)"),
]
_PORTFOLIO_DESC = {"EU Grains Origination": "Grain origination book (demo).",
                   "Softs Trading": "Sugar, coffee and cocoa trading book (demo).",
                   "Oilseeds Hedging": "Soybean hedging book (demo).",
                   "Treasury Overlay": "Treasury-managed overlay positions (demo)."}


def _rec(kind, code, name, description="", status=ACTIVE, attrs=None, system=False) -> dict:
    return {"kind": kind, "code": code, "name": name, "status": status, "description": description,
            "attrs": attrs or {}, "system": system, "updated_by": "demo.seed (synthetic data loader)",
            "updated_at": ""}


def default_records(seeded_at: str = "") -> list[dict]:
    out = []
    for k, spec in ref.COMMODITIES.items():
        out.append(_rec("commodity", k, spec["name"], f"{spec['exchange']} {spec['code']} (simplified demo spec).",
                        attrs={"category": COMMODITY_CATEGORY.get(k, "Other")}, system=True))
    out += [_rec("strategy", c, n, d, s) for c, n, d, s in _STRATEGIES]
    out += [_rec("location", c, n, attrs={"location_type": t, "region": r}) for c, n, t, r in _LOCATIONS]
    for cp in ref.COUNTERPARTIES:
        out.append(_rec("counterparty", cp, cp, "Synthetic counterparty (demo).",
                        attrs={"counterparty_type": "Clearing member" if "Clearing" in cp else "Commercial"}))
    out += [_rec("portfolio", p, p, _PORTFOLIO_DESC.get(p, "")) for p in ref.PORTFOLIOS]
    for b in ref.BROKERS:
        out.append(_rec("broker", b, b, "Synthetic broker (demo).",
                        attrs={"broker_type": "OTC / direct" if "OTC" in b else "Exchange broker"}))
    for r in out:
        r["updated_at"] = seeded_at
    return out


def derived_records(stale_hours: float) -> list[dict]:
    """Read-only kinds computed from code (not stored)."""
    out = []
    for key, factor in ref.TONNES_PER_UNIT.items():
        users = [k for k, s in ref.COMMODITIES.items() if s["tonnes_key"] == key]
        out.append(_rec("unit", key, UNIT_NAMES.get(key, key), status=ACTIVE, system=True,
                        attrs={"tonnes_per_unit": factor, "commodities": users}))
    out.append(_rec("price_source", "SYNTHETIC_DEMO_FEED", "Synthetic demo feed",
                    "Generated in agri/demo_data.py; one snapshot per day. Not a market-data provider.",
                    system=True, attrs={"commodities": list(ref.COMMODITIES), "frequency": "Daily snapshot",
                                        "stale_after_hours": stale_hours}))
    return out


def lookups(records: list[dict]) -> dict:
    """Active codes per trade field, for trade validation."""
    out = {KINDS[k]["trade_field"]: set() for k in KINDS if KINDS[k].get("trade_field")}
    for r in records:
        f = KINDS.get(r["kind"], {}).get("trade_field")
        if f and r["status"] == ACTIVE:
            out[f].add(r["code"])
    return out


def default_lookups() -> dict:
    return lookups(default_records())


def validate(kind: str, data: dict, existing: dict | None, taken_codes: set) -> tuple[dict, dict]:
    """Validate a create (existing=None) or edit. Returns (clean, errors)."""
    meta = KINDS.get(kind)
    errors: dict = {}
    if not meta:
        return {}, {"kind": "Unknown reference data type."}
    if existing is None and not meta["creatable"]:
        return {}, {"kind": f"New {meta['label'].lower()} cannot be created in the prototype."}
    if existing is not None and not meta["editable"]:
        return {}, {"kind": f"{meta['label']} are read-only."}

    clean = {"attrs": dict(existing["attrs"]) if existing else {}}
    for f in meta["fields"]:
        name = f["name"]
        if existing is not None and f.get("immutable"):
            continue
        raw = data.get(name)
        val = "" if raw is None else str(raw).strip()
        if f.get("required") and not val:
            errors[name] = f"{f['label']} is required."
        elif f.get("max") and len(val) > f["max"]:
            errors[name] = f"Must be {f['max']} characters or fewer."
        elif f["type"] == "select" and val and val not in f["options"]:
            errors[name] = f"{f['label']} must be one of: {', '.join(f['options'])}."
        if name in ("name", "description"):
            clean[name] = val
        else:
            clean["attrs"][name] = val

    status = str(data.get("status") or (existing or {}).get("status") or ACTIVE).upper()
    if status not in STATUSES:
        errors["status"] = "Status must be Active or Inactive."
    clean["status"] = status

    if existing is None:
        if meta.get("name_is_code"):
            code = clean.get("name", "")
        else:
            code = str(data.get("code") or "").strip().upper()
            if not code:
                errors["code"] = "Code is required."
            elif not _CODE_RE.match(code):
                errors["code"] = "Use 2–30 capital letters, digits, '_' or '-', starting with a letter or digit."
        if code and code in taken_codes:
            errors["code" if not meta.get("name_is_code") else "name"] = \
                f"A {meta['singular']} with this {'name' if meta.get('name_is_code') else 'code'} already exists."
        clean["code"] = code
    else:
        clean["code"] = existing["code"]
        if meta.get("name_is_code") or kind == "commodity":
            clean["name"] = existing["name"]
    return clean, errors
