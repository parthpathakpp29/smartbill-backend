"""
SmartBill AI — Supabase Storage Service

Handles uploading invoice images to Supabase Storage
and generating permanent public URLs for the CA dashboard.
"""

import uuid
from datetime import datetime
from app.config import supabase, SUPABASE_URL, INVOICE_IMAGES_BUCKET


def _get_mime_extension(mime_type: str) -> str:
    """Map MIME type to file extension."""
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/heic": "heic",
        "image/heif": "heif",
    }
    return mapping.get(mime_type.lower(), "jpg")


async def upload_invoice_image(
    image_bytes: bytes,
    client_id: str,
    mime_type: str = "image/jpeg",
) -> str:
    """
    Upload an invoice image to Supabase Storage.

    File path structure: {client_id}/{date}_{uuid}.{ext}
    This keeps each client's invoices organized in folders.

    Args:
        image_bytes: Raw image bytes
        client_id: The client's UUID (used as folder name)
        mime_type: MIME type of the image

    Returns:
        Public URL of the uploaded image

    Raises:
        Exception if upload fails
    """
    ext = _get_mime_extension(mime_type)
    date_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    file_path = f"{client_id}/{date_str}_{unique_id}.{ext}"

    print(f"\n☁️  Uploading to Storage: {INVOICE_IMAGES_BUCKET}/{file_path}")

    # Upload to Supabase Storage
    result = supabase.storage.from_(INVOICE_IMAGES_BUCKET).upload(
        path=file_path,
        file=image_bytes,
        file_options={
            "content-type": mime_type,
            "upsert": "true",
        },
    )

    # Build the public URL
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{INVOICE_IMAGES_BUCKET}/{file_path}"

    print(f"   ✅ Uploaded! URL: {public_url}")

    return public_url
