"""
SmartBill AI — Invoice Processing Pipeline (Background Worker)

This is the ORCHESTRATOR that runs in a background thread.
It coordinates the full 6-phase pipeline:

  Phase 3: Download image from Meta
  Phase 4: Upload to Supabase Storage (permanent archive)
  Phase 5: Extract data with Gemini AI
  Phase 6: Save to database + send WhatsApp confirmation

Phases 1-2 (ACK + async handoff) happen in main.py before this runs.
"""

import json
from datetime import datetime, timezone
from app.config import supabase
from app.utils.phone import get_clean_digits
from app.services.whatsapp import send_whatsapp_message, download_media
from app.services.storage import upload_invoice_image
from app.services.gemini import extract_invoice_data


async def process_invoice_background(
    invoice_id: str,
    client: dict,
    phone_number: str,
    image_id: str,
    mime_type: str,
) -> None:
    """
    The main background pipeline. Called via FastAPI BackgroundTasks.

    This function NEVER raises — all errors are caught, logged,
    and communicated to the user via WhatsApp.

    Args:
        invoice_id: Pre-created invoice row UUID (status='processing')
        client: Full client dict from database
        phone_number: Sender's phone (digits only, for WhatsApp replies)
        image_id: WhatsApp media ID
        mime_type: Image MIME type from webhook
    """
    clean_phone = get_clean_digits(phone_number)
    client_name = client.get("name", "there")
    client_id = client["id"]
    ca_id = client.get("ca_id")

    print(f"\n{'='*60}")
    print(f"🚀 PIPELINE START — Client: {client_name}, Invoice: {invoice_id}")
    print(f"{'='*60}")

    try:
        # ============================================
        # PHASE 3: Download image from Meta
        # ============================================
        print(f"\n📥 Phase 3: Downloading image from Meta...")
        image_bytes, actual_mime = await download_media(image_id)
        mime_type = actual_mime or mime_type  # Prefer Meta's reported MIME

        # ============================================
        # PHASE 4: Upload to Supabase Storage
        # ============================================
        print(f"\n☁️  Phase 4: Uploading to Supabase Storage...")
        image_url = await upload_invoice_image(image_bytes, client_id, mime_type)

        # Update the invoice row with the image URL
        supabase.table("invoices").update({
            "image_url": image_url,
            "whatsapp_media_id": image_id,
        }).eq("id", invoice_id).execute()

        # ============================================
        # PHASE 5: AI Extraction with Gemini
        # ============================================
        print(f"\n🤖 Phase 5: Extracting data with Gemini AI...")
        extracted = await extract_invoice_data(image_bytes, mime_type)

        # Check if extraction had critical errors
        has_error = "_error" in extracted
        confidence = extracted.get("confidence", 0.0)

        # Determine status based on extraction quality
        if has_error or confidence == 0.0:
            status = "needs_review"
        elif confidence < 0.5:
            status = "needs_review"
        else:
            status = "completed"

        # ============================================
        # PHASE 6A: Save to Database
        # ============================================
        print(f"\n💾 Phase 6A: Saving to database (status: {status})...")

        update_data = {
            "vendor_name": extracted.get("vendor_name"),
            "invoice_number": extracted.get("invoice_number"),
            "invoice_date": extracted.get("invoice_date"),
            "total_amount": extracted.get("total_amount", 0.0),
            "tax_amount": extracted.get("tax_amount", 0.0),
            "payment_method": extracted.get("payment_method"),
            "line_items": json.dumps(extracted.get("line_items", [])),
            "raw_ai_response": json.dumps(extracted),
            "confidence_score": confidence,
            "status": status,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Clean None values for Supabase
        update_data = {k: v for k, v in update_data.items() if v is not None}

        supabase.table("invoices").update(update_data).eq("id", invoice_id).execute()

        print(f"   ✅ Invoice saved to database!")

        # ============================================
        # PHASE 6B: Send WhatsApp Confirmation
        # ============================================
        print(f"\n📤 Phase 6B: Sending confirmation to {clean_phone}...")

        vendor = extracted.get("vendor_name") or "Unknown vendor"
        total = extracted.get("total_amount", 0.0)
        tax = extracted.get("tax_amount", 0.0)

        if status == "completed":
            confirmation = (
                f"✅ Invoice processed successfully!\n\n"
                f"📋 Vendor: {vendor}\n"
                f"💰 Total: ₹{total:,.2f}\n"
                f"🧾 Tax: ₹{tax:,.2f}\n"
                f"\nYour CA can see this in their dashboard now."
            )
        else:
            confirmation = (
                f"📋 Invoice received and saved!\n\n"
                f"⚠️ Some details couldn't be read clearly. "
                f"Your CA will review this manually.\n"
                f"\nImage has been saved for reference."
            )

        await send_whatsapp_message(clean_phone, confirmation)

        print(f"\n{'='*60}")
        print(f"✅ PIPELINE COMPLETE — {status.upper()}")
        print(f"   Vendor: {vendor}")
        print(f"   Total: ₹{total}")
        print(f"   Confidence: {confidence}")
        print(f"{'='*60}\n")

    except Exception as e:
        # ============================================
        # ERROR HANDLING: Update DB + notify user
        # ============================================
        error_msg = str(e)
        print(f"\n❌ PIPELINE FAILED: {error_msg}")

        # Update invoice status to failed
        try:
            supabase.table("invoices").update({
                "status": "failed",
                "processing_error": error_msg[:500],  # Truncate long errors
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", invoice_id).execute()
        except Exception as db_err:
            print(f"   ❌ Couldn't update invoice status: {db_err}")

        # Notify user about the failure
        try:
            await send_whatsapp_message(
                clean_phone,
                "😔 Sorry, I couldn't process that invoice. "
                "Please try sending a clearer photo, or ask your CA for help."
            )
        except Exception as wa_err:
            print(f"   ❌ Couldn't send failure notification: {wa_err}")
