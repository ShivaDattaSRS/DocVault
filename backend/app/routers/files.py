from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, log_activity, optional_user
from ..models import Blob, FileRecord, FileStatus, Folder, User, Visibility
from ..schemas import (
    DownloadLinkResponse,
    FileListResponse,
    FileMove,
    FileOut,
    FileRename,
    MessageResponse,
    StorageStats,
    UploadResponse,
    VisibilityUpdate,
)
from ..security import create_download_token, decode_download_token, decode_token, new_share_token
from ..services import storage
from ..services.jobs import enqueue_processing
from ..services.validation import ValidationError, category_for, sanitize_filename, validate_upload

router = APIRouter(prefix="/api/files", tags=["files"])

CHUNK = 1024 * 1024


def _out(record: FileRecord, owner_email: str | None = None) -> FileOut:
    data = FileOut.model_validate(record)
    data.owner_email = owner_email or (record.owner.email if record.owner else None)
    return data


def _get_owned(db: Session, file_id: uuid.UUID, user: User) -> FileRecord:
    record = db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if record.owner_id != user.id and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this file")
    return record


def _release_blob(db: Session, sha256: str) -> None:
    """Drop one reference; delete the physical blob when nothing points at it."""
    blob = db.get(Blob, sha256, with_for_update=True)
    if blob is None:
        return
    blob.ref_count = max(0, blob.ref_count - 1)
    if blob.ref_count == 0:
        db.delete(blob)
        storage.delete_blob(sha256)


# --------------------------------------------------------------- upload
@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def upload_file(
    request: Request,
    upload: UploadFile = File(...),
    folder_id: str | None = Form(None),
    visibility: str = Form("private"),
    allow_duplicate: bool = Form(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    original_name = upload.filename or "unnamed"
    safe_name = sanitize_filename(original_name)

    # Folder must belong to the caller.
    folder: Folder | None = None
    if folder_id:
        try:
            folder = db.get(Folder, uuid.UUID(folder_id))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid folder id") from None
        if folder is None or folder.owner_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")

    remaining = user.quota_bytes - user.used_bytes
    if remaining <= 0:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Storage quota exhausted ({storage.human_size(user.quota_bytes)} used).",
        )

    # Reject oversized uploads before spooling the whole body, when possible.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_file_size + 4096:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {settings.max_file_size_mb} MB per-file limit.",
        )

    limit = min(settings.max_file_size, remaining)
    chunks = iter(lambda: upload.file.read(CHUNK), b"")
    try:
        temp_path, sha256, size, head = storage.stream_to_temp(chunks, limit)
    except storage.UploadTooLarge:
        if remaining < settings.max_file_size:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Not enough storage left. You have {storage.human_size(remaining)} available.",
            ) from None
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {settings.max_file_size_mb} MB per-file limit.",
        ) from None
    finally:
        upload.file.close()

    if size == 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty")

    try:
        ext, content_type = validate_upload(safe_name, upload.content_type or "", head)
    except ValidationError as exc:
        temp_path.unlink(missing_ok=True)
        log_activity(db, "upload_rejected", request=request, user=user, detail=f"{original_name}: {exc}")
        db.commit()
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None

    # ---- duplicate detection (same owner, identical content) ----
    twin = db.scalar(
        select(FileRecord).where(FileRecord.owner_id == user.id, FileRecord.sha256 == sha256).limit(1)
    )
    if twin is not None and not allow_duplicate:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": f"You already uploaded this file as '{twin.filename}'.",
                "duplicate_of": str(twin.id),
                "filename": twin.filename,
                "uploaded_at": twin.created_at.isoformat(),
            },
        )

    # ---- commit blob (content-addressed, shared across duplicates) ----
    stored_new = storage.commit_blob(temp_path, sha256)
    blob = db.get(Blob, sha256, with_for_update=True)
    if blob is None:
        blob = Blob(sha256=sha256, size_bytes=size, ref_count=0)
        db.add(blob)
        db.flush()
    elif not stored_new and not storage.blob_exists(sha256):
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Storage inconsistency; upload again")
    blob.ref_count += 1

    # Keep filenames unique within a folder.
    final_name = safe_name
    counter = 1
    while db.scalar(
        select(func.count())
        .select_from(FileRecord)
        .where(
            FileRecord.owner_id == user.id,
            FileRecord.folder_id == (folder.id if folder else None),
            FileRecord.filename == final_name,
        )
    ):
        stem = Path(safe_name).stem
        final_name = f"{stem} ({counter}){ext}"
        counter += 1

    record = FileRecord(
        owner_id=user.id,
        folder_id=folder.id if folder else None,
        sha256=sha256,
        filename=final_name,
        original_filename=original_name[:255],
        extension=ext,
        content_type=content_type,
        size_bytes=size,
        status=FileStatus.processing,
        visibility=Visibility.public if visibility == "public" else Visibility.private,
        share_token=new_share_token() if visibility == "public" else None,
        is_duplicate=twin is not None,
        extra={"kind": category_for(ext)},
    )
    db.add(record)
    user.used_bytes += size
    db.flush()  # assigns record.id so the log entry can reference it
    log_activity(
        db,
        "upload",
        request=request,
        user=user,
        file_id=record.id,
        detail=f"{final_name} ({storage.human_size(size)})"
        + (" — duplicate content" if twin is not None else ""),
    )
    db.commit()
    db.refresh(record)

    backend = enqueue_processing(str(record.id))
    return UploadResponse(
        file=_out(record, user.email),
        duplicate_of=twin.id if twin else None,
        message=f"Uploaded. Background processing queued via {backend}.",
    )


