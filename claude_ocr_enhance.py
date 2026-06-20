"""claude_ocr_enhance.py
Enhanced OCR using Claude vision API for handling non-standard document layouts.

Workflow:
  1. Try standard OCR (simulate_ocr)
  2. If uncertain or format unrecognized, use Claude vision
  3. Check for duplicate counterparties in BigQuery
  4. Flag for user review if duplicate detected
"""
import os
import base64
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


def _get_claude_client():
    """Get or create Anthropic client."""
    if not ANTHROPIC_API_KEY:
        return None
    return Anthropic(api_key=ANTHROPIC_API_KEY)


def is_pdf_or_image(filename: str) -> bool:
    """Check if file is PDF or image."""
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"])


def encode_file_to_base64(content: bytes) -> str:
    """Encode file bytes to base64."""
    return base64.standard_b64encode(content).decode("utf-8")


def enhance_with_claude(content: bytes, filename: str, existing_standard_records: list) -> dict:
    """
    Use Claude to intelligently parse a document, handling non-standard layouts.

    Args:
        content: File bytes
        filename: Original filename
        existing_standard_records: Records extracted via standard OCR (may be empty)

    Returns:
        Dict with:
        - records: List of enhanced counterparty records
        - confidence: "high" | "medium" | "low"
        - extraction_method: "claude_vision" | "claude_text"
        - duplicates_flagged: List of potential duplicates found
    """
    client = _get_claude_client()
    if not client:
        return {
            "records": [],
            "confidence": "low",
            "extraction_method": "error",
            "error": "ANTHROPIC_API_KEY not set - Claude enhancement unavailable. Set env var: export ANTHROPIC_API_KEY=sk-...",
            "duplicates_flagged": [],
        }

    # Prepare the document for Claude
    is_binary = is_pdf_or_image(filename)

    prompt = f"""You are a financial document parser. Analyze the provided document and extract counterparty financial information.

Required fields to extract (if present):
- company_name: Legal company name
- country: Country of incorporation/operation
- sector: Industry/sector (e.g., Oil & Gas, Trading House, Metals & Mining)
- credit_rating: Credit rating (e.g., BBB, BB+, N/A)
- period_year: Fiscal year or reporting period
- revenue_usd_m: Revenue in USD millions
- ebitda_usd_m: EBITDA in USD millions
- net_income_usd_m: Net income in USD millions
- total_assets_usd_m: Total assets in USD millions
- total_debt_usd_m: Total debt in USD millions
- current_ratio: Current ratio

Return ONLY a JSON array of records. Each record must have all fields (use "" or 0 for missing values).
Example: [{{ "company_name": "Shell plc", "country": "Netherlands", "sector": "Oil & Gas", ... }}]

If the document layout is non-standard or contains multiple counterparties, extract each separately.
If you cannot extract meaningful financial data, return an empty array [].
"""

    try:
        if is_binary:
            # Claude vision: encode as base64 and send
            b64_content = encode_file_to_base64(content)

            # Determine media type
            lower_filename = filename.lower()
            if lower_filename.endswith(".pdf"):
                media_type = "application/pdf"
            elif lower_filename.endswith((".png", ".jpg", ".jpeg")):
                media_type = "image/png" if lower_filename.endswith(".png") else "image/jpeg"
            else:
                media_type = "image/webp"

            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64_content,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )
            extraction_method = "claude_vision"
        else:
            # Claude text: send raw text
            text_content = content.decode(errors="ignore").strip()
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": f"{prompt}\n\n=== Document Content ===\n{text_content}",
                    }
                ],
            )
            extraction_method = "claude_text"

        # Parse Claude's JSON response
        response_text = response.content[0].text.strip()

        # Try to extract JSON from response
        import json
        try:
            records = json.loads(response_text)
        except json.JSONDecodeError:
            # If not pure JSON, try to find JSON in the response
            import re
            json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
            if json_match:
                records = json.loads(json_match.group())
            else:
                records = []

        if not isinstance(records, list):
            records = [records] if isinstance(records, dict) else []

        # Normalize records to expected schema
        normalized = []
        for rec in records:
            if isinstance(rec, dict) and rec.get("company_name"):
                normalized.append(_normalize_claude_record(rec))

        # Check for duplicates
        duplicates = _check_duplicates(normalized) if normalized else []

        return {
            "records": normalized,
            "confidence": "high" if normalized else "low",
            "extraction_method": extraction_method,
            "duplicates_flagged": duplicates,
        }

    except Exception as e:
        return {
            "records": existing_standard_records,
            "confidence": "low",
            "extraction_method": "error",
            "error": str(e),
            "duplicates_flagged": [],
        }


def _normalize_claude_record(rec: dict) -> dict:
    """Normalize Claude-extracted record to BigQuery schema."""
    def flt(key, default=0.0):
        val = rec.get(key, default)
        if val is None or str(val).strip() in ("", "nan", "N/A", "-"):
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
    """
    Fallback to Claude if standard OCR is uncertain or returns empty.

    Returns enhanced records with metadata about the extraction.
    """
    return enhance_with_claude(content, filename, standard_records)
