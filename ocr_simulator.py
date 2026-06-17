"""ocr_simulator.py
Financial document parser – simulates extraction of structured financial data.

In production, replace with Google Cloud Document AI or Vision API.
Accepts CSV/TSV uploads with counterparty financials, or falls back to
plain-text heuristic extraction.
"""
import csv
import io
import re

try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False


EXPECTED_COLUMNS = {
    "company_name", "country", "sector", "credit_rating", "period_year",
    "revenue_usd_m", "ebitda_usd_m", "net_income_usd_m",
    "total_assets_usd_m", "total_debt_usd_m", "current_ratio",
}


def simulate_ocr(content: bytes, filename: str = "") -> list[dict]:
    """Parse a financial document into a list of counterparty records.

    Tries XLSX first (if filename ends in .xlsx), then CSV, then plain-text
    heuristic extraction.

    Args:
        content:  Raw bytes downloaded from Cloud Storage.
        filename: Original filename — used to choose the parser.

    Returns:
        List of dicts matching the counterparties BigQuery schema.
    """
    if filename.lower().endswith(".xlsx"):
        records = _try_xlsx(content)
        if records:
            return records

    text = content.decode(errors="ignore").strip()
    records = _try_csv(text)
    if records:
        return records
    return _heuristic_extract(text)


def _try_xlsx(content: bytes) -> list[dict]:
    """Parse an XLSX workbook into counterparty records using pandas.

    Iterates all sheets; uses the first sheet whose columns overlap with
    EXPECTED_COLUMNS by at least 4 fields.  Rows with no company_name are skipped.
    """
    if not _PANDAS:
        return []
    try:
        xl = pd.ExcelFile(io.BytesIO(content))
        for sheet in xl.sheet_names:
            # Try reading with header on row 0; if columns don't match, probe row 1
            for header_row in (0, 1):
                df = pd.read_excel(xl, sheet_name=sheet, header=header_row)
                df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
                if len(set(df.columns) & EXPECTED_COLUMNS) >= 4:
                    df = df.dropna(how="all")
                    records = [_normalise_row(row.to_dict()) for _, row in df.iterrows()
                               if str(row.get("company_name", "")).strip() not in ("", "nan")]
                    if records:
                        return records
    except Exception:
        pass
    return []


def _try_csv(text: str) -> list[dict]:
    """Attempt to parse the text as a CSV/TSV with financial columns."""
    dialect = "excel-tab" if "\t" in text.split("\n")[0] else "excel"
    try:
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = list(reader)
        if not rows:
            return []
        headers = {h.strip().lower() for h in rows[0].keys()}
        # Accept if at least 4 of the expected columns are present
        if len(headers & EXPECTED_COLUMNS) < 4:
            return []
        return [_normalise_row(r) for r in rows]
    except Exception:
        return []


def _normalise_row(row: dict) -> dict:
    """Coerce a raw CSV row into the BigQuery schema types."""
    def flt(key, default=0.0):
        try:
            return float(str(row.get(key, default)).replace(",", "").strip())
        except (ValueError, TypeError):
            return default

    def intv(key, default=0):
        try:
            return int(str(row.get(key, default)).strip())
        except (ValueError, TypeError):
            return default

    revenue = flt("revenue_usd_m")
    ebitda = flt("ebitda_usd_m")
    total_debt = flt("total_debt_usd_m")
    equity = flt("total_assets_usd_m") - total_debt

    return {
        "company_name":      str(row.get("company_name", "Unknown")).strip(),
        "country":           str(row.get("country", "")).strip(),
        "sector":            str(row.get("sector", "")).strip(),
        "credit_rating":     str(row.get("credit_rating", "N/A")).strip(),
        "period_year":       intv("period_year", 2024),
        "revenue_usd_m":     revenue,
        "ebitda_usd_m":      ebitda,
        "ebitda_margin_pct": round((ebitda / revenue * 100) if revenue else 0.0, 2),
        "net_income_usd_m":  flt("net_income_usd_m"),
        "total_assets_usd_m": flt("total_assets_usd_m"),
        "total_debt_usd_m":  total_debt,
        "debt_to_equity":    round(total_debt / equity if equity > 0 else 0.0, 2),
        "current_ratio":     flt("current_ratio", 1.0),
    }


def _heuristic_extract(text: str) -> list[dict]:
    """Best-effort extraction from unstructured financial text."""
    def find(pattern, cast=float, default=0.0):
        m = re.search(pattern, text, re.IGNORECASE)
        try:
            return cast(m.group(1).replace(",", "")) if m else default
        except Exception:
            return default

    revenue = find(r"revenue[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    ebitda  = find(r"ebitda[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    assets  = find(r"total assets[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    debt    = find(r"total debt[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    ni      = find(r"net income[:\s]+\$?([\d,]+\.?\d*)\s*[mM]")
    cr      = find(r"current ratio[:\s]+([\d.]+)", default=1.0)
    equity  = assets - debt

    name_match = re.search(r"company[:\s]+([A-Za-z &.,]+)", text, re.IGNORECASE)
    company_name = name_match.group(1).strip() if name_match else "Unknown"

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
