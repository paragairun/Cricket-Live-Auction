"""Image upload: S3 when configured, local disk otherwise.

Version: 2.0.0 (2026-11-20)

Changed from 1.x: `upload_file` took raw bytes, was synchronous, returned None
when S3 was not configured (so the caller silently stored no photo), and did no
validation of type or size. Added `save_upload`, which takes a FastAPI
UploadFile, enforces a content-type allowlist and a size cap, and falls back to
local disk so photo upload works without AWS.
"""
import logging
import os
import uuid
from typing import Optional

from fastapi import HTTPException, UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


def upload_bytes(
    content: bytes,
    key_prefix: str = "profile",
    content_type: str = "image/jpeg",
) -> Optional[str]:
    """Push bytes to S3. Returns the public URL, or None when S3 is off."""
    if not settings.s3_enabled:
        return None

    try:
        import boto3

        ext = ALLOWED_CONTENT_TYPES.get(content_type, "jpg")
        key = f"{key_prefix}/{uuid.uuid4()}.{ext}"

        key_id = settings.AWS_ACCESS_KEY_ID or settings.AWS_MAIL_ACCESS_KEY_ID
        secret = settings.AWS_SECRET_ACCESS_KEY or settings.AWS_MAIL_SECRET_ACCESS_KEY
        bucket = settings.AWS_BUCKET_NAME

        s3 = boto3.client(
            "s3",
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name=settings.AWS_REGION,
        )
        s3.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)
        return f"https://{bucket}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"
    except Exception as exc:
        logger.exception("S3 upload failed: %s", exc)
        return None


def _save_local(content: bytes, folder: str, ext: str) -> str:
    directory = os.path.join(settings.UPLOAD_DIR, folder)
    os.makedirs(directory, exist_ok=True)
    filename = f"{uuid.uuid4()}.{ext}"
    with open(os.path.join(directory, filename), "wb") as handle:
        handle.write(content)
    return f"/uploads/{folder}/{filename}"


async def save_upload(file: UploadFile, folder: str = "profile") -> str:
    """Validate and store an uploaded image. Returns a URL the frontend can use."""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type '{content_type or 'unknown'}'. "
            "Use JPEG, PNG or WebP.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image is {len(content) // 1024}KB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1024}KB",
        )

    url = upload_bytes(content, key_prefix=folder, content_type=content_type)
    if url:
        return url
    return _save_local(content, folder, ALLOWED_CONTENT_TYPES[content_type])


# Backwards-compatible alias for call sites that still expect the old name.
upload_file = upload_bytes
