import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import random
import string

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import hash_password, verify_password, create_access_token
from app.core.config import settings
from app.models.user import User, ROLE_VIEWER, ROLE_ADMIN
from app.models.auth_token import AuthToken
from app.models.auction import AuctionEvent

MAGIC_CODE_TTL = 600  # 10 minutes


def _generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))
from app.schemas.user import UserRegister, GodmodeRegister, UserLogin, UserOnboard, UserOut, TokenOut
from app.core.background import dispatch_email
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional


class InviteRegister(BaseModel):
    name: str
    password: str
    token: str
    phone: Optional[str] = None


class MagicCodeRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class MagicCodeVerify(BaseModel):
    email: EmailStr
    code: str

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)):
    """Public registration. Creates a viewer account.

    Players are roster records created by an organizer and never register here;
    see POST /organizer/events/{id}/players.
    """
    existing = await db.execute(
        select(User).where(
            User.email == payload.email,
            User.deleted_at.is_(None),
            User.is_active == True
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        hashed_password=hash_password(payload.password),
        roles=[ROLE_VIEWER],
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(str(user.id))
    await dispatch_email("task_send_welcome", user.email, user.name)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/godmode", response_model=TokenOut, status_code=status.HTTP_200_OK)
async def godmode_register(payload: GodmodeRegister, db: AsyncSession = Depends(get_db)):
    """
    Bootstrap admin registration. Requires GODMODE_SECRET.
    If the email already exists, admin role is added to the existing account.
    """
    # 1.x only compared the secret, so a deployment that forgot to set
    # GODMODE_SECRET shipped with the documented default "GODMODE_CHANGEME".
    if not settings.godmode_enabled:
        raise HTTPException(
            status_code=404,
            detail="Not found",
        )
    if not secrets.compare_digest(payload.secret, settings.GODMODE_SECRET or ""):
        raise HTTPException(status_code=403, detail="Invalid godmode secret")

    result = await db.execute(
        select(User).where(
            User.email == payload.email,
            User.deleted_at.is_(None),
            User.is_active == True
        )
    )
    user = result.scalar_one_or_none()

    if user:
        # Existing account — just grant admin role
        user.add_role(ROLE_ADMIN)
        user.onboarded = True
    else:
        # New account — create with admin + player roles
        user = User(
            name=payload.name,
            email=payload.email,
            hashed_password=hash_password(payload.password),
            roles=[ROLE_ADMIN],
            onboarded=True,
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)

    token = create_access_token(str(user.id))
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(
            User.email == payload.email,
            User.deleted_at.is_(None),
            User.is_active == True
        )
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(str(user.id))
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.patch("/onboard", response_model=UserOut)
async def onboard(
    payload: UserOnboard,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.phone is not None:
        current_user.phone = payload.phone
    current_user.onboarded = True
    await db.commit()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)


@router.post("/upload-photo", response_model=UserOut)
async def upload_photo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg"
    content = await file.read()
    content_type = file.content_type or f"image/{ext}"

    if settings.s3_enabled:
        from app.services.s3_service import upload_file
        import asyncio
        url = await asyncio.to_thread(upload_file, content, "profile", content_type)
        if url:
            current_user.profile_photo = url
        else:
            raise HTTPException(status_code=500, detail="S3 upload failed")
    else:
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        filename = f"{uuid.uuid4()}.{ext}"
        filepath = os.path.join(settings.UPLOAD_DIR, filename)
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(content)
        current_user.profile_photo = f"/uploads/{filename}"

    await db.commit()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)


@router.get("/invite-info")
async def get_invite_info(token: str, db: AsyncSession = Depends(get_db)):
    """Return invitation metadata from the database if the token is still valid."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AuthToken).where(
            AuthToken.token == token,
            AuthToken.purpose == "invite",
            AuthToken.expires_at > now,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Invite link is invalid or has expired")
    return {
        "email": record.email,
        "event_id": record.event_id,
        "event_name": record.event_name,
        "role": record.role,
    }


@router.post("/accept-invite", response_model=TokenOut, status_code=201)
async def accept_invite(payload: InviteRegister, db: AsyncSession = Depends(get_db)):
    """Accept a one-time invitation stored in the authoritative database."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AuthToken)
        .where(
            AuthToken.token == payload.token,
            AuthToken.purpose == "invite",
            AuthToken.expires_at > now,
        )
        .with_for_update()
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite link is invalid or has expired")

    email, role, event_id = invite.email, invite.role, invite.event_id
    if not role or not event_id:
        raise HTTPException(status_code=400, detail="Invitation data is incomplete")

    result = await db.execute(
        select(User).where(
            User.email == email,
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user:
        user.add_role(role)
    else:
        user = User(
            name=payload.name,
            email=email,
            phone=payload.phone,
            hashed_password=hash_password(payload.password),
            roles=[ROLE_VIEWER, role],
            onboarded=False,
        )
        db.add(user)
        await db.flush()

    event = await db.get(AuctionEvent, event_id)
    if event:
        if role == "organizer":
            event.organizer_id = user.id
        elif role == "auctioneer":
            event.auctioneer_id = user.id

    await db.delete(invite)
    await db.commit()
    await db.refresh(user)
    access_token = create_access_token(str(user.id))
    return TokenOut(access_token=access_token, user=UserOut.model_validate(user))

@router.post("/send-magic-code")
async def send_magic_code(payload: MagicCodeRequest, db: AsyncSession = Depends(get_db)):
    """Create a database-backed, expiring one-time login code."""
    result = await db.execute(
        select(User).where(
            User.email == payload.email,
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        return {"detail": "If that email is registered, a code has been sent."}

    code = _generate_code()
    token_id = f"magic:{payload.email}"
    existing = await db.get(AuthToken, token_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=MAGIC_CODE_TTL)
    if existing:
        existing.code = code
        existing.expires_at = expires_at
    else:
        db.add(AuthToken(
            token=token_id,
            purpose="magic",
            email=payload.email,
            code=code,
            expires_at=expires_at,
        ))
    await db.commit()
    await dispatch_email("task_send_magic_code", payload.email, user.name, code)
    return {"detail": "Code sent. Check your email."}


@router.post("/verify-magic-code", response_model=TokenOut)
async def verify_magic_code(payload: MagicCodeVerify, db: AsyncSession = Depends(get_db)):
    """Verify and consume a database-backed one-time login code."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AuthToken)
        .where(
            AuthToken.token == f"magic:{payload.email}",
            AuthToken.purpose == "magic",
            AuthToken.expires_at > now,
        )
        .with_for_update()
    )
    record = result.scalar_one_or_none()
    if not record or not secrets.compare_digest(record.code or "", payload.code.strip()):
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    result = await db.execute(
        select(User).where(
            User.email == payload.email,
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(record)
    await db.commit()
    token = create_access_token(str(user.id))
    return TokenOut(access_token=token, user=UserOut.model_validate(user))
