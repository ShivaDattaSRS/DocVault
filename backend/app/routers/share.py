from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import log_activity
from ..models import FileRecord, User, Visibility
from ..services import storage

router = APIRouter(prefix="/api/share", tags=["share"])


def _lookup(db: Session, token: str) -> FileRecord:
    record = db.scalar(select(FileRecord).where(FileRecord.share_token == token))
    if record is None or record.visibility != Visibility.public:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link is invalid or has been revoked")
    return record


@router.get("/{token}")
def share_info(token: str, db: Session = Depends(get_db)):
    record = _lookup(db, token)
    owner = db.get(User, record.owner_id)
    return {
        "filename": record.filename,
        "size_bytes": record.size_bytes,
        "size_human": storage.human_size(record.size_bytes),
        "content_type": record.content_type,
        "extension": record.extension,
        "status": record.status.value,
        "uploaded_by": owner.full_name or owner.email if owner else "Unknown",
        "uploaded_at": record.created_at.isoformat(),
        "download_count": record.download_count,
        "has_thumbnail": bool(record.thumbnail_path),
        "extra": record.extra,
    }


@router.get("/{token}/thumbnail")
def share_thumbnail(token: str, db: Session = Depends(get_db)):
    record = _lookup(db, token)
    if not record.thumbnail_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No thumbnail")
    path = settings.thumb_path / record.thumbnail_path
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No thumbnail")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{token}/download")
def share_download(token: str, request: Request, db: Session = Depends(get_db)):
    record = _lookup(db, token)
    path = storage.blob_file(record.sha256)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Stored file is missing")

    record.download_count += 1
    log_activity(db, "public_download", request=request, file_id=record.id, detail=record.filename)
    db.commit()
    return FileResponse(path, media_type=record.content_type, filename=record.filename)
