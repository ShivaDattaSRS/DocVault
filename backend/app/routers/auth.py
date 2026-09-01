from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, log_activity
from ..models import OTPCode, User
from ..schemas import (
    LoginRequest,
    MessageResponse,
    OTPResendRequest,
    OTPVerifyRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from ..security import create_access_token, generate_otp, hash_otp, hash_password, verify_otp, verify_password
from ..services.jobs import rate_limit
from ..services.mailer import send_otp_email

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_otp(db: Session, user: User, purpose: str) -> tuple[str, bool]:
    """Invalidate outstanding codes, create a new one and email it."""
    db.execute(
        update(OTPCode)
        .where(OTPCode.email == user.email, OTPCode.purpose == purpose, OTPCode.consumed.is_(False))
        .values(consumed=True)
    )
    code = generate_otp()
    db.add(
        OTPCode(
            email=user.email,
            purpose=purpose,
            code_hash=hash_otp(code),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.otp_ttl_seconds),
        )
    )
    db.commit()
    emailed = send_otp_email(user.email, user.full_name, code)
    return code, emailed


def _otp_response(code: str, emailed: bool, message: str) -> MessageResponse:
    # In dev mode (SMTP unreachable) the code is surfaced so the flow stays testable.
    return MessageResponse(
        message=message if emailed else f"{message} (SMTP unavailable — dev code shown below)",
        dev_otp=None if emailed else code,
    )


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        if existing.is_verified:
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
        # Unverified signup — refresh the password and re-send a code.
        existing.password_hash = hash_password(payload.password)
        existing.full_name = payload.full_name.strip()
        db.commit()
        code, emailed = _issue_otp(db, existing, "verify")
        return _otp_response(code, emailed, "Verification code re-sent to your email.")

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        quota_bytes=settings.default_quota_bytes,
        is_admin=email == settings.admin_email.lower().strip(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_activity(db, "register", request=request, user=user, detail=f"Account created for {email}")
    db.commit()

    code, emailed = _issue_otp(db, user, "verify")
    return _otp_response(code, emailed, "Account created. Enter the code we emailed you to verify.")


@router.post("/login", response_model=MessageResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Step 1 of login: password check, then an OTP is emailed."""
    email = payload.email.lower().strip()
    if not rate_limit(f"login:{email}", limit=10, window_seconds=300):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Try again shortly.")

    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        log_activity(db, "login_failed", request=request, user=user, detail=email)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")

    purpose = "login" if user.is_verified else "verify"
    code, emailed = _issue_otp(db, user, purpose)
    return _otp_response(code, emailed, f"We emailed a 6-digit code to {email}.")


@router.post("/verify-otp", response_model=TokenResponse)
def verify_otp_code(payload: OTPVerifyRequest, request: Request, db: Session = Depends(get_db)):
    """Step 2 of login: exchange a valid OTP for an access token."""
    email = payload.email.lower().strip()
    if not rate_limit(f"otp:{email}", limit=15, window_seconds=300):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Request a new code.")

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No account found for that email")

    otp = db.scalar(
        select(OTPCode)
        .where(OTPCode.email == email, OTPCode.consumed.is_(False))
        .order_by(OTPCode.created_at.desc())
    )
    if otp is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No active code. Please request a new one.")

    expires_at = otp.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        otp.consumed = True
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code has expired. Please request a new one.")

    if otp.attempts >= settings.otp_max_attempts:
        otp.consumed = True
        db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong codes. Request a new one.")

    if not verify_otp(payload.code.strip(), otp.code_hash):
        otp.attempts += 1
        remaining = settings.otp_max_attempts - otp.attempts
        log_activity(db, "otp_failed", request=request, user=user, detail=f"{remaining} attempts left")
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Incorrect code. {remaining} attempt(s) left.")

    otp.consumed = True
    user.is_verified = True
    log_activity(db, "login", request=request, user=user, detail="OTP verified")
    db.commit()
    db.refresh(user)

    token = create_access_token(str(user.id), {"admin": user.is_admin})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/resend-otp", response_model=MessageResponse)
def resend_otp(payload: OTPResendRequest, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    if not rate_limit(f"resend:{email}", limit=5, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Please wait before requesting another code.")

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No account found for that email")

    code, emailed = _issue_otp(db, user, "login" if user.is_verified else "verify")
    return _otp_response(code, emailed, "A new code is on its way.")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log_activity(db, "logout", request=request, user=user)
    db.commit()
    return MessageResponse(message="Signed out")
