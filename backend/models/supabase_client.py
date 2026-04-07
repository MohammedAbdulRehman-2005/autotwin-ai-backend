"""
models/supabase_client.py
──────────────────────────
AutoTwin AI — Supabase client singleton.

Provides:
  • Singleton Supabase client (uses service_role key for server-side ops)
  • upload_invoice_file()   → upload bytes to the 'invoices' Storage bucket
  • get_invoice_file_url()  → return a signed URL for a stored file
  • get_supabase_client()   → returns the raw client for advanced usage
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Optional

from core.config import settings

logger = logging.getLogger("autotwin_ai.supabase_client")

# ──────────────────────────────────────────────────────────────
# Singleton client
# ──────────────────────────────────────────────────────────────
_supabase_client = None
_storage_available = False

if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY:
    try:
        from supabase import create_client, Client  # type: ignore

        _supabase_client: Optional[Client] = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY,  # service role bypasses RLS
        )
        _storage_available = True
        logger.info("Supabase client initialised → %s", settings.SUPABASE_URL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase client init failed (%s) — Storage disabled.", exc)
else:
    logger.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — Storage disabled.")


def get_supabase_client():
    """Return the raw Supabase client, or None if unavailable."""
    return _supabase_client


def is_storage_available() -> bool:
    """Returns True when the Supabase Storage client is ready."""
    return _storage_available


# ──────────────────────────────────────────────────────────────
# Bucket bootstrap — called at startup
# ──────────────────────────────────────────────────────────────

async def ensure_bucket_exists() -> None:
    """
    Create the storage bucket if it doesn't already exist.
    Safe to call multiple times (idempotent).
    """
    if not _storage_available or _supabase_client is None:
        return
    bucket_name = settings.SUPABASE_STORAGE_BUCKET
    try:
        existing = _supabase_client.storage.list_buckets()
        bucket_names = [b.name for b in existing]
        if bucket_name not in bucket_names:
            _supabase_client.storage.create_bucket(bucket_name, options={"public": False})
            logger.info("Storage bucket '%s' created.", bucket_name)
        else:
            logger.debug("Storage bucket '%s' already exists.", bucket_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not ensure bucket '%s' exists: %s", bucket_name, exc)


# ──────────────────────────────────────────────────────────────
# File upload
# ──────────────────────────────────────────────────────────────

async def upload_invoice_file(
    invoice_id: str,
    filename: str,
    file_bytes: bytes,
) -> Optional[str]:
    """
    Upload *file_bytes* to Supabase Storage.

    Storage path: invoices/{invoice_id}/{filename}

    Returns:
        Signed URL (valid 1 hour) on success, None on failure.
    """
    if not _storage_available or _supabase_client is None:
        logger.debug("Storage unavailable — skipping file upload for %s", invoice_id)
        return None

    bucket = settings.SUPABASE_STORAGE_BUCKET
    object_path = f"{invoice_id}/{filename}"
    content_type, _ = mimetypes.guess_type(filename)
    content_type = content_type or "application/octet-stream"

    try:
        _supabase_client.storage.from_(bucket).upload(
            path=object_path,
            file=file_bytes,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        logger.info("File uploaded: %s/%s", bucket, object_path)

        # Generate a signed URL valid for 1 hour (3600 seconds)
        signed = _supabase_client.storage.from_(bucket).create_signed_url(
            path=object_path,
            expires_in=3600,
        )
        url: Optional[str] = signed.get("signedURL") or signed.get("signedUrl")
        logger.debug("Signed URL generated for %s: %s", object_path, url)
        return url
    except Exception as exc:  # noqa: BLE001
        logger.warning("File upload failed for %s: %s", object_path, exc)
        return None


# ──────────────────────────────────────────────────────────────
# Signed URL for existing file
# ──────────────────────────────────────────────────────────────

def get_invoice_file_url(invoice_id: str, filename: str, expires_in: int = 3600) -> Optional[str]:
    """
    Return a fresh signed URL for an already-uploaded file.

    Args:
        invoice_id:  Invoice ID (used as folder prefix).
        filename:    Original filename.
        expires_in:  Seconds until expiry (default 1 hour).

    Returns:
        Signed URL string, or None if unavailable.
    """
    if not _storage_available or _supabase_client is None:
        return None

    bucket = settings.SUPABASE_STORAGE_BUCKET
    object_path = f"{invoice_id}/{filename}"
    try:
        signed = _supabase_client.storage.from_(bucket).create_signed_url(
            path=object_path,
            expires_in=expires_in,
        )
        return signed.get("signedURL") or signed.get("signedUrl")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not generate signed URL for %s: %s", object_path, exc)
        return None
