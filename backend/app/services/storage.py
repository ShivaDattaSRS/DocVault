from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from ..config import settings


def blob_file(sha256: str) -> Path:
    """Content-addressed path, sharded two levels deep to keep directories small."""
    return settings.blob_path / sha256[:2] / sha256[2:4] / sha256


def thumb_file(file_id: str) -> Path:
    return settings.thumb_path / f"{file_id}.jpg"


class UploadTooLarge(Exception):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"File exceeds the {limit // (1024 * 1024)} MB limit")


class QuotaExceeded(Exception):
    def __init__(self, needed: int, available: int) -> None:
        self.needed, self.available = needed, available
        super().__init__("Storage quota exceeded")


def stream_to_temp(chunks, max_bytes: int, head_size: int = 512) -> tuple[Path, str, int, bytes]:
    """Stream an upload to a temp file while hashing it.

    Returns (temp_path, sha256_hex, size, first_bytes). Raises UploadTooLarge.
    """
    digest = hashlib.sha256()
    size = 0
    head = b""
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=settings.storage_path, suffix=".part")
    try:
        with tmp:
            for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge(max_bytes)
                if len(head) < head_size:
                    head += chunk[: head_size - len(head)]
                digest.update(chunk)
                tmp.write(chunk)
        return Path(tmp.name), digest.hexdigest(), size, head
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise


def commit_blob(temp_path: Path, sha256: str) -> bool:
    """Move a staged temp file into the blob store. Returns False if it already existed."""
    dest = blob_file(sha256)
    if dest.exists():
        temp_path.unlink(missing_ok=True)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(temp_path, dest)
    except OSError:
        shutil.move(str(temp_path), str(dest))
    dest.chmod(0o640)
    return True


def delete_blob(sha256: str) -> None:
    path = blob_file(sha256)
    path.unlink(missing_ok=True)
    for parent in (path.parent, path.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            break


def blob_exists(sha256: str) -> bool:
    return blob_file(sha256).exists()


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