# ----------------------------------------------------------------- list
@router.get("", response_model=FileListResponse)
def list_files(
    q: str | None = Query(None, description="Search filename"),
    folder_id: str | None = Query(None),
    kind: str | None = Query(None, description="image | pdf | csv | docx"),
    visibility: Visibility | None = Query(None),
    status_filter: FileStatus | None = Query(None, alias="status"),
    sort: str = Query("newest", pattern="^(newest|oldest|name|size|largest)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(FileRecord).where(FileRecord.owner_id == user.id)

    if q:
        stmt = stmt.where(FileRecord.filename.ilike(f"%{q.strip()}%"))
    if folder_id == "root":
        stmt = stmt.where(FileRecord.folder_id.is_(None))
    elif folder_id:
        try:
            stmt = stmt.where(FileRecord.folder_id == uuid.UUID(folder_id))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid folder id") from None
    if kind:
        exts = [e for e, meta in _EXT_BY_KIND.items() if meta == kind]
        stmt = stmt.where(FileRecord.extension.in_(exts))
    if visibility:
        stmt = stmt.where(FileRecord.visibility == visibility)
    if status_filter:
        stmt = stmt.where(FileRecord.status == status_filter)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    order = {
        "newest": FileRecord.created_at.desc(),
        "oldest": FileRecord.created_at.asc(),
        "name": FileRecord.filename.asc(),
        "size": FileRecord.size_bytes.asc(),
        "largest": FileRecord.size_bytes.desc(),
    }[sort]
    rows = db.scalars(stmt.order_by(order).offset((page - 1) * page_size).limit(page_size)).all()

    return FileListResponse(
        items=[_out(r, user.email) for r in rows], total=total, page=page, page_size=page_size
    )


_EXT_BY_KIND = {ext: category_for(ext) for ext in (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".csv", ".docx")}


@router.get("/stats", response_model=StorageStats)
def storage_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(FileRecord.extension, func.count(), func.coalesce(func.sum(FileRecord.size_bytes), 0))
        .where(FileRecord.owner_id == user.id)
        .group_by(FileRecord.extension)
    ).all()
    by_type: dict[str, int] = {}
    count = 0
    for ext, n, _ in rows:
        by_type[category_for(ext)] = by_type.get(category_for(ext), 0) + n
        count += n
    return StorageStats(
        used_bytes=user.used_bytes, quota_bytes=user.quota_bytes, file_count=count, by_type=by_type
    )


@router.get("/{file_id}", response_model=FileOut)
def get_file(file_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    record = _get_owned(db, file_id, user)
    return _out(record)


# -------------------------------------------------------------- mutate
@router.patch("/{file_id}/rename", response_model=FileOut)
def rename_file(
    file_id: uuid.UUID,
    payload: FileRename,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _get_owned(db, file_id, user)
    safe = sanitize_filename(payload.filename)
    if not Path(safe).suffix:
        safe = f"{safe}{record.extension}"
    if Path(safe).suffix.lower() != record.extension:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Extension must stay '{record.extension}'")

    clash = db.scalar(
        select(FileRecord).where(
            FileRecord.owner_id == record.owner_id,
            FileRecord.folder_id == record.folder_id,
            FileRecord.filename == safe,
            FileRecord.id != record.id,
        )
    )
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "A file with that name already exists here")

    old = record.filename
    record.filename = safe
    log_activity(db, "rename", request=request, user=user, file_id=record.id, detail=f"{old} → {safe}")
    db.commit()
    db.refresh(record)
    return _out(record)


@router.patch("/{file_id}/move", response_model=FileOut)
def move_file(
    file_id: uuid.UUID,
    payload: FileMove,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _get_owned(db, file_id, user)
    if payload.folder_id is not None:
        folder = db.get(Folder, payload.folder_id)
        if folder is None or folder.owner_id != record.owner_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    record.folder_id = payload.folder_id
    log_activity(db, "move", request=request, user=user, file_id=record.id, detail=str(payload.folder_id))
    db.commit()
    db.refresh(record)
    return _out(record)


@router.patch("/{file_id}/visibility", response_model=FileOut)
def set_visibility(
    file_id: uuid.UUID,
    payload: VisibilityUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _get_owned(db, file_id, user)
    record.visibility = payload.visibility
    if payload.visibility == Visibility.public and not record.share_token:
        record.share_token = new_share_token()
    elif payload.visibility == Visibility.private:
        record.share_token = None  # revokes any circulating public link
    log_activity(
        db, "visibility", request=request, user=user, file_id=record.id, detail=payload.visibility.value
    )
    db.commit()
    db.refresh(record)
    return _out(record)


@router.post("/{file_id}/reprocess", response_model=FileOut)
def reprocess(
    file_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _get_owned(db, file_id, user)
    record.status = FileStatus.processing
    record.status_detail = ""
    log_activity(db, "reprocess", request=request, user=user, file_id=record.id)
    db.commit()
    db.refresh(record)
    enqueue_processing(str(record.id))
    return _out(record)


@router.delete("/{file_id}", response_model=MessageResponse)
def delete_file(
    file_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _get_owned(db, file_id, user)
    owner = db.get(User, record.owner_id)
    name, size, sha = record.filename, record.size_bytes, record.sha256

    if record.thumbnail_path:
        (settings.thumb_path / record.thumbnail_path).unlink(missing_ok=True)

    db.delete(record)
    db.flush()
    _release_blob(db, sha)
    if owner:
        owner.used_bytes = max(0, owner.used_bytes - size)
    log_activity(db, "delete", request=request, user=user, detail=f"{name} ({storage.human_size(size)})")
    db.commit()
    return MessageResponse(message=f"Deleted '{name}'")


# ------------------------------------------------------------ download
@router.post("/{file_id}/download-url", response_model=DownloadLinkResponse)
def create_download_url(
    file_id: uuid.UUID,
    expires_in: int = Query(300, ge=30, le=86400),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _get_owned(db, file_id, user)
    token = create_download_token(str(record.id), str(user.id), expires_in)
    return DownloadLinkResponse(url=f"/api/files/{record.id}/download?token={token}", expires_in=expires_in)


def _authorize_download(db: Session, record: FileRecord, token: str | None, user: User | None) -> bool:
    """Public files, the owner/admin (header or ?token=), or a signed download link."""
    if record.visibility == Visibility.public:
        return True
    if user and (user.id == record.owner_id or user.is_admin):
        return True
    if not token:
        return False

    payload = decode_download_token(token)
    if payload and payload.get("sub") == str(record.id):
        return True

    # <img src> and plain browser navigations cannot send an Authorization
    # header, so an access token may also travel in the query string.
    payload = decode_token(token)
    if payload and payload.get("typ") == "access":
        try:
            holder = db.get(User, uuid.UUID(payload["sub"]))
        except (KeyError, ValueError):
            return False
        if holder and holder.is_active and (holder.id == record.owner_id or holder.is_admin):
            return True
    return False


@router.get("/{file_id}/download")
def download_file(
    file_id: uuid.UUID,
    request: Request,
    token: str | None = Query(None),
    disposition: str = Query("attachment", pattern="^(attachment|inline)$"),
    user: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    record = db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if not _authorize_download(db, record, token, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this file")

    path = storage.blob_file(record.sha256)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Stored file is missing")

    record.download_count += 1
    log_activity(db, "download", request=request, user=user, file_id=record.id, detail=record.filename)
    db.commit()

    return FileResponse(
        path,
        media_type=record.content_type,
        filename=record.filename,
        content_disposition_type=disposition,
    )


@router.get("/{file_id}/thumbnail")
def get_thumbnail(
    file_id: uuid.UUID,
    token: str | None = Query(None),
    user: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    record = db.get(FileRecord, file_id)
    if record is None or not record.thumbnail_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No thumbnail for this file")
    if not _authorize_download(db, record, token, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this file")

    path = settings.thumb_path / record.thumbnail_path
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thumbnail not generated")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.head("/{file_id}/download")
def head_download(file_id: uuid.UUID, db: Session = Depends(get_db)):
    record = db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return Response(headers={"Content-Length": str(record.size_bytes)})
