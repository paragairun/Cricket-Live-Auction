"""Admin endpoints: tournaments and user roles.

Version: 2.0.0 (2026-11-20)

Changed from 1.x:
  - Added DELETE /events/{id} and a complete PATCH. An admin can now edit and
    delete a tournament; 1.x had create, list, get and a partial patch that
    silently ignored budget, squad-size and base-price changes.
  - Event endpoints are scoped to the event's own admin rather than to anyone
    holding the global admin role.
  - Role assignment is restricted to ASSIGNABLE_ROLES and an admin cannot strip
    their own admin role (which would lock the platform out of itself).
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.background import dispatch_email
from app.core.security import decode_token  # noqa: F401 (kept for route parity)
from app.models.auction import (
    AuctionEvent,
    AuctionPlayer,
    AuctionStatus,
    Bid,
    BidSlab,
    PlayerCategory,
    Team,
    TeamPlayer,
)
from app.models.user import (
    ASSIGNABLE_ROLES,
    ROLE_ADMIN,
    ROLE_AUCTIONEER,
    ROLE_ORGANIZER,
    User,
)
from app.models.auth_token import AuthToken
from app.schemas.auction import AuctionEventCreate, AuctionEventOut, AuctionEventUpdate
from app.schemas.user import AssignRolePayload, UserOut
from app.core.deps import require_role

logger = logging.getLogger(__name__)

INVITE_TTL_SECONDS = 60 * 60 * 24 * 7

router = APIRouter(prefix="/admin", tags=["admin"])

AdminUser = Depends(require_role(ROLE_ADMIN))


class InvitePayload(BaseModel):
    email: EmailStr
    role: str  # "organizer" | "auctioneer"

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


async def _get_own_event(event_id: int, admin: User, db: AsyncSession) -> AuctionEvent:
    result = await db.execute(
        select(AuctionEvent).where(
            AuctionEvent.id == event_id, AuctionEvent.deleted_at.is_(None)
        )
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Tournament not found")
    if event.admin_id != admin.id:
        raise HTTPException(status_code=403, detail="This tournament belongs to another admin")
    return event


# ── Tournaments ───────────────────────────────────────────────────────────────


@router.post("/events", response_model=AuctionEventOut, status_code=201)
async def create_event(
    payload: AuctionEventCreate,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    event = AuctionEvent(
        name=payload.name.strip(),
        description=payload.description,
        admin_id=current_user.id,
        allowed_domains=payload.allowed_domains,
        scheduled_at=payload.scheduled_at,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


@router.get("/events", response_model=list[AuctionEventOut])
async def list_events(
    include_deleted: bool = False,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    query = select(AuctionEvent).where(AuctionEvent.admin_id == current_user.id)
    if not include_deleted:
        query = query.where(AuctionEvent.deleted_at.is_(None))
    result = await db.execute(query.order_by(AuctionEvent.created_at.desc()))
    return [AuctionEventOut.model_validate(e) for e in result.scalars().all()]


@router.get("/events/{event_id}", response_model=AuctionEventOut)
async def get_event(
    event_id: int,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    event = await _get_own_event(event_id, current_user, db)
    return AuctionEventOut.model_validate(event)


@router.patch("/events/{event_id}", response_model=AuctionEventOut)
async def update_event(
    event_id: int,
    payload: AuctionEventUpdate,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Edit a tournament.

    1.x applied only name, description, organizer, auctioneer, domains and
    schedule — budget, squad sizes and base price were in the schema but
    silently dropped.
    """
    event = await _get_own_event(event_id, current_user, db)

    if event.status == AuctionStatus.completed:
        raise HTTPException(status_code=400, detail="Cannot edit a completed tournament")

    data = payload.model_dump(exclude_unset=True)

    if event.status == AuctionStatus.active:
        locked = {"team_budget", "team_max_players", "team_min_players", "player_base_price"}
        attempted = locked & data.keys()
        if attempted:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot change {', '.join(sorted(attempted))} while the auction is running",
            )

    # Assigning an operator grants them the matching role.
    if "organizer_id" in data and data["organizer_id"] is not None:
        org = await db.get(User, data["organizer_id"])
        if not org:
            raise HTTPException(status_code=400, detail="Organizer user not found")
        org.add_role(ROLE_ORGANIZER)

    if "auctioneer_id" in data and data["auctioneer_id"] is not None:
        auctioneer = await db.get(User, data["auctioneer_id"])
        if not auctioneer:
            raise HTTPException(status_code=400, detail="Auctioneer user not found")
        auctioneer.add_role(ROLE_AUCTIONEER)

    if "team_min_players" in data and data["team_min_players"] is not None:
        max_players = data.get("team_max_players", event.team_max_players)
        if data["team_min_players"] > max_players:
            raise HTTPException(
                status_code=400, detail="Minimum squad cannot exceed maximum squad"
            )

    for field, value in data.items():
        setattr(event, field, value)

    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


