"""Authentication and authorization dependencies.

Version: 2.0.0 (2026-11-20)
Changed from 1.x: `require_role` only ever checked whether a role string was
present in the user's global roles array. It never looked at WHICH event the
request was for, and the endpoints did not check either — so any user holding
the auctioneer role anywhere could hammer, pause, finish, re-pick or rewrite
bids on ANY event on the platform. `require_event_access` fixes that by loading
the event and checking the caller's relationship to that specific event.

`require_role` is kept for genuinely global gates (platform admin, "can this
user create events at all") and must NOT be used alone on an endpoint that
takes an event_id.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models.auction import AuctionEvent
from app.models.user import ROLE_ADMIN, ROLE_PLAYER, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Event-scoped roles — what this user is *to this event*, not platform-wide.
EVENT_ADMIN = "event_admin"
EVENT_ORGANIZER = "event_organizer"
EVENT_AUCTIONEER = "event_auctioneer"
# Anyone authenticated who is not attached to the event in an operating role.
EVENT_SPECTATOR = "event_spectator"

# The roles permitted to drive the live auction console. Two people can hold
# these at once (the assigned auctioneer and the organizer acting as assistant),
# which is why every mutating auction operation is idempotency-guarded.
AUCTION_OPERATORS = (EVENT_AUCTIONEER, EVENT_ORGANIZER, EVENT_ADMIN)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await db.execute(
        select(User).where(
            User.id == int(user_id),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*roles: str):
    """Global role gate.

    Use for platform-wide capability checks only. For anything scoped to an
    event, use require_event_access — this function cannot tell one event from
    another and will happily let an auctioneer from event A act on event B.
    """

    async def checker(current_user: User = Depends(get_current_user)) -> User:
        user_roles = current_user.roles or [ROLE_PLAYER]
        if not any(r in user_roles for r in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {list(roles)}",
            )
        return current_user

    return checker


def event_roles_for(event: AuctionEvent, user: User) -> set[str]:
    """Which event-scoped roles this user holds for this specific event."""
    roles: set[str] = set()
    if event.admin_id == user.id or user.has_role(ROLE_ADMIN):
        roles.add(EVENT_ADMIN)
    if event.organizer_id == user.id:
        roles.add(EVENT_ORGANIZER)
    if event.auctioneer_id == user.id:
        roles.add(EVENT_AUCTIONEER)
    if not roles:
        roles.add(EVENT_SPECTATOR)
    return roles


@dataclass
class EventContext:
    """Resolved event plus the caller's relationship to it."""

    event: AuctionEvent
    user: User
    roles: set[str]

    def has(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)

    @property
    def is_operator(self) -> bool:
        return self.has(*AUCTION_OPERATORS)


def require_event_access(*allowed_roles: str):
    """Load the event in the path and assert the caller's role ON THAT EVENT.

    Usage:
        ctx: EventContext = Depends(require_event_access(*AUCTION_OPERATORS))

    With no roles given, any authenticated user passes and `ctx.roles` carries
    what they are — useful for read endpoints that vary by viewer.
    """

    async def checker(
        event_id: int = Path(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> EventContext:
        result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
        event = result.scalar_one_or_none()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        roles = event_roles_for(event, current_user)
        if allowed_roles and not any(r in roles for r in allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have that role on this event",
            )
        return EventContext(event=event, user=current_user, roles=roles)

    return checker


async def authorize_event_socket(
    event_id: int,
    token: str,
    db: AsyncSession,
) -> User | None:
    """Authorize a WebSocket connection to an event's live feed.

    Returns the authenticated user, or None when the connection must be refused.

    Previously the WebSocket endpoint declared `token: str = ""` and accepted a
    missing or invalid token as an anonymous connection, then immediately pushed
    the full auction state — every team's budget, every bid, every player — to
    anyone who knew an event id.
    """
    if not token:
        return None

    user_id = decode_token(token)
    if not user_id:
        return None

    result = await db.execute(
        select(User).where(
            User.id == int(user_id),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        return None

    event_result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event:
        return None

    # A draft event is visible only to the people setting it up. Once an event
    # is published, any authenticated user may spectate.
    if event.status == "draft":
        roles = event_roles_for(event, user)
        if EVENT_SPECTATOR in roles:
            return None

    return user
