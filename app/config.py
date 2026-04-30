"""
SmartBill AI — Centralized Configuration

Single source of truth for all environment variables,
API clients, and shared constants.
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai

load_dotenv()

# ============================================
# SUPABASE (Service Role — bypasses RLS)
# ============================================
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")  # Must be service_role key

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================
# GOOGLE GEMINI AI
# ============================================
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

if not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY must be set in .env")

gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

# Model to use for invoice extraction
GEMINI_MODEL = "gemini-2.0-flash"

# ============================================
# WHATSAPP / META
# ============================================
WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")

WHATSAPP_API_VERSION = "v21.0"
WHATSAPP_BASE_URL = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"

# ============================================
# STORAGE
# ============================================
INVOICE_IMAGES_BUCKET = "invoice-images"
