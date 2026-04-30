"""
SmartBill AI — Main Application

Slim entry point that handles:
  - FastAPI app setup + CORS
  - WhatsApp webhook (GET verify + POST receive)
  - Client welcome message API
  - Routes incoming messages to appropriate handlers

Heavy processing is delegated to service modules via BackgroundTasks.
"""

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
import re
from datetime import datetime, timezone

from app.config import supabase, WHATSAPP_VERIFY_TOKEN
from app.utils.phone import normalize_phone, get_clean_digits, find_client_by_phone
from app.services.whatsapp import send_whatsapp_message
from app.services.invoice import process_invoice_background


# ============================================
# APP SETUP
# ============================================
app = FastAPI(
    title="SmartBill AI Backend",
    description="Async invoice processing via WhatsApp + Gemini AI",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# HEALTH CHECK
# ============================================
@app.get("/")
def health_check():
    return {
        "status": "healthy",
        "service": "smartbill-api",
        "version": "2.0.0",
        "message": "SmartBill AI Backend is running",
    }


# ============================================
# WHATSAPP WEBHOOK VERIFICATION (GET)
# ============================================
@app.get("/webhooks/whatsapp")
async def verify_webhook(request: Request):
    """Meta calls this endpoint to verify the webhook."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    print(f"📞 Webhook verification request")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        print("✅ Webhook verified!")
        return PlainTextResponse(content=challenge)
    else:
        print("❌ Verification failed")
        raise HTTPException(status_code=403, detail="Verification failed")


# ============================================
# WHATSAPP WEBHOOK RECEIVER (POST)
# ============================================
@app.post("/webhooks/whatsapp")
async def receive_whatsapp_message(request: Request, background_tasks: BackgroundTasks):
    """
    Receive messages from WhatsApp.

    TEXT messages → handle verification
    IMAGE messages → instant ACK + background processing pipeline
    """
    try:
        body = await request.json()
        print("\n" + "=" * 50)
        print("📨 WhatsApp webhook received")
        print("=" * 50)

        if body.get("object") != "whatsapp_business_account":
            return {"status": "ok"}

        entries = body.get("entry", [])

        for entry in entries:
            changes = entry.get("changes", [])

            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])

                if not messages:
                    continue

                for message in messages:
                    from_phone = message.get("from")  # Format: "919876543210"
                    message_type = message.get("type")

                    print(f"\n📩 Message from: {from_phone}")
                    print(f"   Type: {message_type}")

                    # ── TEXT: Handle verification replies ──
                    if message_type == "text":
                        text_body = message.get("text", {}).get("body", "").strip().upper()
                        print(f"   💬 Text: {text_body}")

                        if text_body in ["YES", "Y", "CONFIRM", "OK"]:
                            await _handle_verification(from_phone)

                    # ── IMAGE: Invoice processing pipeline ──
                    elif message_type == "image":
                        image_data = message.get("image", {})
                        image_id = image_data.get("id")
                        mime_type = image_data.get("mime_type", "image/jpeg")
                        caption = image_data.get("caption", "")

                        print(f"   📸 Image ID: {image_id}")
                        print(f"   📸 MIME: {mime_type}")
                        print(f"   📸 Caption: {caption}")

                        # Phase 1 + 2: Verify, ACK, and hand off to background
                        # Handle invoice image with simplified processing
                        await handle_invoice_image(from_phone, image_id, mime_type)

        return {"status": "ok"}

    except Exception as e:
        print(f"❌ Webhook error: {str(e)}")
        return {"status": "error", "message": str(e)}


# ============================================
# VERIFICATION HANDLER
# ============================================
async def _handle_verification(phone_number: str):
    """
    When client replies YES, mark them as verified in the database.

    Args:
        phone_number: Format "919876543210" (no + sign, from WhatsApp)
    """
    try:
        print(f"\n✅ Verification reply from: {phone_number}")

        client = await find_client_by_phone(phone_number)
        clean_phone = get_clean_digits(phone_number)

        if not client:
            await send_whatsapp_message(
                clean_phone,
                "Sorry, I don't recognize this number. Please ask your CA to add you first.",
            )
            return

        # Already verified?
        if client.get("phone_verified"):
            print(f"ℹ️  Client already verified: {client['name']}")
            await send_whatsapp_message(
                clean_phone,
                f"Hi {client['name']}! You're already verified. Send me invoice photos anytime! 📸",
            )
            return

        # Mark as verified
        supabase.table("clients").update({"phone_verified": True}).eq(
            "id", client["id"]
        ).execute()

        print(f"✅ Client verified: {client['name']}")

        await send_whatsapp_message(
            clean_phone,
            f"Perfect! ✅ Your number is now verified.\n\n"
            f"Hi {client['name']}, you can now send your invoice photos directly here.\n\n"
            f"Just snap a clear photo of any invoice and send it to me. "
            f"I'll extract all the details automatically!\n\n"
            f"Try it now — send me an invoice photo! 📸",
        )

    except Exception as e:
        print(f"❌ Verification error: {str(e)}")


# ============================================
# HANDLE INVOICE IMAGE (SIMPLIFIED)
# ============================================
async def handle_invoice_image(phone_number: str, image_id: str, mime_type: str):
    """
    Handle incoming invoice image - simplified version
    
    Args:
        phone_number: Format "919876543210"
        image_id: WhatsApp media ID
        mime_type: Image MIME type
    """
    try:
        phone_with_plus = f"+{phone_number}"
        
        # Find client using simple lookup
        response = supabase.table("clients") \
            .select("*") \
            .eq("phone", phone_with_plus) \
            .execute()
        
        if not response.data or len(response.data) == 0:
            print(f"Image from unknown number: {phone_with_plus}")
            await send_whatsapp_message(
                phone_number,
                "Please ask your CA to add your number to SmartBill AI first."
            )
            return
        
        client = response.data[0]
        
        # Check if verified
        if not client.get("phone_verified"):
            print(f"Image from unverified client: {client['name']}")
            await send_whatsapp_message(
                phone_number,
                "Please reply YES first to verify your number before sending invoices."
            )
            return
        
        print(f"Processing invoice from verified client: {client['name']}")
        
        # Create a basic invoice entry in database first
        invoice_data = {
            "client_id": client["id"],
            "vendor_name": "Processing...",
            "invoice_number": f"WA-{image_id[:8]}",
            "invoice_date": None,
            "total_amount": None,
            "tax_amount": None,
            "payment_method": None,
            "image_url": f"https://graph.facebook.com/v21.0/{image_id}",
            "status": "processing",
            "confidence_score": None,
            "extracted_data": None,
            "created_at": None,  # Will be set by database
            "processed_at": None
        }

        # Save to database
        result = supabase.table("invoices").insert(invoice_data).execute()

        if result.data:
            invoice_id = result.data[0]["id"]
            print(f"Created invoice entry: {invoice_id}")
            
            # Now process the image in background
            try:
                # Download image from WhatsApp
                from app.services.whatsapp import download_media
                image_bytes, actual_mime = await download_media(image_id)
                print(f"Downloaded image: {len(image_bytes)} bytes")

                # Process with Gemini AI
                from app.services.gemini import extract_invoice_data
                extracted = await extract_invoice_data(image_bytes, actual_mime or mime_type)
                print(f"Extracted data: {extracted}")

                # Update invoice with extracted data
                update_data = {
                    "vendor_name": extracted.get("vendor_name", "Unknown Vendor"),
                    "invoice_number": extracted.get("invoice_number", f"WA-{image_id[:8]}"),
                    "invoice_date": extracted.get("invoice_date"),
                    "total_amount": extracted.get("total_amount"),
                    "tax_amount": extracted.get("tax_amount"),
                    "payment_method": extracted.get("payment_method"),
                    "status": "completed" if extracted.get("confidence", 0) > 0.5 else "needs_review",
                    "confidence_score": extracted.get("confidence", 0.0),
                    "extracted_data": extracted,
                    "processed_at": datetime.now(timezone.utc).isoformat()
                }

                # Clean None values
                update_data = {k: v for k, v in update_data.items() if v is not None}

                supabase.table("invoices").update(update_data).eq("id", invoice_id).execute()
                print(f"Updated invoice with extracted data")

                await send_whatsapp_message(
                    phone_number,
                    f"Got it! I've processed your invoice (ID: {invoice_id[:8]}). Your CA can see it in their dashboard now."
                )

            except Exception as processing_error:
                print(f"Processing error: {str(processing_error)}")
                # Update with error status
                supabase.table("invoices").update({
                    "status": "needs_review",
                    "processing_error": str(processing_error)[:500],
                    "processed_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", invoice_id).execute()

                await send_whatsapp_message(
                    phone_number,
                    f"Got your invoice (ID: {invoice_id[:8]})! I had some trouble reading it, but your CA will review it manually."
                )
        else:
            print("Failed to create invoice entry")
            await send_whatsapp_message(
                phone_number,
                "Sorry, I had trouble saving your invoice. Please try again."
            )
        

    except Exception as e:
        print(f"❌ Invoice handler error: {str(e)}")
        # Still try to notify the user
        try:
            await send_whatsapp_message(
                phone_number,
                "Sorry, something went wrong. Please try sending the invoice again."
            )
        except Exception:
            pass


# ============================================
# API: SEND WELCOME MESSAGE
# ============================================
@app.post("/api/send-welcome")
async def send_welcome_message(request: Request):
    """Send welcome message to new client (called by Next.js frontend)."""
    try:
        data = await request.json()
        phone = data.get("phone")  # Format: "+919876543210"
        client_name = data.get("client_name", "there")

        if not phone:
            raise HTTPException(status_code=400, detail="Phone number required")

        # Normalize and auto-fix DB phone format
        normalized = normalize_phone(phone)
        print(f"📱 Welcome message — raw: '{phone}' normalized: '{normalized}'")

        digits = get_clean_digits(phone)
        last_10 = digits[-10:] if len(digits) >= 10 else digits
        response = supabase.table("clients").select("id, phone").like("phone", f"%{last_10}%").execute()
        if response.data:
            stored = response.data[0].get("phone", "")
            if stored != normalized:
                print(f"🔧 Fixing stored phone: '{stored}' -> '{normalized}'")
                supabase.table("clients").update({"phone": normalized}).eq(
                    "id", response.data[0]["id"]
                ).execute()

        # Send the welcome message (WhatsApp API wants digits only)
        clean_phone = get_clean_digits(phone)

        message = (
            f"Hi {client_name}! 👋\n\n"
            f"You've been added to SmartBill AI by your CA.\n\n"
            f"From now on, send your invoice photos directly here, "
            f"and I'll automatically extract the details.\n\n"
            f"📸 Reply YES to verify your number and get started!"
        )

        result = await send_whatsapp_message(clean_phone, message)

        return {
            "success": True,
            "message_id": result.get("messages", [{}])[0].get("id"),
        }

    except Exception as e:
        print(f"❌ Welcome message error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))