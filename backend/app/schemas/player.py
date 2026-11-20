"""Player schemas — roster records, not accounts.

New in 2.0.0 (2026-11-20).

The point of this module: `email` is Optional everywhere below. In 1.x a player
had to be a User row, and User.email is unique and mandatory because a User logs
in. That forced an organizer to invent an email address for every person in a
room who was never going to log in to anything. Players now carry a name and
nothing else that is required.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class PlayerBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    age: Optional[int] = Field(default=None, ge=5, le=100)
    notes: Optional[str] = None
    batting_rating: float = Field(default=5.0, ge=0, le=10)
    bowling_rating: float = Field(default=5.0, ge=0, le=10)
    fielding_rating: float = Field(default=5.0, ge=0, le=10)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v):
        return v.lower() if v else None

    @field_validator("phone")
    @classmethod
    def blank_phone_is_none(cls, v):
        return v.strip() or None if v else None


class PlayerCreate(PlayerBase):
    """Only `name` is required."""


class PlayerUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    age: Optional[int] = Field(default=None, ge=5, le=100)
    notes: Optional[str] = None
    profile_photo: Optional[str] = None
    batting_rating: Optional[float] = Field(default=None, ge=0, le=10)
    bowling_rating: Optional[float] = Field(default=None, ge=0, le=10)
    fielding_rating: Optional[float] = Field(default=None, ge=0, le=10)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v):
        return v.lower() if v else None


class PlayerOut(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    age: Optional[int] = None
    profile_photo: Optional[str] = None
    notes: Optional[str] = None
    batting_rating: float
    bowling_rating: float
    fielding_rating: float
    created_at: datetime

    model_config = {"from_attributes": True}


class PlayerWithAuctionState(PlayerOut):
    """A player plus their state in one event, for roster screens."""

    auction_player_id: Optional[int] = None
    base_price: Optional[int] = None
    category_id: Optional[int] = None
    status: Optional[str] = None
    sold_price: Optional[int] = None
    sold_to_team_id: Optional[int] = None


class PlayerImportRow(BaseModel):
    """One CSV row. `name` is the only required column."""

    name: str = Field(min_length=1, max_length=120)
    email: Optional[str] = None
    phone: Optional[str] = None
    age: Optional[int] = None
    batting_rating: Optional[float] = 5.0
    bowling_rating: Optional[float] = 5.0
    fielding_rating: Optional[float] = 5.0
    category: Optional[str] = None
    base_price: Optional[int] = None


class PlayerImportResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str]
