"""ocr_simulator.py
Financial document parser for the Treasury & Commodity Counterparty platform.

Parse order: XLSX → CSV/TSV → plain-text heuristic.
Column matching is case-insensitive and handles common naming variations.
"""
import csv
import io
import re

try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False


# Canonical column names expected in BigQuery
EXPECTED_COLUMNS = {
    "company_name", "country", "sector", "credit_rating", "period_year",
    "revenue_usd_m", "ebitda_usd_m", "net_income_usd_m",
    "total_assets_usd_m", "total_debt_usd_m", "current_ratio",
}

# Aliases: any of these header strings map to the canonical key
_ALIASES = {
    "company_name":      ["company", "name", "company name", "counterparty", "entity"],
    "country":           ["country", "nation", "jurisdiction", "country_sector", "country_and_sector"],
    "sector":            ["sector", "industry", "segment"],
    "credit_rating":     ["credit_rating", "rating", "credit rating", "s&p", "moody's"],
    "period_year":       ["period_year", "year", "fy", "fiscal year", "period"],
    "revenue_usd_m":     ["revenue_usd_m", "revenue", "revenues", "turnover", "total revenue",
                          "net revenue", "sales", "total sales"],
    "ebitda_usd_m":      ["ebitda_usd_m", "ebitda"],
    "net_income_usd_m":  ["net_income_usd_m", "net income", "net profit", "profit after tax",
                          "pat", "net earnings"],
    "total_assets_usd_m":["total_assets_usd_m", "total assets", "assets"],
    "total_debt_usd_m":  ["total_debt_usd_m", "total debt", "debt", "net debt", "financial debt",
                          "borrowings"],
    "current_ratio":     ["current_ratio", "current ratio", "liquidity ratio"],
}

# Build reverse lookup: raw string → canonical name
_ALIAS_MAP: dict[str, str] = {}
for _canon, _variants in _ALIASES.items():
    for _v in _variants:
        _ALIAS_MAP[_v.lower().replace(" ", "_")] = _canon
        _ALIAS_MAP[_v.lower()] = _canon


def simulate_ocr(content: bytes, filename: str = "") -> list[dict]:
    """Parse a financial document into a list of counterparty records."""
    if filename.lower().endswith(".xlsx"):
        records = _try_xlsx(content)
        if records:
            return records

    text = content.decode(errors="ignore").strip()
    records = _try_csv(text)
    if records:
        return records
    return _heuristic_extract(text)


# ── XLSX ─────────────────────────────────────────────────────────────────────

def _try_xlsx(content: bytes) -> list[dict]:
    if not _PANDAS:
        return []
    try:
        xl = pd.ExcelFile(io.BytesIO(content))
        for sheet in xl.sheet_names:
            for header_row in (0, 1, 2):
                df = pd.read_excel(xl, sheet_name=sheet, header=header_row)
                df = df.dropna(how="all")
                # Normalise column names
                df.columns = [_normalise_key(str(c)) for c in df.columns]
                # Map aliases → canonical names
                df = df.rename(columns=lambda c: _ALIAS_MAP.get(c, c))
                overlap = set(df.columns) & EXPECTED_COLUMNS
                if len(overlap) < 3:
                    continue
                records = []
                for _, row in df.iterrows():
                    d = _normalise_row(row.to_dict())
                    if d["company_name"] not in ("Unknown", "", "nan"):
                        records.append(d)
                if records:
                    return records
    except Exception:
        pass
    return []


# ── CSV / TSV ─────────────────────────────────────────────────────────────────

def _try_csv(text: str) -> list[dict]:
    # Auto-detect delimiter
    first_line = text.split("\n")[0]
    if "\t" in first_line:
        dialect = "excel-tab"
    elif ";" in first_line:
        dialect = None  # use custom delimiter below
    else:
        dialect = "excel"

    try:
        delimiter = ";" if dialect is None else None
        if delimiter:
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        else:
            reader = csv.DictReader(io.StringIO(text), dialect=dialect)

        rows = list(reader)
        if not rows:
            return []

        # Normalise and alias-map all keys
        normalised = [_remap_row(r) for r in rows]

        # Check enough expected columns are present
        sample_keys = set(normalised[0].keys()) if normalised else set()
        if len(sample_keys & EXPECTED_COLUMNS) < 3:
            return []

        records = []
        for row in normalised:
            d = _normalise_row(row)
            if d["company_name"] not in ("Unknown", "", "nan"):
                records.append(d)
        return records
    except Exception:
        return []


