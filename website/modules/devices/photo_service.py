"""Validation and timestamp normalization for device JPEG uploads."""

import io
from datetime import datetime, timezone
from PIL import Image, UnidentifiedImageError

MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTO_PIXELS = 40_000_000


def normalize_captured_at(value, now=None):
    """Return (UTC ISO timestamp, source) or (None, error code)."""
    now = now or datetime.now(timezone.utc)
    value = str(value or "").strip()
    if not value:
        return (now.isoformat(timespec="milliseconds").replace("+00:00", "Z"), "server"), None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid-captured-at"
    if parsed.tzinfo is None:
        return None, "invalid-captured-at"
    parsed = parsed.astimezone(timezone.utc)
    return (parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z"), "device"), None


def inspect_jpeg(payload):
    """Fully decode a JPEG and return dimensions without trusting headers alone."""
    if not payload:
        return None, "empty-photo"
    if len(payload) > MAX_PHOTO_BYTES:
        return None, "payload-too-large"
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "JPEG":
                return None, "invalid-jpeg"
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_PHOTO_PIXELS:
                return None, "invalid-image-dimensions"
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None, "invalid-jpeg"
    return {"width":width, "height":height, "size_bytes":len(payload)}, None
