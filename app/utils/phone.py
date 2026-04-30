"""
SmartBill AI — Phone Number Utilities

Bulletproof phone normalization and client lookup.
Handles whitespace, dashes, country code mismatches, etc.
"""

import re
from app.config import supabase


def normalize_phone(phone: str) -> str:
    """
    Normalize any phone input to canonical format: +<digits>

    Handles: spaces, dashes, parentheses, unicode whitespace, etc.
    Examples:
        '+91 8319494685'     -> '+918319494685'
        '918319494685'       -> '+918319494685'
        ' +91-8319-494685 '  -> '+918319494685'
    """
    if not phone:
        return phone
    # Strip ALL non-digit characters
    digits = re.sub(r'\D', '', str(phone).strip())
    if not digits:
        return phone
    return f'+{digits}'


def get_clean_digits(phone: str) -> str:
    """Extract only digits from any phone string (no + sign)."""
    return re.sub(r'\D', '', str(phone).strip())


async def find_client_by_phone(phone_raw: str) -> dict | None:
    """
    Robust client lookup that handles any phone format mismatch.

    Strategy (3 attempts):
      1. Exact match with +digits (canonical)
      2. Exact match with digits-only (no +)
      3. Fallback LIKE search on last 10 digits

    If found via fallback, auto-fixes the stored phone to canonical
    format so future lookups are instant.

    Returns:
        Client dict or None
    """
    normalized = normalize_phone(phone_raw)
    digits = get_clean_digits(normalized)
    last_10 = digits[-10:] if len(digits) >= 10 else digits

    print(f"🔍 Looking up phone: raw='{phone_raw}' normalized='{normalized}' last10='{last_10}'")

    # Attempt 1: Exact match with +digits (canonical format)
    response = supabase.table("clients").select("*").eq("phone", normalized).execute()
    if response.data:
        print(f"✅ Found client via exact match: {response.data[0].get('name')}")
        return response.data[0]

    # Attempt 2: Exact match digits-only (no + sign)
    response = supabase.table("clients").select("*").eq("phone", digits).execute()
    if response.data:
        print(f"✅ Found client via digits-only match: {response.data[0].get('name')}")
        return response.data[0]

    # Attempt 3: Fallback — LIKE search on last 10 digits
    response = supabase.table("clients").select("*").like("phone", f"%{last_10}%").execute()
    if response.data:
        client = response.data[0]
        print(f"✅ Found client via fuzzy last-10 match: {client.get('name')} (stored as '{client.get('phone')}')")

        # Auto-fix the stored phone to canonical format
        stored_phone = client.get('phone', '')
        if stored_phone != normalized:
            print(f"🔧 Auto-fixing stored phone: '{stored_phone}' -> '{normalized}'")
            supabase.table("clients").update({"phone": normalized}).eq("id", client["id"]).execute()

        return client

    print(f"❌ No client found for any variant of: {phone_raw}")
    return None
