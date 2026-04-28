from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
import httpx
import os
import re
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


# ============================================
# PHONE NUMBER UTILITIES (BULLETPROOF)
# ============================================
def normalize_phone(phone: str) -> str:
    """
    Normalize any phone input to canonical format: +<digits>
    Handles: spaces, dashes, parentheses, unicode whitespace, etc.
    Examples:
        '+91 8319494685'  -> '+918319494685'
        '918319494685'    -> '+918319494685'
        ' +91-8319-494685 ' -> '+918319494685'
    """
    if not phone:
        return phone
    # Strip ALL non-digit characters
    digits = re.sub(r'\D', '', str(phone).strip())
    if not digits:
        return phone
    return f'+{digits}'


async def find_client_by_phone(phone_raw: str):
    """
    Robust client lookup that handles any phone format mismatch.
    Tries: exact match with +digits, digits-only, and finally
    a LIKE search on the last 10 digits as a fallback.
    Returns the client dict or None.
    """
    normalized = normalize_phone(phone_raw)
    digits = re.sub(r'\D', '', normalized)
    last_10 = digits[-10:] if len(digits) >= 10 else digits

    print(f"🔍 Looking up phone: raw='{phone_raw}' normalized='{normalized}' digits='{digits}' last10='{last_10}'")

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
    # This catches hidden whitespace, extra characters, etc.
    response = supabase.table("clients").select("*").like("phone", f"%{last_10}%").execute()
    if response.data:
        print(f"✅ Found client via fuzzy last-10 match: {response.data[0].get('name')} (stored as '{response.data[0].get('phone')}')")
        # Fix the stored phone to canonical format so future lookups are instant
        stored_phone = response.data[0].get('phone', '')
        if stored_phone != normalized:
            print(f"🔧 Auto-fixing stored phone: '{stored_phone}' -> '{normalized}'")
            supabase.table("clients").update({"phone": normalized}).eq("id", response.data[0]["id"]).execute()
        return response.data[0]

    print(f"❌ No client found for any variant of: {phone_raw}")
    return None

app = FastAPI(title="SmartBill AI Backend")

# CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WhatsApp API credentials
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

# Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


@app.get("/")
def health_check():
    return {
        "status": "healthy",
        "service": "smartbill-api",
        "version": "1.0.0",
        "message": "SmartBill AI Backend is running"
    }


# ============================================
# WHATSAPP WEBHOOK VERIFICATION (GET)
# ============================================
@app.get("/webhooks/whatsapp")
async def verify_webhook(request: Request):
    """Meta calls this to verify webhook"""
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
async def receive_whatsapp_message(request: Request):
    """Receive messages from WhatsApp"""
    try:
        body = await request.json()
        print("\n" + "="*50)
        print("📨 WhatsApp webhook received")
        print("="*50)
        
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
                    
                    # Handle TEXT messages (for verification)
                    if message_type == "text":
                        text_body = message.get("text", {}).get("body", "").strip().upper()
                        print(f"   💬 Text: {text_body}")
                        
                        # Check if it's a verification response
                        if text_body in ["YES", "Y", "CONFIRM", "OK"]:
                            await handle_verification(from_phone)
                    
                    # Handle IMAGE messages (invoices)
                    elif message_type == "image":
                        image_data = message.get("image", {})
                        image_id = image_data.get("id")
                        mime_type = image_data.get("mime_type")
                        caption = image_data.get("caption", "")
                        
                        print(f"   📸 Image ID: {image_id}")
                        print(f"   📸 MIME: {mime_type}")
                        print(f"   📸 Caption: {caption}")
                        
                        # TODO: Download and process invoice image
                        # We'll add this in next step
                        await handle_invoice_image(from_phone, image_id, mime_type)
        
        return {"status": "ok"}
    
    except Exception as e:
        print(f"❌ Webhook error: {str(e)}")
        return {"status": "error", "message": str(e)}


