"""Public event listing for the dashboard.

Version: 2.0.0 (2026-11-20)

Changed from 1.x: "my events" matched `Team.captain_id == user.id` and
`AuctionPlayer.player_id == user.id`. Both of those columns now point at
players.id, not users.id — a user's relationship to an event is their operating
role (admin, organizer or auctioneer). Also filters out archived tournaments.

Visibility:
  draft                              -> the event's admin and organizer only
  ready / active / paused / completed -> any authenticated user
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.auction import AuctionEvent, AuctionPlayer, AuctionStatus, Team
from app.models.user import User

router = APIRouter(prefix="/events", tags=["events"])


class EventCardOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    status: str
    allowed_domains: list[str]
    team_count: int
    player_count: int
    auctioneer_id: Optional[int]
    scheduled_at: Optional[datetime]
    created_at: datetime
    my_roles: list[str] = []
    viewer_count: Optional[int] = None
    logo: Optional[str] = None


def _can_see_draft(event: AuctionEvent, user: User) -> bool:
    return event.admin_id == user.id or event.organizer_id == user.id


@router.get("", response_model=list[EventCardOut])
async def list_all_events(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuctionEvent).where(AuctionEvent.deleted_at.is_(None))
    )
    visible = [
        e
        for e in result.scalars().all()
        if e.status != AuctionStatus.draft or _can_see_draft(e, current_user)
    ]
    return [await _to_card(e, current_user, db) for e in visible]


@router.get("/mine", response_model=list[EventCardOut])
async def list_my_events(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Events where this user holds an operating role."""
    uid = current_user.id
    result = await db.execute(
        select(AuctionEvent).where(
            AuctionEvent.deleted_at.is_(None),
            or_(
                AuctionEvent.admin_id == uid,
                AuctionEvent.organizer_id == uid,
                AuctionEvent.auctioneer_id == uid,
            ),
        )
    )
    return [await _to_card(e, current_user, db) for e in result.scalars().all()]


@router.get("/{event_id}/readiness")
async def event_readiness(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Checklist of what is still missing before the event can be marked ready."""
    result = await db.execute(
        select(AuctionEvent).where(
            AuctionEvent.id == event_id, AuctionEvent.deleted_at.is_(None)
        )
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    teams_result = await db.execute(select(Team).where(Team.event_id == event_id))
    teams = teams_result.scalars().all()

    players_result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    players = players_result.scalars().all()

    teams_with_captain = [t for t in teams if t.captain_id]

    checks = {
        "organizer_assigned": event.organizer_id is not None,
        "auctioneer_assigned": event.auctioneer_id is not None,
        "teams_created": len(teams) >= 2,
        # A captain is now a roster player, so this is informational rather than
        # a hard requirement — an auction can run with no captains named.
        "all_teams_have_captain": len(teams) > 0 and len(teams_with_captain) == len(teams),
        "players_added": len(players) > len(teams),
    }
    required = {k: v for k, v in checks.items() if k != "all_teams_have_captain"}

    return {
        "checks": checks,
        "ready": all(required.values()),
        "teams_count": len(teams),
        "teams_with_captain": len(teams_with_captain),
        "players_count": len(players),
        "auctioneer_id": event.auctioneer_id,
        "status": event.status,
    }


async def _to_card(event: AuctionEvent, user: User, db: AsyncSession) -> EventCardOut:
    teams_result = await db.execute(select(Team).where(Team.event_id == event.id))
    teams = teams_result.scalars().all()

    players_result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event.id)
    )
    players = players_result.scalars().all()

    my_roles: list[str] = []
    if event.admin_id == user.id:
        my_roles.append("admin")
    if event.organizer_id == user.id:
        my_roles.append("organizer")
    if event.auctioneer_id == user.id:
        my_roles.append("auctioneer")

    viewer_count = None
    if event.status == AuctionStatus.completed:
        viewer_count = event.total_viewers
    elif event.status in (AuctionStatus.active, AuctionStatus.paused):
        from app.ws.manager import manager

        stats = await manager.get_viewer_stats(event.id)
        viewer_count = stats.get("live_viewers", 0)

    return EventCardOut(
        id=event.id,
        name=event.name,
        description=event.description,
        status=event.status,
        allowed_domains=event.allowed_domains,
        team_count=len(teams),
        player_count=len(players),
        auctioneer_id=event.auctioneer_id,
        scheduled_at=event.scheduled_at,
        created_at=event.created_at,
        my_roles=my_roles,
        viewer_count=viewer_count,
        logo=event.logo,
    )
