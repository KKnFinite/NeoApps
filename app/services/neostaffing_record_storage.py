"""Private signature object boundary. No local disk or database fallback."""
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

from flask import current_app
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 150_000


def backend():
    # Deployment injects a private durable adapter; never reuse SPEAR's bucket.
    adapter = current_app.config.get("EMPLOYEE_RECORD_SIGNATURE_STORAGE")
    if adapter is None:
        raise ValueError("Signature storage is not configured. The draft is unchanged; RTS is available only if the employee refuses to sign.")
    return adapter


def store(record_id, raw):
    adapter = backend()
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Signature is missing or too large.")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG" or image.width > 1600 or image.height > 600 or image.width < 2 or image.height < 2:
                raise ValueError("Use the signature pad to provide a valid signature.")
            image.load()
            # Flatten/re-encode to strip metadata, ancillary payloads and trailing data.
            normalized = image.convert("RGB")
            if all(low == high for low, high in normalized.getextrema()):
                raise ValueError("Draw initials or a signature before acknowledging.")
            buffer = BytesIO()
            normalized.save(buffer, format="PNG")
            raw = buffer.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Invalid signature image.") from None
    if len(raw) > MAX_BYTES:
        raise ValueError("Signature is too large.")
    key = f"employee-records/{record_id}/{uuid4()}.png"
    checksum = sha256(raw).hexdigest()
    try:
        # Must durably put-if-absent, private, before returning. Never log payloads.
        adapter.put(key, raw, content_type="image/png", checksum=checksum)
    except Exception:
        raise ValueError("Signature storage is unavailable. The record was not finalized.") from None
    return key, checksum, len(raw)


def read(record):
    try:
        raw = backend().get(record.signature_key, max_bytes=MAX_BYTES)
        if len(raw) > MAX_BYTES or sha256(raw).hexdigest() != record.signature_sha256:
            raise ValueError()
        return raw
    except Exception:
        raise ValueError("Signature is currently unavailable.") from None
