"""ocr_simulator.py
Simulated OCR module – placeholder for real text-extraction logic.

In production, replace the body of ``simulate_ocr`` with a call to an
actual OCR library such as ``pytesseract``, Google Cloud Vision API, or
Document AI.
"""


def simulate_ocr(content: bytes) -> str:
    """Convert raw file bytes to plain text (simulated OCR).

    This placeholder simply decodes the bytes as UTF-8.  Non-decodable
    bytes are silently ignored so that the pipeline never crashes on
    binary files.

    Args:
        content: Raw bytes downloaded from Cloud Storage.

    Returns:
        The decoded text content of the file.
    """
    return content.decode(errors='ignore')
