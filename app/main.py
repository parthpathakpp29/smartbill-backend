from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
import httpx
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

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
    """
    try:
        # 🛡️ DEFENSIVE FORMATTING: Strip hidden spaces, add + only if missing
        clean_phone = str(phone_number).strip()
        db_phone = clean_phone if clean_phone.startswith('+') else f"+{clean_phone}"
        
        print(f"\n✅ Verification reply from: {db_phone}")
        
        # Find client by phone number
        response = supabase.table("clients") \
            .select("*") \
            .eq("phone", db_phone) \
            .execute()
        
        if not response.data or len(response.data) == 0:
            print(f"⚠️  No client found with phone: {db_phone}")
            print("💡 TIP: Check Supabase directly to ensure the number doesn't have hidden spaces saved in the row!")
            
            await send_whatsapp_message(
                phone_number,
                "Sorry, I don't recognize this number. Please ask your CA to add you first."
            )
            return
        
        client = response.data[0]
        
        # Check if already verified
        if client.get("phone_verified"):
            print(f"ℹ️  Client already verified: {client['name']}")
            await send_whatsapp_message(
                phone_number,
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
        await send_whatsapp_message(
            phone_number,
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
    """
    try:
        # 🛡️ DEFENSIVE FORMATTING
        clean_phone = str(phone_number).strip()
        db_phone = clean_phone if clean_phone.startswith('+') else f"+{clean_phone}"
        
        # Find client
        response = supabase.table("clients") \
            .select("*") \
            .eq("phone", db_phone) \
            .execute()
        
        if not response.data or len(response.data) == 0:
            print(f"⚠️  Image from unknown number: {db_phone}")
            await send_whatsapp_message(
                phone_number,
                "Please ask your CA to add your number to SmartBill AI first."
            )
            return
        
        client = response.data[0]
        
        # Check if verified
        if not client.get("phone_verified"):
            print(f"⚠️  Image from unverified client: {client['name']}")
            await send_whatsapp_message(
                phone_number,
                "Please reply YES first to verify your number before sending invoices."
            )
            return
        
        print(f"📸 Processing invoice from verified client: {client['name']}")
        
        # TODO: Download image, process with AI, save to database
        # For now, just acknowledge
        await send_whatsapp_message(
            phone_number,
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
        
        # Remove + for API call
        clean_phone = phone.replace("+", "")
        
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