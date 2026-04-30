"""
SmartBill AI — Gemini AI Extraction Service

Uses Google Gemini 1.5 Flash vision model to extract
structured invoice data from photos.

The AI is prompted as a strict data extractor (not a chatbot)
and forced to return pure JSON with specific keys.
"""

import json
import re
from google.genai import types
from app.config import gemini_client, GEMINI_MODEL


# The strict extraction prompt — forces JSON output, no chatbot behavior
EXTRACTION_PROMPT = """You are a strict invoice data extractor for an Indian accounting system.

ANALYZE the invoice image and extract the following fields.
Return ONLY a valid JSON object — no markdown, no explanation, no extra text.

Required JSON structure:
{
    "vendor_name": "Name of the shop/business on the invoice",
    "invoice_number": "Invoice/bill number if visible, else null",
    "invoice_date": "Date in YYYY-MM-DD format if visible, else null",
    "total_amount": 0.00,
    "tax_amount": 0.00,
    "payment_method": "cash/upi/card/bank_transfer/unknown",
    "line_items": [
        {
            "description": "Item name",
            "quantity": 1,
            "unit_price": 0.00,
            "amount": 0.00
        }
    ],
    "confidence": 0.85
}

RULES:
1. All amounts must be numbers (not strings). Use 0.00 if unclear.
2. "confidence" is your self-assessed accuracy from 0.0 to 1.0.
3. If you cannot read a field, set it to null — do NOT guess.
4. For Indian invoices, look for CGST/SGST/IGST as tax fields.
5. Sum all tax components into a single "tax_amount".
6. "line_items" can be empty [] if individual items aren't readable.
7. Dates: convert to YYYY-MM-DD regardless of original format.
8. RETURN ONLY THE JSON. No ```json markers. No explanation."""


def _clean_ai_response(raw_text: str) -> str:
    """
    Strip markdown formatting and extract pure JSON from AI response.
    
    Gemini sometimes wraps output in ```json ... ``` despite being told not to.
    This function handles all known quirks.
    """
    text = raw_text.strip()

    # Remove ```json ... ``` wrapper
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    # Remove any leading/trailing whitespace
    text = text.strip()

    return text


def _validate_and_normalize(data: dict) -> dict:
    """
    Validate and normalize the extracted data.
    Ensures all expected keys exist with correct types.
    """
    # Ensure numeric fields are actually numbers
    for field in ["total_amount", "tax_amount"]:
        val = data.get(field)
        if val is None:
            data[field] = 0.0
        elif isinstance(val, str):
            try:
                # Handle Indian format: "1,500.00" -> 1500.00
                data[field] = float(val.replace(",", ""))
            except ValueError:
                data[field] = 0.0
        else:
            data[field] = float(val)

    # Ensure confidence is a float between 0 and 1
    confidence = data.get("confidence", 0.5)
    if isinstance(confidence, str):
        try:
            confidence = float(confidence)
        except ValueError:
            confidence = 0.5
    data["confidence"] = max(0.0, min(1.0, float(confidence)))

    # Ensure line_items is a list
    if not isinstance(data.get("line_items"), list):
        data["line_items"] = []

    # Ensure string fields exist
    for field in ["vendor_name", "invoice_number", "invoice_date", "payment_method"]:
        if field not in data:
            data[field] = None

    return data


async def extract_invoice_data(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    Extract structured invoice data from an image using Gemini Vision.

    Args:
        image_bytes: Raw image bytes
        mime_type: MIME type (e.g. "image/jpeg", "image/png")

    Returns:
        Dict with keys: vendor_name, invoice_number, invoice_date,
        total_amount, tax_amount, payment_method, line_items, confidence

    Never raises — returns partial/empty data on failure with confidence=0.
    """
    try:
        print(f"\n🤖 Sending {len(image_bytes)} bytes to Gemini ({GEMINI_MODEL})...")

        # Build the multimodal request: prompt + image bytes
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                EXTRACTION_PROMPT,
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=mime_type,
                ),
            ],
        )

        raw_text = response.text
        print(f"   📝 Raw AI response ({len(raw_text)} chars): {raw_text[:200]}...")

        # Clean and parse JSON
        clean_text = _clean_ai_response(raw_text)
        data = json.loads(clean_text)

        # Validate and normalize
        data = _validate_and_normalize(data)

        print(f"   ✅ Extracted: vendor={data.get('vendor_name')}, "
              f"total=₹{data.get('total_amount')}, "
              f"tax=₹{data.get('tax_amount')}, "
              f"confidence={data.get('confidence')}")

        return data

    except json.JSONDecodeError as e:
        print(f"   ❌ JSON parse error: {e}")
        print(f"   📝 Raw text was: {raw_text[:500] if 'raw_text' in dir() else 'N/A'}")
        return {
            "vendor_name": None,
            "invoice_number": None,
            "invoice_date": None,
            "total_amount": 0.0,
            "tax_amount": 0.0,
            "payment_method": "unknown",
            "line_items": [],
            "confidence": 0.0,
            "_error": f"JSON parse failed: {str(e)}",
        }

    except Exception as e:
        print(f"   ❌ Gemini extraction error: {str(e)}")
        return {
            "vendor_name": None,
            "invoice_number": None,
            "invoice_date": None,
            "total_amount": 0.0,
            "tax_amount": 0.0,
            "payment_method": "unknown",
            "line_items": [],
            "confidence": 0.0,
            "_error": f"Gemini error: {str(e)}",
        }
