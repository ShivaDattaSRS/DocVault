from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, log_activity
from ..models import FileRecord, Folder, User
from ..schemas import FolderCreate, FolderOut, FolderRename, MessageResponse
from ..services.validation import sanitize_filename

router = APIRouter(prefix="/api/folders", tags=["folders"])

MAX_DEPTH = 5


def _depth(db: Session, folder_id: uuid.UUID | None) -> int:
    depth = 0
    current = folder_id
    while current is not None and depth <= MAX_DEPTH + 1:
        folder = db.get(Folder, current)
        if folder is None:
            break
        current = folder.parent_id
        depth += 1
    return depth


def _counts(db: Session, owner_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = db.execute(
        select(FileRecord.folder_id, func.count())
        .where(FileRecord.owner_id == owner_id, FileRecord.folder_id.is_not(None))
        .group_by(FileRecord.folder_id)
    ).all()
    return {fid: n for fid, n in rows}


@router.get("", response_model=list[FolderOut])
def list_folders(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    folders = db.scalars(
        select(Folder).where(Folder.owner_id == user.id).order_by(Folder.name)
    ).all()
    counts = _counts(db, user.id)
    result = []
    for folder in folders:
        out = FolderOut.model_validate(folder)
        out.file_count = counts.get(folder.id, 0)
        result.append(out)
    return result


@router.post("", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
def create_folder(
    payload: FolderCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = sanitize_filename(payload.name, fallback="folder")[:120]
    if payload.parent_id is not None:
        parent = db.get(Folder, payload.parent_id)
        if parent is None or parent.owner_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent folder not found")
        if _depth(db, parent.id) >= MAX_DEPTH:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Folders can nest at most {MAX_DEPTH} deep")

    exists = db.scalar(
        select(Folder).where(
            Folder.owner_id == user.id, Folder.parent_id == payload.parent_id, Folder.name == name
        )
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "A folder with that name already exists here")

    folder = Folder(owner_id=user.id, parent_id=payload.parent_id, name=name)
    db.add(folder)
    log_activity(db, "folder_create", request=request, user=user, detail=name)
    db.commit()
    db.refresh(folder)
    return FolderOut.model_validate(folder)


@router.patch("/{folder_id}", response_model=FolderOut)
def rename_folder(
    folder_id: uuid.UUID,
    payload: FolderRename,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = db.get(Folder, folder_id)
    if folder is None or folder.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    name = sanitize_filename(payload.name, fallback="folder")[:120]
    clash = db.scalar(
        select(Folder).where(
            Folder.owner_id == user.id,
            Folder.parent_id == folder.parent_id,
            Folder.name == name,
            Folder.id != folder.id,
        )
    )
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "A folder with that name already exists here")
    old, folder.name = folder.name, name
    log_activity(db, "folder_rename", request=request, user=user, detail=f"{old} → {name}")
    db.commit()
    db.refresh(folder)
    out = FolderOut.model_validate(folder)
    out.file_count = _counts(db, user.id).get(folder.id, 0)
    return out


@router.delete("/{folder_id}", response_model=MessageResponse)
def delete_folder(
    folder_id: uuid.UUID,
    request: Request,
    cascade: bool = Query(False, description="Delete contained files too (otherwise they move to root)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from .files import _release_blob  # local import avoids a circular module reference

    folder = db.get(Folder, folder_id)
    if folder is None or folder.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")

    files = db.scalars(select(FileRecord).where(FileRecord.folder_id == folder.id)).all()
    if cascade:
        freed = 0
        for record in files:
            freed += record.size_bytes
            sha = record.sha256
            db.delete(record)
            db.flush()
            _release_blob(db, sha)
        user.used_bytes = max(0, user.used_bytes - freed)
        detail = f"{folder.name} (+{len(files)} files)"
    else:
        for record in files:
            record.folder_id = None
        detail = f"{folder.name} ({len(files)} files moved to root)"

    db.delete(folder)
    log_activity(db, "folder_delete", request=request, user=user, detail=detail)
    db.commit()
    return MessageResponse(message=f"Deleted folder '{folder.name}'")
