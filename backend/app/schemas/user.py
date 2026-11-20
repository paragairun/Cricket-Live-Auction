"""User schemas — the people who log in.

Version: 2.0.0 (2026-11-20)

Changed from 1.x: cricket ratings and age are gone. A User is an admin,
organizer or auctioneer; players are roster records (schemas/player.py) and do
not have accounts. The organizer-player create/update schemas moved there too,
where email is optional.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.user import ASSIGNABLE_ROLES


class UserRegister(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone: Optional[str] = None

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class GodmodeRegister(BaseModel):
    name: str
    email: EmailStr
    password: str
    secret: str

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class UserLogin(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class UserOnboard(BaseModel):
    phone: Optional[str] = None


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    phone: Optional[str]
    roles: list[str]
    profile_photo: Optional[str]
    onboarded: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AssignRolePayload(BaseModel):
    role: str

    def model_post_init(self, __context):
        if self.role not in ASSIGNABLE_ROLES:
            raise ValueError(f"Invalid role. Must be one of: {sorted(ASSIGNABLE_ROLES)}")
