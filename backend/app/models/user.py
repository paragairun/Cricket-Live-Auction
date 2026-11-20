"""Platform users: the people who log in.

Version: 2.0.0 (2026-11-20)

Changed from 1.x:
  - ROLE_CAPTAIN is gone. Captains no longer log in or bid; a captain is now a
    Player recorded on Team.captain_id. The captain dashboard has been removed.
  - Cricket ratings and age moved off User and onto Player. A User is an admin,
    organizer or auctioneer — an operator of the platform — and operators do not
    have a batting average. Players are roster records (models.auction.Player)
    and never have accounts.

A User still requires a unique email, because a User logs in. Players do not,
which is what "email must not be mandatory for player registration" means: the
mandatory-email constraint only ever applied because players were forced through
the User table.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import StringList

# Role constants — string values stored in the roles array.
ROLE_ADMIN = "admin"
ROLE_ORGANIZER = "organizer"
ROLE_AUCTIONEER = "auctioneer"
# Baseline role for any registered account with no operating privileges.
# Such a user can browse published events and spectate a live auction.
ROLE_VIEWER = "viewer"

# Retained as an alias so historical rows whose roles array contains "player"
# keep working. New accounts get ROLE_VIEWER.
ROLE_PLAYER = "player"

ALL_ROLES = {ROLE_ADMIN, ROLE_ORGANIZER, ROLE_AUCTIONEER, ROLE_VIEWER, ROLE_PLAYER}

# Roles an admin is allowed to grant. "player" is legacy-only and not grantable.
ASSIGNABLE_ROLES = {ROLE_ADMIN, ROLE_ORGANIZER, ROLE_AUCTIONEER, ROLE_VIEWER}

# Roles that carry no privileges, used when normalising an empty roles array.
BASELINE_ROLES = {ROLE_VIEWER, ROLE_PLAYER}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255))

    roles: Mapped[list[str]] = mapped_column(StringList, default=lambda: [ROLE_VIEWER])

    profile_photo: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_active: Mapped[bool] = mapped_column(default=True)
    onboarded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    def has_role(self, role: str) -> bool:
        return role in (self.roles or [ROLE_VIEWER])

    def add_role(self, role: str):
        current = list(self.roles or [ROLE_VIEWER])
        if role not in current:
            current.append(role)
        self.roles = current

    def remove_role(self, role: str):
        current = list(self.roles or [ROLE_VIEWER])
        remaining = [r for r in current if r != role]
        # Never leave an account with no role at all.
        self.roles = remaining or [ROLE_VIEWER]

    @property
    def is_operator(self) -> bool:
        """Can this account run or configure events at all?"""
        return any(
            self.has_role(r) for r in (ROLE_ADMIN, ROLE_ORGANIZER, ROLE_AUCTIONEER)
        )

    def soft_delete(self):
        self.deleted_at = datetime.now(timezone.utc)
        self.is_active = False

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
