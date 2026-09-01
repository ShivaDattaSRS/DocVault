from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_admin, log_activity
from ..models import ActivityLog, Blob, FileRecord, FileStatus, User
from ..schemas import (
    AdminStats,
    FileListResponse,
    FileOut,
    LogOut,
    MessageResponse,
    QuotaUpdate,
    UserOut,
    UserStatusUpdate,
)
from ..services.jobs import backend_name, queue_depth
from ..services.validation import category_for

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])


@router.get("/stats", response_model=AdminStats)
def stats(db: Session = Depends(get_db)):
    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    active_users = db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    total_files = db.scalar(select(func.count()).select_from(FileRecord)) or 0
    total_bytes = db.scalar(select(func.coalesce(func.sum(FileRecord.size_bytes), 0))) or 0
    unique_bytes = db.scalar(select(func.coalesce(func.sum(Blob.size_bytes), 0))) or 0
    failures = (
        db.scalar(select(func.count()).select_from(FileRecord).where(FileRecord.status == FileStatus.failed))
        or 0
    )

    by_ext = db.execute(
        select(FileRecord.extension, func.count()).group_by(FileRecord.extension)
    ).all()
    files_by_type: dict[str, int] = {}
    for ext, count in by_ext:
        files_by_type[category_for(ext)] = files_by_type.get(category_for(ext), 0) + count

    since = datetime.now(timezone.utc) - timedelta(days=6)
    daily = db.execute(
        select(func.date(FileRecord.created_at), func.count(), func.coalesce(func.sum(FileRecord.size_bytes), 0))
        .where(FileRecord.created_at >= since)
        .group_by(func.date(FileRecord.created_at))
        .order_by(func.date(FileRecord.created_at))
    ).all()
    buckets = {str(day): {"count": count, "bytes": int(size)} for day, count, size in daily}

    series = []
    for offset in range(6, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=offset)).date()
        entry = buckets.get(str(day), {"count": 0, "bytes": 0})
        series.append({"date": str(day), **entry})

    return AdminStats(
        total_users=total_users,
        active_users=active_users,
        total_files=total_files,
        total_bytes=int(total_bytes),
        unique_bytes=int(unique_bytes),
        bytes_saved_by_dedup=max(0, int(total_bytes) - int(unique_bytes)),
        files_by_type=files_by_type,
        uploads_last_7_days=series,
        processing_failures=failures,
    )


@router.get("/queue")
def queue_status():
    return {"backend": backend_name(), "depth": queue_depth()}


@router.get("/users", response_model=list[UserOut])
def list_users(
    q: str | None = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(User).order_by(User.created_at.desc())
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q.strip()}%") | User.full_name.ilike(f"%{q.strip()}%"))
    return [UserOut.model_validate(u) for u in db.scalars(stmt.limit(200)).all()]


@router.patch("/users/{user_id}/quota", response_model=UserOut)
def update_quota(
    user_id: uuid.UUID,
    payload: QuotaUpdate,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    target.quota_bytes = payload.quota_mb * 1024 * 1024
    log_activity(
        db, "admin_quota", request=request, user=admin, detail=f"{target.email} → {payload.quota_mb} MB"
    )
    db.commit()
    db.refresh(target)
    return UserOut.model_validate(target)


@router.patch("/users/{user_id}/status", response_model=UserOut)
def update_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target.id == admin.id and not payload.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")
    target.is_active = payload.is_active
    log_activity(
        db,
        "admin_status",
        request=request,
        user=admin,
        detail=f"{target.email} → {'active' if payload.is_active else 'deactivated'}",
    )
    db.commit()
    db.refresh(target)
    return UserOut.model_validate(target)


@router.get("/files", response_model=FileListResponse)
def all_files(
    q: str | None = Query(None),
    owner: str | None = Query(None),
    status_filter: FileStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(FileRecord, User.email).join(User, FileRecord.owner_id == User.id)
    if q:
        stmt = stmt.where(FileRecord.filename.ilike(f"%{q.strip()}%"))
    if owner:
        stmt = stmt.where(User.email.ilike(f"%{owner.strip()}%"))
    if status_filter:
        stmt = stmt.where(FileRecord.status == status_filter)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(FileRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()

    items = []
    for record, email in rows:
        out = FileOut.model_validate(record)
        out.owner_email = email
        items.append(out)
    return FileListResponse(items=items, total=total, page=page, page_size=page_size)


@router.delete("/files/{file_id}", response_model=MessageResponse)
def admin_delete_file(
    file_id: uuid.UUID,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    from .files import _release_blob

    record = db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    owner = db.get(User, record.owner_id)
    name, size, sha = record.filename, record.size_bytes, record.sha256
    db.delete(record)
    db.flush()
    _release_blob(db, sha)
    if owner:
        owner.used_bytes = max(0, owner.used_bytes - size)
    log_activity(db, "admin_delete", request=request, user=admin, detail=f"{name} (owner {owner.email if owner else '?'})")
    db.commit()
    return MessageResponse(message=f"Deleted '{name}'")


@router.get("/logs", response_model=list[LogOut])
def logs(
    action: str | None = Query(None),
    user_email: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = (
        select(ActivityLog, User.email)
        .join(User, ActivityLog.user_id == User.id, isouter=True)
        .order_by(ActivityLog.created_at.desc())
    )
    if action:
        stmt = stmt.where(ActivityLog.action == action)
    if user_email:
        stmt = stmt.where(User.email.ilike(f"%{user_email.strip()}%"))

    rows = db.execute(stmt.limit(limit)).all()
    out = []
    for log, email in rows:
        item = LogOut.model_validate(log)
        item.user_email = email
        out.append(item)
    return out
