import secrets
import json
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.core.database import get_db
from app.core.deps import require_role
from app.core.redis import get_redis
from app.core.security import hash_password
from app.models.user import User, ROLE_ORGANIZER, ROLE_CAPTAIN, ROLE_AUCTIONEER, ROLE_PLAYER
from app.models.auction import (
    AuctionEvent, Team, AuctionPlayer, AuctionStatus, TeamPlayer, PlayerAuctionStatus,
    BidSlab, PlayerCategory,
)
from app.schemas.auction import (
    AuctionPlayerCreate,
    AuctionPlayerOut,
    TeamCreate,
    TeamUpdate,
    TeamOut,
    AuctionEventOut,
    PlayerImportRow,
    PlayerImportResult,
    BidSlabCreate,
    BidSlabOut,
    PlayerCategoryCreate,
    PlayerCategoryUpdate,
    PlayerCategoryOut,
)
from app.schemas.user import UserOut, AssignRolePayload
from app.services import email_service
from app.core.queue import enqueue

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from app.schemas.user import UserOut, AssignRolePayload, OrganizerPlayerCreate, OrganizerPlayerUpdate
from app.services import email_service
from app.services.s3_service import upload_file
from app.core.queue import enqueue

INVITE_TTL_SECONDS = 60 * 60 * 24 * 7


