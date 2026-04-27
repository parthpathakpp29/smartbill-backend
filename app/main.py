from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="SmartBill AI Backend")

# CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://*.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WhatsApp API credentials from .env
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

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
    """
    Meta will call this endpoint with GET to verify the webhook.
    We need to return the challenge if verify_token matches.
    """
    # Get query parameters
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    print(f"📞 Webhook verification request received")
    print(f"   Mode: {mode}")
    print(f"   Token: {token}")
    print(f"   Challenge: {challenge}")
    
    # Check if mode and token are correct
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        print("✅ Webhook verified successfully!")
        # Return the challenge as plain text (Meta requires this)
        return PlainTextResponse(content=challenge)
    else:
        print("❌ Verification failed - token mismatch")
        raise HTTPException(status_code=403, detail="Verification token mismatch")


# ============================================
# WHATSAPP WEBHOOK RECEIVER (POST)
# ============================================
@app.post("/webhooks/whatsapp")
async def receive_whatsapp_message(request: Request):
    """
    Meta will POST to this endpoint when:
    - Someone sends a message to our WhatsApp number
    - Message status updates (delivered, read, etc.)
    """
    try:
        body = await request.json()
        print("\n" + "="*50)
        print("📨 Received WhatsApp webhook!")
        print("="*50)
        print(f"Full payload: {body}")
        
        # Check if this is a message event
        if body.get("object") == "whatsapp_business_account":
            entries = body.get("entry", [])
            
            for entry in entries:
                changes = entry.get("changes", [])
                
                for change in changes:
                    value = change.get("value", {})
                    
                    # Check if there are messages
                    messages = value.get("messages", [])
                    
                    if messages:
                        for message in messages:
                            print("\n📩 NEW MESSAGE DETECTED:")
                            print(f"   From: {message.get('from')}")
                            print(f"   Type: {message.get('type')}")
                            print(f"   Timestamp: {message.get('timestamp')}")
                            
                            # Check if it's an image
                            if message.get("type") == "image":
                                image_data = message.get("image", {})
                                print(f"   📸 IMAGE RECEIVED!")
                                print(f"      Image ID: {image_data.get('id')}")
                                print(f"      MIME type: {image_data.get('mime_type')}")
                                print(f"      Caption: {image_data.get('caption', 'No caption')}")
                                
                                # TODO: Download and process the image
                                # We'll add this in the next step
                            
                            elif message.get("type") == "text":
                                text_data = message.get("text", {})
                                incoming_text = text_data.get('body', '').strip().lower()
                                from_phone = message.get('from') # Meta sends format: 919876543210
                                
                                print(f"   💬 TEXT MESSAGE: {incoming_text}")
                                
                                # Check if the user is replying "yes" to verify
                                if incoming_text == "yes":
                                    print(f"   ✅ Verifying phone number for: {from_phone}")
                                    try:
                                        # Add the '+' back to match how Next.js saved it in the database
                                        db_phone = f"+{from_phone}"
                                        
                                        # Update the client's status in Supabase
                                        supabase_db.table("clients").update({"phone_verified": True}).eq("phone", db_phone).execute()
                                        print("   💾 Database updated! Client is verified.")
                                        
                                        # Send a quick confirmation back to the shop owner
                                        import asyncio
                                        confirmation_msg = "Awesome! Your number is verified. You can now send invoice photos here. 📸"
                                        asyncio.create_task(send_whatsapp_message(from_phone, confirmation_msg))
                                        
                                    except Exception as e:
                                        print(f"   ❌ Failed to update verification status: {e}")
        
        # Always return 200 OK to Meta (they require this)
        return {"status": "ok"}
    
    except Exception as e:
        print(f"❌ Error processing webhook: {str(e)}")
        # Still return 200 to Meta (don't want them to retry forever)
        return {"status": "error", "message": str(e)}


# ============================================
# SEND WHATSAPP MESSAGE (Helper Function)
# ============================================
async def send_whatsapp_message(to_phone: str, message_text: str):
    """
    Send a text message via WhatsApp Cloud API
    
    Args:
        to_phone: Phone number in format "919876543210" (no + sign)
        message_text: The message to send
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
        "text": {
            "body": message_text
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            
            print(f"✅ WhatsApp message sent to {to_phone}")
            return response.json()
    
    except Exception as e:
        print(f"❌ Failed to send WhatsApp: {str(e)}")
        raise


# ============================================
# API ENDPOINT: Send Welcome Message
# ============================================
@app.post("/api/send-welcome")
async def send_welcome_message(request: Request):
    """
    Called by Next.js frontend when CA adds a new client.
    Sends a welcome message to the client's WhatsApp.
    """
    try:
        data = await request.json()
        phone = data.get("phone")  # Format: "+919876543210"
        client_name = data.get("client_name", "there")
        
        # Remove + sign from phone (Meta API doesn't want it)
        clean_phone = phone.replace("+", "")
        
        message = f"""Hi {client_name}! 👋

You've been added to SmartBill AI by your CA.

From now on, you can send your invoice photos directly to this WhatsApp number, and we'll automatically extract the data for your accountant.

Just send a clear photo of any invoice, and we'll handle the rest!

Reply YES to confirm you received this message."""
        
        result = await send_whatsapp_message(clean_phone, message)
        
        return {
            "success": True,
            "message_id": result.get("messages", [{}])[0].get("id")
        }
    
    except Exception as e:
        print(f"❌ Error in send_welcome_message: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))