def _remap_row(row: dict) -> dict:
    """Lowercase + strip keys, then map aliases to canonical names."""
    out = {}
    for k, v in row.items():
        normalised_key = _normalise_key(k)
        canonical = _ALIAS_MAP.get(normalised_key, normalised_key)
        out[canonical] = v
    return out


def _normalise_key(s: str) -> str:
    """Lowercases, removes punctuation, and replaces spaces with underscores."""
    s = s.strip().lower()
    for char in ",/\\()":
        s = s.replace(char, " ")
    return s.replace(" ", "_").replace("-", "_").replace("__", "_").strip("_")


# ── Row normalisation ─────────────────────────────────────────────────────────

def _normalise_row(row: dict) -> dict:
    """Coerce a mapped row dict into the BigQuery schema types."""
    def flt(key, default=0.0):
        val = row.get(key, default)
        if val is None or str(val).strip() in ("", "nan", "N/A", "-"):
            return default
        try:
            return float(str(val).replace(",", "").replace("$", "").strip())
        except (ValueError, TypeError):
            return default

    def intv(key, default=0):
        val = row.get(key, default)
        try:
            return int(float(str(val).strip()))
        except (ValueError, TypeError):
            return default

    def strv(key, default=""):
        val = row.get(key, default)
        s = str(val).strip() if val is not None else default
        return "" if s.lower() in ("nan", "none", "") else s

    revenue    = flt("revenue_usd_m")
    ebitda     = flt("ebitda_usd_m")
    total_debt = flt("total_debt_usd_m")
    assets     = flt("total_assets_usd_m")
    equity     = assets - total_debt

    return {
        "company_name":       strv("company_name", "Unknown") or "Unknown",
        "country":            strv("country"),
        "sector":             strv("sector"),
        "credit_rating":      strv("credit_rating", "N/A") or "N/A",
        "period_year":        intv("period_year", 2024),
        "revenue_usd_m":      revenue,
        "ebitda_usd_m":       ebitda,
        "ebitda_margin_pct":  round((ebitda / revenue * 100) if revenue else 0.0, 2),
        "net_income_usd_m":   flt("net_income_usd_m"),
        "total_assets_usd_m": assets,
        "total_debt_usd_m":   total_debt,
        "debt_to_equity":     round(total_debt / equity if equity > 0 else 0.0, 2),
        "current_ratio":      flt("current_ratio", 1.0),
    }


# ── Plain-text heuristic fallback ─────────────────────────────────────────────

def _heuristic_extract(text: str) -> list[dict]:
    def find(pattern, cast=float, default=0.0):
        m = re.search(pattern, text, re.IGNORECASE)
        try:
            return cast(m.group(1).replace(",", "")) if m else default
        except Exception:
            return default

    revenue = find(r"revenue[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    ebitda  = find(r"ebitda[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    assets  = find(r"total[\s_]assets[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    debt    = find(r"total[\s_]debt[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    ni      = find(r"net[\s_]income[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    cr      = find(r"current[\s_]ratio[:\s]+([\d.]+)", default=1.0)
    equity  = assets - debt

    name_m = re.search(r"(?:company|counterparty|entity)[:\s]+([A-Za-z0-9 &.,]+)", text, re.IGNORECASE)
    company_name = name_m.group(1).strip() if name_m else "Unknown"

    return [{
        "company_name":       company_name,
        "country":            "",
        "sector":             "",
        "credit_rating":      "N/A",
        "period_year":        2024,
        "revenue_usd_m":      revenue,
        "ebitda_usd_m":       ebitda,
        "ebitda_margin_pct":  round((ebitda / revenue * 100) if revenue else 0.0, 2),
        "net_income_usd_m":   ni,
        "total_assets_usd_m": assets,
        "total_debt_usd_m":   debt,
        "debt_to_equity":     round(debt / equity if equity > 0 else 0.0, 2),
        "current_ratio":      cr,
    }]