# ============================================
# HANDLE PHONE VERIFICATION
# ============================================
async def handle_verification(phone_number: str):
    """
    When client replies YES, mark them as verified
    
    Args:
        phone_number: Format "919876543210" (no + sign, from WhatsApp)
    """
    try:
        print(f"\n✅ Verification reply from: {phone_number}")
        
        # Use robust phone lookup
        client = await find_client_by_phone(phone_number)
        
        if not client:
            # Clean phone for sending reply (digits only, no +)
            clean_phone = re.sub(r'\D', '', str(phone_number))
            await send_whatsapp_message(
                clean_phone,
                "Sorry, I don't recognize this number. Please ask your CA to add you first."
            )
            return
        
        # Check if already verified
        if client.get("phone_verified"):
            print(f"ℹ️  Client already verified: {client['name']}")
            clean_phone = re.sub(r'\D', '', str(phone_number))
            await send_whatsapp_message(
                clean_phone,
                f"Hi {client['name']}! You're already verified. Send me invoice photos anytime! 📸"
            )
            return
        
        # Update to verified
        supabase.table("clients") \
            .update({"phone_verified": True}) \
            .eq("id", client["id"]) \
            .execute()
        
        print(f"✅ Client verified: {client['name']}")
        
        # Send confirmation
        clean_phone = re.sub(r'\D', '', str(phone_number))
        await send_whatsapp_message(
            clean_phone,
            f"""Perfect! ✅ Your number is now verified.

Hi {client['name']}, you can now send your invoice photos directly here.

Just snap a clear photo of any invoice and send it to me. I'll extract all the details automatically!

Try it now - send me an invoice photo! 📸"""
        )
        
    except Exception as e:
        print(f"❌ Verification error: {str(e)}")


# ============================================
# HANDLE INVOICE IMAGE
# ============================================
async def handle_invoice_image(phone_number: str, image_id: str, mime_type: str):
    """
    Handle incoming invoice image
    
    Args:
        phone_number: Format "919876543210" (from WhatsApp)
        image_id: WhatsApp media ID
        mime_type: Image MIME type
    """
    try:
        clean_phone = re.sub(r'\D', '', str(phone_number))
        
        # Use robust phone lookup
        client = await find_client_by_phone(phone_number)
        
        if not client:
            print(f"⚠️  Image from unknown number: {phone_number}")
            await send_whatsapp_message(
                clean_phone,
                "Please ask your CA to add your number to SmartBill AI first."
            )
            return
        
        # Check if verified
        if not client.get("phone_verified"):
            print(f"⚠️  Image from unverified client: {client['name']}")
            await send_whatsapp_message(
                clean_phone,
                "Please reply YES first to verify your number before sending invoices."
            )
            return
        
        print(f"📸 Processing invoice from verified client: {client['name']}")
        
        # TODO: Download image, process with AI, save to database
        # For now, just acknowledge
        await send_whatsapp_message(
            clean_phone,
            f"Got it! 📸 I'm processing your invoice now. You'll see it in your CA's dashboard shortly."
        )
        
    except Exception as e:
        print(f"❌ Image handling error: {str(e)}")


# ============================================
# SEND WHATSAPP MESSAGE
# ============================================
async def send_whatsapp_message(to_phone: str, message_text: str):
    """
    Send WhatsApp message
    
    Args:
        to_phone: "919876543210" (NO + sign)
        message_text: Message content
    """
    url = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message_text}
    }
    
    print(f"\n📤 Sending WhatsApp to: {to_phone}")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            
            print(f"   Status: {response.status_code}")
            
            if response.status_code != 200:
                error_body = response.text
                print(f"   ❌ Error: {error_body}")
                raise Exception(f"WhatsApp API error: {error_body}")
            
            print(f"   ✅ Message sent!")
            return response.json()
    
    except Exception as e:
        print(f"❌ Send failed: {str(e)}")
        raise


# ============================================
# API: SEND WELCOME MESSAGE
# ============================================
@app.post("/api/send-welcome")
async def send_welcome_message(request: Request):
    """Send welcome message to new client"""
    try:
        data = await request.json()
        phone = data.get("phone")  # Format: "+919876543210"
        client_name = data.get("client_name", "there")
        
        if not phone:
            raise HTTPException(status_code=400, detail="Phone number required")
        
        # Normalize the phone and also fix it in the database
        normalized = normalize_phone(phone)
        print(f"📱 Welcome message - raw: '{phone}' normalized: '{normalized}'")
        
        # Update the stored phone to canonical format (fixes whitespace issues)
        digits = re.sub(r'\D', '', normalized)
        last_10 = digits[-10:] if len(digits) >= 10 else digits
        response = supabase.table("clients").select("id, phone").like("phone", f"%{last_10}%").execute()
        if response.data:
            stored = response.data[0].get('phone', '')
            if stored != normalized:
                print(f"🔧 Fixing stored phone at welcome time: '{stored}' -> '{normalized}'")
                supabase.table("clients").update({"phone": normalized}).eq("id", response.data[0]["id"]).execute()
        
        # Remove + for WhatsApp API call (expects digits only)
        clean_phone = re.sub(r'\D', '', phone)
        
        message = f"""Hi {client_name}! 👋

You've been added to SmartBill AI by your CA.

From now on, send your invoice photos directly here, and I'll automatically extract the details.

📸 Reply YES to verify your number and get started!"""
        
        result = await send_whatsapp_message(clean_phone, message)
        
        return {
            "success": True,
            "message_id": result.get("messages", [{}])[0].get("id")
        }
    
    except Exception as e:
        print(f"❌ Welcome message error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))