"""claude_ocr_enhance.py

Enhanced OCR using Claude for handling non-standard document layouts.

Uses simple text extraction - no vision API complexity.
"""
import os
import json
import re
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


def enhance_with_claude(content: bytes, filename: str, existing_standard_records: list) -> dict:
    """
    Use Claude to extract financial data from document content.

    Simple approach: Convert to text and ask Claude to parse it.
    No vision API - just text extraction.
    """
    if not ANTHROPIC_API_KEY:
        return {
            "records": [],
            "confidence": "low",
            "extraction_method": "error",
            "error": "ANTHROPIC_API_KEY not set. Set: export ANTHROPIC_API_KEY=sk-ant-v1-...",
            "duplicates_flagged": [],
        }

    # Convert file to text
    text_content = _extract_text(content, filename)
    if not text_content or len(text_content.strip()) < 10:
        return {
            "records": [],
            "confidence": "low",
            "extraction_method": "error",
            "error": "Could not extract text from file",
            "duplicates_flagged": [],
        }

    # Call Claude
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = f"Extract all counterparty financial records from the following document text:\n\n{text_content[:8000]}"

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            tools=[{
                "name": "record_financial_data",
                "description": "Log extracted financial data for one or more companies found in a document.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "records": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "company_name": {"type": "string"},
                                    "country": {"type": "string"},
                                    "sector": {"type": "string"},
                                    "credit_rating": {"type": "string"},
                                    "period_year": {"type": "integer"},
                                    "revenue_usd_m": {"type": "number"},
                                    "ebitda_usd_m": {"type": "number"},
                                    "net_income_usd_m": {"type": "number"},
                                    "total_assets_usd_m": {"type": "number"},
                                    "total_debt_usd_m": {"type": "number"},
                                    "current_ratio": {"type": "number"}
                                },
                                "required": ["company_name", "period_year"]
                            }
                        }
                    },
                    "required": ["records"]
                }
            }],
            tool_choice={"type": "tool", "name": "record_financial_data"},
            messages=[{"role": "user", "content": prompt}]
        )

        # Extract records from tool use content
        records = []
        for content in response.content:
            if content.type == "tool_use" and content.name == "record_financial_data":
                records = content.input.get("records", [])
                break

        if not records:
            return {
                "records": [],
                "confidence": "low",
                "extraction_method": "claude_text",
                "error": "Claude returned no records",
                "duplicates_flagged": [],
            }

        # Normalize records
        normalized = []
        for rec in records:
            if isinstance(rec, dict) and rec.get("company_name"):
                normalized.append(_normalize_claude_record(rec))

        # Check for duplicates
        duplicates = _check_duplicates(normalized) if normalized else []

        return {
            "records": normalized,
            "confidence": "high" if normalized else "low",
            "extraction_method": "claude_text",
            "duplicates_flagged": duplicates,
        }

    except Exception as e:
        import sys
        print(f"[CLAUDE ERROR] {e}", file=sys.stderr)
        return {
            "records": [],
            "confidence": "low",
            "extraction_method": "error",
            "error": str(e),
            "duplicates_flagged": [],
        }


def _extract_text(content: bytes, filename: str) -> str:
    """Extract text from various file formats."""
    lower = filename.lower()

    # Plain text or CSV
    if lower.endswith(('.txt', '.csv', '.tsv')):
        return content.decode(errors="ignore").strip()

    # Excel
    if lower.endswith(('.xlsx', '.xls')):
        try:
            import pandas as pd
            import io
            xl = pd.ExcelFile(io.BytesIO(content))
            text_parts = []
            for sheet in xl.sheet_names[:3]:  # First 3 sheets
                df = pd.read_excel(xl, sheet_name=sheet, header=None)
                text_parts.append(df.to_string())
            return "\n".join(text_parts)
        except Exception:
            return ""

    # PDF - try to extract text
    if lower.endswith('.pdf'):
        try:
            import pypdf
            import io
            reader = pypdf.PdfReader(io.BytesIO(content))
            text_parts = []
            for page in reader.pages[:5]:  # First 5 pages
                text_parts.append(page.extract_text())
            return "\n".join(text_parts)
        except Exception:
            pass

    # Image - just return empty (can't OCR without special lib)
    if lower.endswith(('.jpg', '.jpeg', '.png', '.gif')):
        return ""

    # Default: try decoding as text
    return content.decode(errors="ignore").strip()


def _parse_json_response(text: str) -> list:
    """Extract JSON array from Claude response."""
    try:
        # Try direct parse
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in response
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
    return []


def _normalize_claude_record(rec: dict) -> dict:
    """Normalize Claude-extracted record to BigQuery schema."""
    def flt(key, default=0.0):
        val = rec.get(key, default)
        if val is None or str(val).strip() in ("", "nan", "N/A", "-", "null"):
            return default
        try:
            return float(str(val).replace(",", "").replace("$", "").strip())
        except (ValueError, TypeError):
            return default

    def intv(key, default=0):
        val = rec.get(key, default)
        try:
            return int(float(str(val).strip()))
        except (ValueError, TypeError):
            return default

    def strv(key, default=""):
        val = rec.get(key, default)
        s = str(val).strip() if val is not None else default
        return "" if s.lower() in ("nan", "none", "") else s

    revenue = flt("revenue_usd_m")
    ebitda = flt("ebitda_usd_m")
    total_debt = flt("total_debt_usd_m")
    assets = flt("total_assets_usd_m")
    equity = assets - total_debt

    return {
        "company_name": strv("company_name", "Unknown") or "Unknown",
        "country": strv("country"),
        "sector": strv("sector"),
        "credit_rating": strv("credit_rating", "N/A") or "N/A",
        "period_year": intv("period_year", 2024),
        "revenue_usd_m": revenue,
        "ebitda_usd_m": ebitda,
        "ebitda_margin_pct": round((ebitda / revenue * 100) if revenue else 0.0, 2),
        "net_income_usd_m": flt("net_income_usd_m"),
        "total_assets_usd_m": assets,
        "total_debt_usd_m": total_debt,
        "debt_to_equity": round(total_debt / equity if equity > 0 else 0.0, 2),
        "current_ratio": flt("current_ratio", 1.0),
    }


def _check_duplicates(records: list) -> list:
    """Check if any records are duplicates of existing counterparties in BQ."""
    if not records:
        return []

    try:
        from bigquery_helper import get_counterparties
        existing = get_counterparties()
        existing_names = {row["company_name"].lower() for row in existing}

        duplicates = []
        for rec in records:
            if rec["company_name"].lower() in existing_names:
                duplicates.append({
                    "company_name": rec["company_name"],
                    "status": "already_registered",
                    "action_required": "review_and_update",
                })
        return duplicates
    except Exception:
        return []


def claude_ocr_fallback(content: bytes, filename: str, standard_records: list) -> dict:
    """Fallback to Claude if standard OCR is uncertain or returns empty."""
    return enhance_with_claude(content, filename, standard_records)