class AuctioneerInvitePayload(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class PlayerInvitePayload(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class EventSettingsUpdatePayload(BaseModel):
    scheduled_at: datetime | None = None
    team_budget: int | None = Field(default=None, ge=1000)
    team_max_players: int | None = None
    player_base_price: int | None = Field(default=None, ge=100)
    no_timer: bool | None = None

router = APIRouter(prefix="/organizer", tags=["organizer"])


async def _get_event_for_organizer(event_id: int, organizer: User, db: AsyncSession) -> AuctionEvent:
    result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.organizer_id != organizer.id:
        raise HTTPException(status_code=403, detail="Not your event")
    return event


@router.get("/users/search", response_model=list[UserOut])
async def search_users(
    q: str = Query(..., min_length=1),
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """Search users by name or email — used by the invite widget for organizers."""
    pattern = f"%{q}%"
    result = await db.execute(
        select(User).where(
            User.deleted_at.is_(None),
            User.is_active == True,
            or_(User.name.ilike(pattern), User.email.ilike(pattern))
        ).limit(8)
    )
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """Get a single user by ID"""
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.deleted_at.is_(None),
            User.is_active == True
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)


@router.get("/events", response_model=list[AuctionEventOut])
async def list_my_events(
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuctionEvent).where(AuctionEvent.organizer_id == current_user.id)
    )
    return [AuctionEventOut.model_validate(e) for e in result.scalars().all()]


@router.get("/events/{event_id}/eligible-players", response_model=list[UserOut])
async def eligible_players(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)

    result = await db.execute(
        select(User).where(User.deleted_at.is_(None), User.is_active == True)
    )
    users = result.scalars().all()

    if not event.allowed_domains:
        return [UserOut.model_validate(u) for u in users]

    eligible = [
        u for u in users
        if any(u.email.endswith(f"@{d}") for d in event.allowed_domains)
    ]
    return [UserOut.model_validate(u) for u in eligible]


@router.post("/events/{event_id}/players", response_model=AuctionPlayerOut, status_code=201)
async def add_player(
    event_id: int,
    payload: AuctionPlayerCreate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify players after event is published")

    existing = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.player_id == payload.player_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Player already added")

    count_result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    count = len(count_result.scalars().all())

    base_price = payload.base_price
    if base_price is None and payload.category_id is not None:
        cat_result = await db.execute(
            select(PlayerCategory).where(PlayerCategory.id == payload.category_id, PlayerCategory.event_id == event_id)
        )
        cat = cat_result.scalar_one_or_none()
        if cat:
            base_price = cat.base_price
    if base_price is None:
        base_price = event.player_base_price

    ap = AuctionPlayer(
        event_id=event_id,
        player_id=payload.player_id,
        base_price=base_price,
        category_id=payload.category_id,
        auction_order=count + 1,
    )
    db.add(ap)
    await db.commit()
    await db.refresh(ap)
    # Emails are sent in bulk when organizer marks the event ready — not here
    return AuctionPlayerOut.model_validate(ap)


@router.post("/events/{event_id}/players/create-profile", response_model=UserOut, status_code=201)
async def create_player_profile(
    event_id: int,
    payload: OrganizerPlayerCreate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """
    Organizer creates a player profile directly.
    Creates a user account with player role. The player can log in later
    using magic code or reset their password.
    """
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot create player profiles after event is published")

    # Check if email already exists
    existing = await db.execute(
        select(User).where(User.email == payload.email, User.deleted_at.is_(None))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        age=payload.age,
        hashed_password=hash_password(payload.password or "password123"),
        roles=[ROLE_PLAYER],
        batting_rating=payload.batting_rating or 5.0,
        bowling_rating=payload.bowling_rating or 5.0,
        fielding_rating=payload.fielding_rating or 5.0,
        onboarded=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.patch("/events/{event_id}/players/{player_id}/profile", response_model=UserOut)
async def edit_player_profile(
    event_id: int,
    player_id: int,
    payload: OrganizerPlayerUpdate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """
    Organizer edits a player's profile (name, email, phone, age, ratings, category).
    Password is not editable here — players reset via magic code / reset password.
    """
    event = await _get_event_for_organizer(event_id, current_user, db)

    # The player must be part of this event's pool
    ap_result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.player_id == player_id,
        )
    )
    ap = ap_result.scalar_one_or_none()
    if not ap:
        raise HTTPException(status_code=404, detail="Player not in this event")

    user_result = await db.execute(select(User).where(User.id == player_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Player user not found")

    # Email uniqueness check if changing email
    if payload.email is not None and payload.email != user.email:
        existing = await db.execute(
            select(User).where(
                User.email == payload.email,
                User.deleted_at.is_(None),
                User.id != player_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email already registered")

    update_data = payload.model_dump(exclude_unset=True)
    category_id = update_data.pop("category_id", None)

    # Validate category belongs to this event
    if category_id is not None:
        cat_result = await db.execute(
            select(PlayerCategory).where(
                PlayerCategory.id == category_id,
                PlayerCategory.event_id == event_id,
            )
        )
        if not cat_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Category not found in this event")
        ap.category_id = category_id

    for field, value in update_data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    await db.refresh(ap)
    return UserOut.model_validate(user)


@router.get("/events/{event_id}/players", response_model=list[AuctionPlayerOut])
async def list_event_players(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    await _get_event_for_organizer(event_id, current_user, db)
    result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    return [AuctionPlayerOut.model_validate(ap) for ap in result.scalars().all()]


@router.get("/events/{event_id}/players/export-xlsx")
async def export_players_xlsx(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """Export event players as an XLSX spreadsheet."""
    if not HAS_OPENPYXL:
        raise HTTPException(status_code=500, detail="Excel export is not available on the server")

    event = await _get_event_for_organizer(event_id, current_user, db)

    # Load players with their user profiles and team/captain info
    result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id).order_by(AuctionPlayer.auction_order)
    )
    auction_players = result.scalars().all()

    # Build player rows
    rows = []
    for ap in auction_players:
        user_result = await db.execute(select(User).where(User.id == ap.player_id))
        user = user_result.scalar_one_or_none()
        if not user:
            continue

        # Fetch category name
        category_name = ""
        if ap.category_id:
            cat_result = await db.execute(
                select(PlayerCategory).where(PlayerCategory.id == ap.category_id)
            )
            cat = cat_result.scalar_one_or_none()
            if cat:
                category_name = cat.name

        # Team + sold price
        team_result = await db.execute(
            select(Team, TeamPlayer)
            .outerjoin(TeamPlayer, TeamPlayer.player_id == user.id)
            .where(Team.id == TeamPlayer.team_id, Team.event_id == event_id)
        )
        team_row = team_result.first()
        team_name = team_row.Team.name if team_row else ""
        sold_price = team_row.TeamPlayer.sold_price if team_row else ""

        rows.append({
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": str(user.phone or ""),
            "age": user.age or "",
            "roles": ", ".join(user.roles or []),
            "batting_rating": user.batting_rating,
            "bowling_rating": user.bowling_rating,
            "fielding_rating": user.fielding_rating,
            "onboarded": "Yes" if user.onboarded else "No",
            "base_price": ap.base_price,
            "category": category_name,
            "current_bid": ap.current_bid,
            "status": ap.status,
            "auction_order": ap.auction_order,
            "team": team_name,
            "sold_price": sold_price,
        })

    # Write XLSX in memory
    wb = Workbook()
    ws = wb.active
    ws.title = "Players"

    headers = [
        "ID", "Name", "Email", "Phone", "Age", "Roles",
        "Batting", "Bowling", "Fielding", "Onboarded",
        "Base Price", "Category", "Current Bid", "Status",
        "Auction Order", "Team", "Sold Price",
    ]

    # Header styling
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F2937")  # gray-800
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Side(style="thin", color="4B5563")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = border

    # Data rows
    for row_idx, row in enumerate(rows, start=2):
        values = [
            row["id"], row["name"], row["email"], row["phone"], row["age"], row["roles"],
            row["batting_rating"], row["bowling_rating"], row["fielding_rating"], row["onboarded"],
            row["base_price"], row["category"], row["current_bid"], row["status"],
            row["auction_order"], row["team"], row["sold_price"],
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = border
            cell.alignment = Alignment(vertical="center")
            
            # Force Phone column to be Text so Excel preserves leading zeros and formatting
            if headers[col_idx - 1] == "Phone":
                cell.number_format = '@'
                cell.value = str(val) if val is not None else ""

            if col_idx == 1:
                cell.font = Font(bold=True)

    # Auto-size columns
    for col_idx in range(1, len(headers) + 1):
        column_letter = get_column_letter(col_idx)
        max_length = len(str(headers[col_idx - 1]))
        for row_idx in range(2, len(rows) + 2):
            cell_val = ws.cell(row=row_idx, column=col_idx).value
            if cell_val is not None:
                max_length = max(max_length, len(str(cell_val)))
        ws.column_dimensions[column_letter].width = min(max_length + 2, 40)

    # Freeze header row
    ws.freeze_panes = "A2"

    # Save to bytes
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"{event.name.replace(' ', '_')}_players.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/events/{event_id}/players/{player_id}", status_code=204)
async def remove_player(
    event_id: int,
    player_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify players after event is published")
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.player_id == player_id,
        )
    )
    ap = result.scalar_one_or_none()
    if not ap:
        raise HTTPException(status_code=404, detail="Player not in this event")
    if ap.status != "pending":
        raise HTTPException(status_code=400, detail="Cannot remove a player already in auction")
    await db.delete(ap)
    await db.commit()


@router.post("/events/{event_id}/teams", response_model=TeamOut, status_code=201)
async def create_team(
    event_id: int,
    payload: TeamCreate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify teams after event is published")

    team = Team(
        event_id=event_id,
        name=payload.name,
        color=payload.color,
        budget=event.team_budget,
        max_players=event.team_max_players,
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return TeamOut.model_validate(team)


@router.get("/events/{event_id}/teams", response_model=list[TeamOut])
async def list_teams(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    await _get_event_for_organizer(event_id, current_user, db)
    result = await db.execute(select(Team).where(Team.event_id == event_id))
    return [TeamOut.model_validate(t) for t in result.scalars().all()]


@router.patch("/events/{event_id}/teams/{team_id}", response_model=TeamOut)
async def update_team(
    event_id: int,
    team_id: int,
    payload: TeamUpdate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify teams after event is published")

    result = await db.execute(
        select(Team).where(Team.id == team_id, Team.event_id == event_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if payload.name is not None:
        team.name = payload.name
    if payload.color is not None:
        team.color = payload.color
    if payload.captain_id is not None:
        # Auto-grant captain role to assigned user
        cap_result = await db.execute(select(User).where(User.id == payload.captain_id))
        cap_user = cap_result.scalar_one_or_none()
        if cap_user:
            cap_user.add_role(ROLE_CAPTAIN)
            # Email queued — will fire when organizer marks event ready

        # Handle removing old captain from roster if changing
        if team.captain_id and team.captain_id != payload.captain_id:
            old_cap_result = await db.execute(select(User).where(User.id == team.captain_id))
            old_cap = old_cap_result.scalar_one_or_none()
            if old_cap:
                old_cap.remove_role(ROLE_CAPTAIN)
            
            # Remove old captain from TeamPlayer (if they were added for 0 price)
            old_tp_res = await db.execute(
                select(TeamPlayer).where(
                    TeamPlayer.team_id == team.id,
                    TeamPlayer.player_id == team.captain_id,
                    TeamPlayer.sold_price == 0
                )
            )
            old_tp = old_tp_res.scalar_one_or_none()
            if old_tp:
                await db.delete(old_tp)
                
            # Revert old captain in AuctionPlayer back to pending
            old_ap_res = await db.execute(
                select(AuctionPlayer).where(
                    AuctionPlayer.event_id == event_id,
                    AuctionPlayer.player_id == team.captain_id
                )
            )
            old_ap = old_ap_res.scalar_one_or_none()
            if old_ap and old_ap.status == PlayerAuctionStatus.sold and old_ap.current_bid == 0:
                old_ap.status = PlayerAuctionStatus.pending
                old_ap.current_bidder_id = None
                old_ap.current_bid = 0

        team.captain_id = payload.captain_id
        
        # Add new captain to TeamPlayer if not already there
        tp_res = await db.execute(
            select(TeamPlayer).where(
                TeamPlayer.team_id == team.id,
                TeamPlayer.player_id == payload.captain_id
            )
        )
        if not tp_res.scalar_one_or_none():
            tp = TeamPlayer(team_id=team.id, player_id=payload.captain_id, sold_price=0)
            db.add(tp)
            
        # Ensure new captain is in AuctionPlayer and marked as 'sold'
        ap_res = await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.event_id == event_id,
                AuctionPlayer.player_id == payload.captain_id
            )
        )
        ap = ap_res.scalar_one_or_none()
        if ap:
            ap.status = PlayerAuctionStatus.sold
            ap.current_bidder_id = payload.captain_id
            ap.current_bid = 0
        else:
            ap = AuctionPlayer(
                event_id=event_id,
                player_id=payload.captain_id,
                base_price=event.player_base_price,
                status=PlayerAuctionStatus.sold,
                current_bid=0,
                current_bidder_id=payload.captain_id,
                auction_order=0
            )
            db.add(ap)

    if payload.budget is not None:
        team.budget = payload.budget
    if payload.max_players is not None:
        team.max_players = payload.max_players

    await db.commit()
    await db.refresh(team)
    return TeamOut.model_validate(team)


@router.delete("/events/{event_id}/teams/{team_id}", status_code=204)
async def delete_team(
    event_id: int,
    team_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a team from the event. Only allowed in draft status."""
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot delete team after event is published")

    result = await db.execute(select(Team).where(Team.id == team_id, Team.event_id == event_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # If team has a captain, remove their captain role and revert AuctionPlayer status
    if team.captain_id:
        cap_result = await db.execute(select(User).where(User.id == team.captain_id))
        cap_user = cap_result.scalar_one_or_none()
        if cap_user:
            cap_user.remove_role(ROLE_CAPTAIN)

        # Revert captain's AuctionPlayer status back to pending
        ap_res = await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.event_id == event_id,
                AuctionPlayer.player_id == team.captain_id
            )
        )
        ap = ap_res.scalar_one_or_none()
        if ap and ap.status == PlayerAuctionStatus.sold and ap.current_bid == 0:
            ap.status = PlayerAuctionStatus.pending
            ap.current_bidder_id = None

    # Delete all TeamPlayers for this team
    await db.execute(
        TeamPlayer.__table__.delete().where(TeamPlayer.team_id == team_id)
    )

    await db.delete(team)
    await db.commit()
    return None


@router.post("/events/{event_id}/invite-auctioneer", response_model=dict)
async def invite_auctioneer(
    event_id: int,
    payload: AuctioneerInvitePayload,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign or invite an auctioneer.
    - Existing user → grants role + assigns immediately.
    - Unknown email → sends invite link (same token flow as admin invite).
    """
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot change auctioneer after event is published")

    user_result = await db.execute(
        select(User).where(
            User.email == payload.email,
            User.deleted_at.is_(None),
            User.is_active == True
        )
    )
    ae_user = user_result.scalar_one_or_none()

    if ae_user:
        ae_user.add_role(ROLE_AUCTIONEER)
        event.auctioneer_id = ae_user.id
        await db.commit()
        await db.refresh(ae_user)
        # Email queued — will fire when organizer marks event ready
        return {
            "status": "assigned",
            "user": UserOut.model_validate(ae_user).model_dump(),
            "message": f"{ae_user.name} assigned as auctioneer",
        }
    else:
        token = secrets.token_urlsafe(32)
        redis = await get_redis()
        invite_data = {
            "email": payload.email,
            "event_id": event_id,
            "event_name": event.name,
            "role": "auctioneer",
        }
        await redis.setex(f"invite:{token}", INVITE_TTL_SECONDS, json.dumps(invite_data))
        await enqueue("task_send_organizer_invite", payload.email, event.name, token, "auctioneer")
        return {
            "status": "invited",
            "user": None,
            "message": f"Invitation sent to {payload.email}",
        }


@router.patch("/events/{event_id}/settings", response_model=AuctionEventOut)
async def update_settings(
    event_id: int,
    payload: EventSettingsUpdatePayload,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """
    Allow organizer to adjust the auction date/time, global budget, max players, and player base price for their event.
    """
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot change settings after event is published")
    if payload.scheduled_at is not None:
        event.scheduled_at = payload.scheduled_at
    if payload.team_budget is not None:
        event.team_budget = payload.team_budget
    if payload.team_max_players is not None:
        event.team_max_players = payload.team_max_players
    if payload.player_base_price is not None:
        event.player_base_price = payload.player_base_price

    if payload.no_timer is not None:
        event.no_timer = payload.no_timer

    # Also update all existing teams in this event
    if payload.team_budget is not None or payload.team_max_players is not None:
        teams_result = await db.execute(select(Team).where(Team.event_id == event_id))
        teams = teams_result.scalars().all()
        for t in teams:
            if payload.team_budget is not None:
                t.budget = payload.team_budget
            if payload.team_max_players is not None:
                t.max_players = payload.team_max_players
                
    # Update existing players' base prices if it changed
    if payload.player_base_price is not None:
        players_result = await db.execute(select(AuctionPlayer).where(AuctionPlayer.event_id == event_id))
        players = players_result.scalars().all()
        for p in players:
            p.base_price = payload.player_base_price

    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


@router.post("/events/{event_id}/logo", response_model=AuctionEventOut)
async def upload_event_logo(
    event_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a logo for the event."""
    event = await _get_event_for_organizer(event_id, current_user, db)
    
    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Read file content
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:  # 5MB limit
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")
    
    # Upload to S3
    url = upload_file(content, key_prefix=f"event-logos/{event_id}", content_type=file.content_type)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload logo. S3 may not be configured.")
    
    event.logo = url
    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


@router.delete("/events/{event_id}/logo", response_model=AuctionEventOut)
async def delete_event_logo(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """Remove the event logo."""
    event = await _get_event_for_organizer(event_id, current_user, db)
    event.logo = None
    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


@router.patch("/events/{event_id}/ready", response_model=AuctionEventOut)
async def mark_ready(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)

    # Validate readiness before allowing mark-ready
    teams_result = await db.execute(select(Team).where(Team.event_id == event_id))
    teams = teams_result.scalars().all()

    players_result = await db.execute(select(AuctionPlayer).where(AuctionPlayer.event_id == event_id))
    players = players_result.scalars().all()

    errors = []
    if not event.auctioneer_id:
        errors.append("Auctioneer not assigned")
    if len(teams) < 2:
        errors.append(f"Need at least 2 teams (have {len(teams)})")
    teams_without_captain = [t for t in teams if not t.captain_id]
    if teams_without_captain:
        names = ", ".join(t.name for t in teams_without_captain)
        errors.append(f"Teams missing captain: {names}")
    if len(players) == 0:
        errors.append("No players added yet")

    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    event.status = AuctionStatus.ready
    await db.commit()
    await db.refresh(event)

    # ── Bulk notifications now that event is confirmed ready ──────────────────
    # Players
    for ap in players:
        p_res = await db.execute(select(User).where(User.id == ap.player_id))
        p = p_res.scalar_one_or_none()
        if p:
            await enqueue("task_send_event_invitation", p.email, p.name, event.name, event.id, "player")

    # Captains
    for team in teams:
        if team.captain_id:
            c_res = await db.execute(select(User).where(User.id == team.captain_id))
            c = c_res.scalar_one_or_none()
            if c:
                await enqueue("task_send_event_invitation", c.email, c.name, event.name, event.id, "captain")

    # Auctioneer
    if event.auctioneer_id:
        ae_res = await db.execute(select(User).where(User.id == event.auctioneer_id))
        ae = ae_res.scalar_one_or_none()
        if ae:
            await enqueue("task_send_event_invitation", ae.email, ae.name, event.name, event.id, "auctioneer")

    return AuctionEventOut.model_validate(event)


@router.patch("/events/{event_id}/unpublish", response_model=AuctionEventOut)
async def unpublish_event(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """
    Move event back to draft.
    Only allowed while status is 'ready' (auction has not started yet).
    """
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.ready:
        raise HTTPException(status_code=400, detail="Only ready events can be unpublished")

    event.status = AuctionStatus.draft
    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


@router.post("/events/{event_id}/invite-player", response_model=dict)
async def invite_player(
    event_id: int,
    payload: PlayerInvitePayload,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """
    Invite a player to the event by email.
    - Domain must match event's allowed_domains (if set).
    - If user already exists → add them to the player pool directly.
    - If not registered → send invite email; on registration they'll be added.
    """
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot invite players after event is published")

    # Domain validation
    if event.allowed_domains:
        email_domain = payload.email.split("@")[1].lower()
        allowed = [d.lower().strip() for d in event.allowed_domains]
        if email_domain not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Email domain '{email_domain}' is not allowed. Allowed: {', '.join(allowed)}",
            )

    user_result = await db.execute(
        select(User).where(
            User.email == payload.email,
            User.deleted_at.is_(None),
            User.is_active == True
        )
    )
    user = user_result.scalar_one_or_none()

    if user:
        # Check if already added
        existing = await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.event_id == event_id,
                AuctionPlayer.player_id == user.id,
            )
        )
        if existing.scalar_one_or_none():
            return {"status": "already_added", "user": UserOut.model_validate(user).model_dump(), "message": f"{user.name} is already in the player pool"}

        count_result = await db.execute(select(AuctionPlayer).where(AuctionPlayer.event_id == event_id))
        count = len(count_result.scalars().all())

        ap = AuctionPlayer(
            event_id=event_id,
            player_id=user.id,
            base_price=event.player_base_price,
            auction_order=count + 1,
        )
        db.add(ap)
        await db.commit()
        email_service.send_event_invitation(user.email, user.name, event.name, event.id, "player")
        return {
            "status": "added",
            "user": UserOut.model_validate(user).model_dump(),
            "message": f"{user.name} added to player pool",
        }
    else:
        # Unknown email — send invite
        token = secrets.token_urlsafe(32)
        redis = await get_redis()
        invite_data = {
            "email": payload.email,
            "event_id": event_id,
            "event_name": event.name,
            "role": "player",
        }
        await redis.setex(f"invite:{token}", INVITE_TTL_SECONDS, json.dumps(invite_data))
        await enqueue("task_send_organizer_invite", payload.email, event.name, token, "player")
        return {
            "status": "invited",
            "user": None,
            "message": f"Invitation sent to {payload.email}",
        }


# ── Bid Slab endpoints ──


@router.get("/events/{event_id}/bid-slabs", response_model=list[BidSlabOut])
async def list_bid_slabs(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    await _get_event_for_organizer(event_id, current_user, db)
    result = await db.execute(
        select(BidSlab).where(BidSlab.event_id == event_id).order_by(BidSlab.sort_order)
    )
    return [BidSlabOut.model_validate(s) for s in result.scalars().all()]


@router.post("/events/{event_id}/bid-slabs", response_model=BidSlabOut, status_code=201)
async def create_bid_slab(
    event_id: int,
    payload: BidSlabCreate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify bid slabs after event is published")
    slab = BidSlab(
        event_id=event_id,
        min_value=payload.min_value,
        max_value=payload.max_value,
        increment=payload.increment,
        sort_order=payload.sort_order,
    )
    db.add(slab)
    await db.commit()
    await db.refresh(slab)
    return BidSlabOut.model_validate(slab)


@router.patch("/events/{event_id}/bid-slabs/{slab_id}", response_model=BidSlabOut)
async def update_bid_slab(
    event_id: int,
    slab_id: int,
    payload: BidSlabCreate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify bid slabs after event is published")
    result = await db.execute(select(BidSlab).where(BidSlab.id == slab_id, BidSlab.event_id == event_id))
    slab = result.scalar_one_or_none()
    if not slab:
        raise HTTPException(status_code=404, detail="Bid slab not found")
    slab.min_value = payload.min_value
    slab.max_value = payload.max_value
    slab.increment = payload.increment
    slab.sort_order = payload.sort_order
    await db.commit()
    await db.refresh(slab)
    return BidSlabOut.model_validate(slab)


@router.delete("/events/{event_id}/bid-slabs/{slab_id}", status_code=204)
async def delete_bid_slab(
    event_id: int,
    slab_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify bid slabs after event is published")
    result = await db.execute(select(BidSlab).where(BidSlab.id == slab_id, BidSlab.event_id == event_id))
    slab = result.scalar_one_or_none()
    if not slab:
        raise HTTPException(status_code=404, detail="Bid slab not found")
    await db.delete(slab)
    await db.commit()


# ── Player Category endpoints ──


@router.get("/events/{event_id}/categories", response_model=list[PlayerCategoryOut])
async def list_player_categories(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    await _get_event_for_organizer(event_id, current_user, db)
    result = await db.execute(
        select(PlayerCategory)
        .where(PlayerCategory.event_id == event_id)
        .order_by(PlayerCategory.auction_first.desc(), PlayerCategory.sort_order)
    )
    return [PlayerCategoryOut.model_validate(c) for c in result.scalars().all()]


@router.post("/events/{event_id}/categories", response_model=PlayerCategoryOut, status_code=201)
async def create_player_category(
    event_id: int,
    payload: PlayerCategoryCreate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify categories after event is published")
    cat = PlayerCategory(
        event_id=event_id,
        name=payload.name,
        base_price=payload.base_price,
        sort_order=payload.sort_order,
        auction_first=payload.auction_first,
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return PlayerCategoryOut.model_validate(cat)


@router.patch("/events/{event_id}/categories/{cat_id}", response_model=PlayerCategoryOut)
async def update_player_category(
    event_id: int,
    cat_id: int,
    payload: PlayerCategoryUpdate,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify categories after event is published")
    result = await db.execute(select(PlayerCategory).where(PlayerCategory.id == cat_id, PlayerCategory.event_id == event_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if payload.name is not None:
        cat.name = payload.name
    if payload.base_price is not None:
        cat.base_price = payload.base_price
    if payload.sort_order is not None:
        cat.sort_order = payload.sort_order
    if payload.auction_first is not None:
        cat.auction_first = payload.auction_first
    await db.commit()
    await db.refresh(cat)
    return PlayerCategoryOut.model_validate(cat)


@router.delete("/events/{event_id}/categories/{cat_id}", status_code=204)
async def delete_player_category(
    event_id: int,
    cat_id: int,
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot modify categories after event is published")
    result = await db.execute(select(PlayerCategory).where(PlayerCategory.id == cat_id, PlayerCategory.event_id == event_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    await db.delete(cat)
    await db.commit()


@router.post("/events/{event_id}/import-players", response_model=PlayerImportResult)
async def import_players_csv(
    event_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role(ROLE_ORGANIZER)),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk import players from a CSV file.

    Expected CSV columns (header required):
      name, email, password (optional), phone (optional),
      batting_rating (optional), bowling_rating (optional), fielding_rating (optional)

    Creates user accounts for any email not already registered, then adds them to
    the event's player pool. Skips players already in the pool.
    """
    event = await _get_event_for_organizer(event_id, current_user, db)
    if event.status != AuctionStatus.draft:
        raise HTTPException(status_code=400, detail="Cannot import players after event is published")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    try:
        reader = csv.DictReader(io.StringIO(text))
        required = {"name", "email"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise HTTPException(
                status_code=400,
                detail=f"CSV must have at least these columns: {', '.join(sorted(required))}",
            )

        imported = 0
        skipped = 0
        errors: list[str] = []

        for row_num, raw_row in enumerate(reader, start=2):
            row = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw_row.items()}
            name = row.get("name", "")
            email = (row.get("email") or "").lower()
            password = row.get("password") or "password123"
            phone = row.get("phone") or None

            try:
                batting = float(row.get("batting_rating") or 5.0)
                bowling = float(row.get("bowling_rating") or 5.0)
                fielding = float(row.get("fielding_rating") or 5.0)
            except ValueError:
                batting = bowling = fielding = 5.0

            if not name or not email:
                skipped += 1
                errors.append(f"Row {row_num}: name and email are required")
                continue

            # Domain validation
            if event.allowed_domains:
                domain = email.split("@")[1].lower() if "@" in email else ""
                allowed = [d.lower().strip() for d in event.allowed_domains]
                if domain not in allowed:
                    skipped += 1
                    errors.append(f"Row {row_num}: domain '{domain}' not allowed for {email}")
                    continue

            # Check existing user
            user_result = await db.execute(
                select(User).where(User.email == email, User.deleted_at.is_(None), User.is_active == True)
            )
            user = user_result.scalar_one_or_none()

            if not user:
                user = User(
                    name=name,
                    email=email,
                    phone=phone,
                    hashed_password=hash_password(password),
                    roles=[ROLE_PLAYER],
                    batting_rating=batting,
                    bowling_rating=bowling,
                    fielding_rating=fielding,
                    onboarded=False,
                )
                db.add(user)
                await db.flush()  # get user.id
            else:
                # Update ratings if provided in CSV
                if row.get("batting_rating"):
                    user.batting_rating = batting
                if row.get("bowling_rating"):
                    user.bowling_rating = bowling
                if row.get("fielding_rating"):
                    user.fielding_rating = fielding

            # Check if already in player pool
            existing_ap = await db.execute(
                select(AuctionPlayer).where(
                    AuctionPlayer.event_id == event_id,
                    AuctionPlayer.player_id == user.id,
                )
            )
            if existing_ap.scalar_one_or_none():
                skipped += 1
                errors.append(f"Row {row_num}: {email} already in player pool")
                continue

            count_result = await db.execute(select(AuctionPlayer).where(AuctionPlayer.event_id == event_id))
            count = len(count_result.scalars().all())

            ap = AuctionPlayer(
                event_id=event_id,
                player_id=user.id,
                base_price=event.player_base_price,
                auction_order=count + 1,
            )
            db.add(ap)
            imported += 1

        await db.commit()
        return PlayerImportResult(imported=imported, skipped=skipped, errors=errors)
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed: {str(exc)}")