@router.delete("/events/{event_id}", status_code=200)
async def delete_event(
    event_id: int,
    hard: bool = Query(
        False,
        description="Permanently remove the tournament and all of its data. "
        "Without this it is archived and can be restored.",
    ),
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Delete a tournament. New in 2.0.0 — 1.x had no delete at all.

    Soft by default: the tournament is archived and disappears from listings but
    its results stay intact. `hard=true` removes everything, which is refused
    while an auction is running.
    """
    event = await _get_own_event(event_id, current_user, db)

    if event.status == AuctionStatus.active:
        raise HTTPException(
            status_code=409,
            detail="This auction is running. Pause or finish it before deleting.",
        )

    if not hard:
        from datetime import datetime, timezone

        event.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        return {
            "status": "archived",
            "message": f"'{event.name}' archived. Its results are preserved.",
        }

    # Hard delete: children first, in foreign-key order.
    team_ids = [
        t.id for t in (await db.execute(select(Team).where(Team.event_id == event_id))).scalars()
    ]

    await db.execute(Bid.__table__.delete().where(Bid.event_id == event_id))
    await db.execute(TeamPlayer.__table__.delete().where(TeamPlayer.event_id == event_id))
    await db.execute(AuctionPlayer.__table__.delete().where(AuctionPlayer.event_id == event_id))
    await db.execute(BidSlab.__table__.delete().where(BidSlab.event_id == event_id))
    await db.execute(PlayerCategory.__table__.delete().where(PlayerCategory.event_id == event_id))
    if team_ids:
        await db.execute(Team.__table__.delete().where(Team.id.in_(team_ids)))
    await db.delete(event)
    await db.commit()

    logger.warning("Admin %s hard-deleted tournament %s", current_user.id, event_id)
    return {"status": "deleted", "message": f"'{event.name}' and all of its data were deleted."}


@router.post("/events/{event_id}/restore", response_model=AuctionEventOut)
async def restore_event(
    event_id: int,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Undo a soft delete."""
    result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Tournament not found")
    if event.admin_id != current_user.id:
        raise HTTPException(status_code=403, detail="This tournament belongs to another admin")
    event.deleted_at = None
    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


# ── Users ─────────────────────────────────────────────────────────────────────


@router.get("/users", response_model=list[UserOut])
async def list_users(
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.deleted_at.is_(None), User.is_active.is_(True))
    )
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.get("/users/search", response_model=list[UserOut])
async def search_users(
    q: str = Query(..., min_length=1),
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    pattern = f"%{q}%"
    result = await db.execute(
        select(User)
        .where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            or_(User.name.ilike(pattern), User.email.ilike(pattern)),
        )
        .limit(8)
    )
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.post("/events/{event_id}/invite", response_model=dict)
async def invite_to_event(
    event_id: int,
    payload: InvitePayload,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Invite an organizer or auctioneer. These are the only roles that log in."""
    if payload.role not in (ROLE_ORGANIZER, ROLE_AUCTIONEER):
        raise HTTPException(status_code=400, detail="Role must be organizer or auctioneer")

    event = await _get_own_event(event_id, current_user, db)

    result = await db.execute(
        select(User).where(
            User.email == payload.email,
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()

    if user:
        user.add_role(payload.role)
        if payload.role == ROLE_ORGANIZER:
            event.organizer_id = user.id
        else:
            event.auctioneer_id = user.id
        await db.commit()
        await db.refresh(user)
        await dispatch_email(
            "task_send_event_invitation",
            user.email, user.name, event.name, event.id, payload.role,
        )
        return {
            "status": "assigned",
            "user": UserOut.model_validate(user).model_dump(),
            "message": f"{user.name} assigned as {payload.role}",
        }

    token = secrets.token_urlsafe(32)
    db.add(AuthToken(
        token=token,
        purpose="invite",
        email=payload.email,
        event_id=event_id,
        event_name=event.name,
        role=payload.role,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=INVITE_TTL_SECONDS),
    ))
    await db.commit()
    await dispatch_email("task_send_organizer_invite", payload.email, event.name, token, payload.role)
    return {"status": "invited", "user": None, "message": f"Invitation sent to {payload.email}"}


@router.post("/users/{user_id}/roles/add", response_model=UserOut)
async def add_user_role(
    user_id: int,
    payload: AssignRolePayload,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    if payload.role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Role must be one of: {sorted(ASSIGNABLE_ROLES)}",
        )
    result = await db.execute(
        select(User).where(
            User.id == user_id, User.deleted_at.is_(None), User.is_active.is_(True)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.add_role(payload.role)
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/users/{user_id}/roles/remove", response_model=UserOut)
async def remove_user_role(
    user_id: int,
    payload: AssignRolePayload,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(
            User.id == user_id, User.deleted_at.is_(None), User.is_active.is_(True)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == current_user.id and payload.role == ROLE_ADMIN:
        raise HTTPException(
            status_code=400, detail="You cannot remove your own admin role"
        )

    user.remove_role(payload.role)
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)
