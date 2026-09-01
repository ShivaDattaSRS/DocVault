from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# extension -> (canonical mime, mime types accepted from the browser, label, category)
ALLOWED_TYPES: dict[str, tuple[str, set[str], str, str]] = {
    ".pdf": ("application/pdf", {"application/pdf"}, "PDF document", "pdf"),
    ".png": ("image/png", {"image/png"}, "PNG image", "image"),
    ".jpg": ("image/jpeg", {"image/jpeg"}, "JPEG image", "image"),
    ".jpeg": ("image/jpeg", {"image/jpeg"}, "JPEG image", "image"),
    ".gif": ("image/gif", {"image/gif"}, "GIF image", "image"),
    ".webp": ("image/webp", {"image/webp"}, "WebP image", "image"),
    ".bmp": ("image/bmp", {"image/bmp", "image/x-ms-bmp"}, "Bitmap image", "image"),
    ".csv": (
        "text/csv",
        {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"},
        "CSV file",
        "csv",
    ),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        "Word document",
        "docx",
    ),
}

# Magic-number signatures, checked against the first bytes of the upload.
SIGNATURES: dict[str, list[bytes]] = {
    ".pdf": [b"%PDF-"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],
    ".bmp": [b"BM"],
    ".docx": [b"PK\x03\x04"],  # docx is a zip container
}

_UNSAFE = re.compile(r"[^A-Za-z0-9 ._\-()\[\]]+")
_RESERVED = {"con", "prn", "aux", "nul", "com1", "lpt1", ".", ".."}


class ValidationError(Exception):
    pass


def sanitize_filename(name: str, fallback: str = "file") -> str:
    """Strip directory components and anything that could escape or confuse the FS."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]          # kill path traversal
    name = name.replace("\x00", "").strip()
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = _UNSAFE.sub("_", stem).strip(" .") or fallback
    ext = _UNSAFE.sub("", ext).lower()
    if stem.lower() in _RESERVED:
        stem = f"{fallback}_{stem}"
    stem = stem[:180]
    return f"{stem}.{ext}" if ext else stem


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def category_for(ext: str) -> str:
    entry = ALLOWED_TYPES.get(ext)
    return entry[3] if entry else "other"


def validate_upload(filename: str, content_type: str, head: bytes) -> tuple[str, str]:
    """Validate extension, declared MIME type and magic bytes.

    Returns (extension, canonical_content_type) or raises ValidationError.
    """
    ext = extension_of(filename)
    if ext not in ALLOWED_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TYPES))
        raise ValidationError(f"File type '{ext or 'unknown'}' is not allowed. Allowed: {allowed}")

    canonical, mimes, label, _ = ALLOWED_TYPES[ext]
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared and declared not in mimes and declared != "application/octet-stream":
        raise ValidationError(f"Content type '{declared}' does not match a {label} ({ext}).")

    sigs = SIGNATURES.get(ext)
    if sigs and not any(head.startswith(sig) for sig in sigs):
        raise ValidationError(f"File contents do not look like a valid {label}.")

    if ext == ".webp" and not (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        raise ValidationError("File contents do not look like a valid WebP image.")

    if ext == ".csv":
        try:
            head.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                head.decode("latin-1")
            except UnicodeDecodeError:
                raise ValidationError("CSV file must be text-encoded (UTF-8 or Latin-1).") from None

    return ext, canonical
