from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import FileStatus, Visibility


# ------------------------------------------------------------------ auth
class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=10)


class OTPResendRequest(BaseModel):
    email: EmailStr


class MessageResponse(BaseModel):
    message: str
    dev_otp: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    is_admin: bool
    is_active: bool
    quota_bytes: int
    used_bytes: int
    created_at: datetime


# ---------------------------------------------------------------- folders
class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None


class FolderRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    created_at: datetime
    file_count: int = 0


# ------------------------------------------------------------------ files
class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    status: FileStatus
    status_detail: str
    visibility: Visibility
    share_token: str | None
    folder_id: uuid.UUID | None
    thumbnail_path: str | None
    extra: dict | None
    is_duplicate: bool
    download_count: int
    created_at: datetime
    updated_at: datetime
    owner_email: str | None = None


class FileListResponse(BaseModel):
    items: list[FileOut]
    total: int
    page: int
    page_size: int


class FileRename(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class FileMove(BaseModel):
    folder_id: uuid.UUID | None = None


class VisibilityUpdate(BaseModel):
    visibility: Visibility


class DownloadLinkResponse(BaseModel):
    url: str
    expires_in: int


class UploadResponse(BaseModel):
    file: FileOut
    duplicate_of: uuid.UUID | None = None
    message: str


# ------------------------------------------------------------------ admin
class QuotaUpdate(BaseModel):
    quota_mb: int = Field(ge=1, le=1024 * 100)


class UserStatusUpdate(BaseModel):
    is_active: bool


class AdminStats(BaseModel):
    total_users: int
    active_users: int
    total_files: int
    total_bytes: int
    unique_bytes: int
    bytes_saved_by_dedup: int
    files_by_type: dict[str, int]
    uploads_last_7_days: list[dict]
    processing_failures: int


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    detail: str
    ip_address: str
    created_at: datetime
    user_email: str | None = None
    file_id: uuid.UUID | None = None


class StorageStats(BaseModel):
    used_bytes: int
    quota_bytes: int
    file_count: int
    by_type: dict[str, int]
