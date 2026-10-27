"""agri/validation.py
Deterministic trade validation. Incomplete or inconsistent trades are
rejected with field-level errors; soft issues are returned as warnings
that the user must see but that do not block capture.
"""
import re
from dataclasses import dataclass, field
from datetime import date

from agri import reference as ref
from agri import refdata

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_TEXT = 120
MAX_NOTES = 1000
MAX_QUANTITY = 1_000_000_000


@dataclass
class ValidationResult:
    clean: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _number(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise ValueError
    return float(v)


def _date(v) -> date:
    return date.fromisoformat(_text(v))


def _month_index(ym: str) -> int:
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def validate_trade(data: dict, today: date, reference_price: float | None = None,
                   is_new: bool = True, lookups: dict | None = None) -> ValidationResult:
    """Validate and normalise raw trade input.

    Args:
        data: raw field values (strings or numbers) from the capture form.
        today: business date used for date checks (passed in for testability).
        reference_price: current synthetic mark for the contract, if any; used
            only for the price-band warning.
        is_new: new trades may not mature in the past.
        lookups: allowed (active) Data Setup codes per trade field; defaults to
            the synthetic seed records.
    """
    allowed = lookups or refdata.default_lookups()
    r = ValidationResult()
    e = r.errors
    c = r.clean

    for f in ("counterparty", "portfolio", "broker", "execution_ref",
              "reference_market", "strategy", "delivery_location"):
        val = _text(data.get(f))
        if len(val) > MAX_TEXT:
            e[f] = f"Must be {MAX_TEXT} characters or fewer."
        c[f] = val
    notes = _text(data.get("notes"))
    if len(notes) > MAX_NOTES:
        e["notes"] = f"Must be {MAX_NOTES} characters or fewer."
    c["notes"] = notes

    # Static data lists
    if not c["counterparty"]:
        e["counterparty"] = "Counterparty is required."
    elif c["counterparty"] not in allowed["counterparty"]:
        e["counterparty"] = "Counterparty is not an active counterparty in Data Setup."
    if not c["portfolio"]:
        e["portfolio"] = "Portfolio / business unit is required."
    elif c["portfolio"] not in allowed["portfolio"]:
        e["portfolio"] = "Portfolio is not an active portfolio in Data Setup."
    if c["broker"] and c["broker"] not in allowed["broker"]:
        e["broker"] = "Broker is not an active broker in Data Setup."
    if c["strategy"] and c["strategy"] not in allowed["strategy"]:
        e["strategy"] = "Strategy is not an active strategy in Data Setup."

    def choice(f, options, label, upper=True):
        val = _text(data.get(f))
        val = val.upper() if upper else val
        if not val:
            e[f] = f"{label} is required."
        elif val not in options:
            e[f] = f"{label} must be one of: {', '.join(options)}."
        c[f] = val
        return val

    commodity = choice("commodity", list(ref.COMMODITIES), "Commodity")
    if commodity in ref.COMMODITIES and commodity not in allowed["commodity"]:
        e["commodity"] = "Commodity is inactive in Data Setup."
    instrument = choice("instrument_type", ref.INSTRUMENT_TYPES, "Instrument type")
    if c["delivery_location"]:
        if instrument and instrument != "FORWARD":
            e["delivery_location"] = "Delivery location applies to forwards only."
        elif c["delivery_location"] not in allowed["delivery_location"]:
            e["delivery_location"] = "Location is not an active delivery location in Data Setup."
    choice("direction", ref.DIRECTIONS, "Direction")
    pricing = _text(data.get("pricing_type")).upper() or "FIXED"
    if pricing not in ref.PRICING_TYPES:
        e["pricing_type"] = f"Pricing type must be one of: {', '.join(ref.PRICING_TYPES)}."
    c["pricing_type"] = pricing
    source = _text(data.get("source")).upper() or "MANUAL"
    if source not in ref.SOURCES:
        e["source"] = f"Source must be one of: {', '.join(ref.SOURCES)}."
    c["source"] = source

    spec = ref.COMMODITIES.get(commodity)
    exchange_traded = instrument in ref.EXCHANGE_TRADED

    # Units and currency are derived from the contract — never free-typed.
    if spec:
        c["quantity_unit"] = spec["qty_unit"]
        c["currency"] = spec["currency"]
        c["price_unit"] = spec["price_unit"]
        for f, expected in (("currency", spec["currency"]),
                            ("price_unit", spec["price_unit"]),
                            ("quantity_unit", spec["qty_unit"])):
            given = _text(data.get(f))
            if given and given != expected:
                e[f] = f"Inconsistent with {spec['name']} contract ({expected})."

    # Quantity
    try:
        qty = _number(data.get("quantity"))
        if qty is None:
            e["quantity"] = "Quantity is required."
        elif qty <= 0:
            e["quantity"] = "Quantity must be greater than zero (use Direction for sells)."
        elif qty > MAX_QUANTITY:
            e["quantity"] = "Quantity exceeds the prototype sanity limit."
        elif spec and exchange_traded and qty % spec["lot_size"] != 0:
            e["quantity"] = (f"Exchange-traded quantity must be a multiple of the lot size "
                             f"({spec['lot_size']:,} {spec['qty_unit']}).")
        c["quantity"] = qty
    except (TypeError, ValueError):
        e["quantity"] = "Quantity must be a number."

    # Price / premium
    price_label = "Premium" if instrument == "OPTION" else "Price"
    try:
        price = _number(data.get("price"))
        if price is None:
            e["price"] = f"{price_label} is required."
        elif price < 0 or (price == 0 and instrument != "OPTION"):
            e["price"] = f"{price_label} must be greater than zero."
        c["price"] = price
    except (TypeError, ValueError):
        e["price"] = f"{price_label} must be a number."

    # Option fields
    opt = _text(data.get("option_type")).upper()
    strike_raw = data.get("strike")
    if instrument == "OPTION":
        if opt not in ref.OPTION_TYPES:
            e["option_type"] = "Option type (CALL or PUT) is required for options."
        try:
            strike = _number(strike_raw)
            if strike is None or strike <= 0:
                e["strike"] = "Strike must be greater than zero for options."
            c["strike"] = strike
        except (TypeError, ValueError):
            e["strike"] = "Strike must be a number."
        c["option_type"] = opt or None
    else:
        if opt:
            e["option_type"] = "Option type only applies to options."
        if _text(strike_raw):
            e["strike"] = "Strike only applies to options."
        c["option_type"] = None
        c["strike"] = None

    if pricing == "INDEX_AVERAGE" and instrument != "SWAP":
        e["pricing_type"] = "Index-average pricing is only supported for swaps."

    # Contract month
    cm = _text(data.get("contract_month"))
    c["contract_month"] = cm
    if not cm:
        e["contract_month"] = "Contract / delivery month is required."
    elif not _MONTH_RE.match(cm):
        e["contract_month"] = "Use the format YYYY-MM."
    elif spec and int(cm[5:]) not in spec["listed_months"]:
        e["contract_month"] = (f"{spec['name']} lists months "
                               f"{', '.join(str(m) for m in spec['listed_months'])} only.")

    # Dates
    dates = {}
    for f, label in (("trade_date", "Trade date"), ("effective_date", "Effective date"),
                     ("maturity_date", "Maturity / delivery date")):
        raw = _text(data.get(f))
        if not raw and f == "effective_date" and "trade_date" in dates:
            raw = dates["trade_date"].isoformat()  # sensible default
        if not raw:
            e[f] = f"{label} is required."
            continue
        try:
            dates[f] = _date(raw)
            c[f] = dates[f].isoformat()
        except ValueError:
            e[f] = f"{label} must be a valid date (YYYY-MM-DD)."
    td, ed, md = (dates.get(k) for k in ("trade_date", "effective_date", "maturity_date"))
    if td and td > today:
        e["trade_date"] = "Trade date cannot be in the future."
    if td and ed and ed < td:
        e["effective_date"] = "Effective date cannot be before the trade date."
    if ed and md and md < ed:
        e["maturity_date"] = "Maturity cannot be before the effective date."
    if is_new and md and md < today and "maturity_date" not in e:
        e["maturity_date"] = "New trades cannot have a maturity in the past."
    if md and exchange_traded and "contract_month" not in e and cm:
        gap = _month_index(cm) - _month_index(md.strftime("%Y-%m"))
        if gap not in (0, 1):
            e["maturity_date"] = ("For exchange-traded contracts the last trading / expiry date "
                                  "must fall in the contract month or the month before.")

    # Execution details
    if exchange_traded:
        if not c["broker"]:
            e["broker"] = "Broker is required for exchange-traded trades."
        if not c["execution_ref"]:
            e["execution_ref"] = "Execution reference is required for exchange-traded trades."
    if spec and not c["reference_market"]:
        c["reference_market"] = f"{spec['exchange']} {spec['code']} {cm}".strip()

    if not c["strategy"]:
        r.warnings.append("No strategy assigned; the trade will show as 'Unassigned' in exposure by strategy.")
    if instrument == "FORWARD" and not c["delivery_location"]:
        r.warnings.append("No delivery location set for this forward.")

    # Soft check: price vs current synthetic mark
    if (spec and instrument != "OPTION" and reference_price and
            isinstance(c.get("price"), float) and "price" not in e):
        dev = abs(c["price"] - reference_price) / reference_price
        if dev > spec["price_band_pct"]:
            r.warnings.append(
                f"Price deviates {dev:.0%} from the current demo mark "
                f"({reference_price:g} {spec['price_unit']}); tolerance is "
                f"{spec['price_band_pct']:.0%}. A valuation exception will be raised.")
    return r
