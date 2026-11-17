from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator

from app.models.user import ALL_ROLES


class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str
    phone: Optional[str] = None
    age: Optional[int] = None
    batting_rating: Optional[float] = 5.0
    bowling_rating: Optional[float] = 5.0
    fielding_rating: Optional[float] = 5.0

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class GodmodeRegister(BaseModel):
    name: str
    email: EmailStr
    password: str
    secret: str  # must match GODMODE_SECRET

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
    batting_rating: float = 5.0
    bowling_rating: float = 5.0
    fielding_rating: float = 5.0


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    phone: Optional[str]
    age: Optional[int]
    roles: list[str]
    profile_photo: Optional[str]
    batting_rating: float
    bowling_rating: float
    fielding_rating: float
    onboarded: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Organizer-created player profile ──


class OrganizerPlayerCreate(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    age: Optional[int] = None
    batting_rating: Optional[float] = 5.0
    bowling_rating: Optional[float] = 5.0
    fielding_rating: Optional[float] = 5.0
    password: Optional[str] = None  # optional — player can set it later via magic code

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class OrganizerPlayerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    age: Optional[int] = None
    batting_rating: Optional[float] = None
    bowling_rating: Optional[float] = None
    fielding_rating: Optional[float] = None
    category_id: Optional[int] = None  # assigned category on AuctionPlayer

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str | None) -> str | None:
        return v.lower() if v else v


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AssignRolePayload(BaseModel):
    role: str

    def model_post_init(self, __context):
        if self.role not in ALL_ROLES:
            raise ValueError(f"Invalid role. Must be one of: {list(ALL_ROLES)}")
