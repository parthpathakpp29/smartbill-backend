"""
SmartBill AI — WhatsApp Service

Handles all communication with the Meta/WhatsApp Business API:
  - Sending text messages
  - Downloading media (images) from WhatsApp
"""

import httpx
from app.config import (
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_BASE_URL,
)


def _get_headers() -> dict:
    """Standard headers for all Meta API calls."""
    return {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


async def send_whatsapp_message(to_phone: str, message_text: str) -> dict:
    """
    Send a text message via WhatsApp Business API.

    Args:
        to_phone: Digits only, NO + sign (e.g. "919876543210")
        message_text: The message body

    Returns:
        Meta API response dict

    Raises:
        Exception if API returns non-200
    """
    url = f"{WHATSAPP_BASE_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message_text},
    }

    print(f"\n📤 Sending WhatsApp to: {to_phone}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=_get_headers(), json=payload)

        print(f"   Status: {response.status_code}")

        if response.status_code != 200:
            error_body = response.text
            print(f"   ❌ Error: {error_body}")
            raise Exception(f"WhatsApp API error: {error_body}")

        print(f"   ✅ Message sent!")
        return response.json()


async def download_media(media_id: str) -> tuple[bytes, str]:
    """
    Download media from WhatsApp in 2 steps:
      1. GET /{media_id} → get the temporary download URL
      2. GET {download_url} → download the raw bytes

    Args:
        media_id: The WhatsApp media ID from the webhook payload

    Returns:
        Tuple of (image_bytes, mime_type)

    Raises:
        Exception if download fails at any step
    """
    headers = _get_headers()

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Step 1: Get the temporary download URL from Meta
        meta_url = f"{WHATSAPP_BASE_URL}/{media_id}"
        print(f"\n📥 Fetching media URL for: {media_id}")

        response = await client.get(meta_url, headers=headers)

        if response.status_code != 200:
            raise Exception(f"Failed to get media URL: {response.text}")

        media_info = response.json()
        download_url = media_info.get("url")
        mime_type = media_info.get("mime_type", "image/jpeg")

        if not download_url:
            raise Exception(f"No download URL in media response: {media_info}")

        print(f"   📎 Download URL obtained (mime: {mime_type})")

        # Step 2: Download the actual image bytes
        # Meta requires the Authorization header for the download URL too
        download_headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        img_response = await client.get(download_url, headers=download_headers)

        if img_response.status_code != 200:
            raise Exception(f"Failed to download media: status {img_response.status_code}")

        image_bytes = img_response.content
        print(f"   ✅ Downloaded {len(image_bytes)} bytes")

        return image_bytes, mime_type
