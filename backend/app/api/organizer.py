"""Organizer endpoints: players, teams, slabs, categories, event settings.

Version: 2.0.0 (2026-11-20)

Changed from 1.x:
  - Players are Player rows, not User rows. Creating a player needs a name and
    nothing else — no email, no password, no invitation. 1.x required a unique
    email per player because players were forced through the User table, which
    meant inventing addresses for people in a room who never log in.
  - Team captains are players (Team.captain_id -> players.id) and are no longer
    granted a login or a role.
  - Authorization is per-event via require_event_access. 1.x used
    require_role(ROLE_ORGANIZER) plus an ad-hoc ownership check in some handlers
    and none in others.
  - Full edit and delete across tournaments, teams, players, slabs and
    categories, with guards so nothing is destroyed mid-auction.
"""
import csv
import io
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import (
    EVENT_ADMIN,
    EVENT_ORGANIZER,
    EventContext,
    get_current_user,
    require_event_access,
    require_role,
)
from app.core.queue import enqueue
from app.core.redis import get_redis
from app.models.auction import (
    AuctionEvent,
    AuctionMode,
    AuctionPlayer,
    AuctionStatus,
    Bid,
    BidSlab,
    Player,
    PlayerAuctionStatus,
    PlayerCategory,
    Team,
    TeamPlayer,
)
from app.models.user import ROLE_ADMIN, ROLE_AUCTIONEER, ROLE_ORGANIZER, User
from app.schemas.auction import (
    AuctionEventOut,
    AuctionPlayerOut,
    AuctionPlayerUpdate,
    BidSlabCreate,
    BidSlabOut,
    BidSlabUpdate,
    PlayerCategoryCreate,
    PlayerCategoryOut,
    PlayerCategoryUpdate,
    TeamCreate,
    TeamOut,
    TeamUpdate,
)
from app.schemas.player import (
    PlayerCreate,
    PlayerImportResult,
    PlayerOut,
    PlayerUpdate,
    PlayerWithAuctionState,
)
from app.schemas.user import UserOut
from app.services.s3_service import save_upload

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    HAS_OPENPYXL = True
except ImportError:  # pragma: no cover
    HAS_OPENPYXL = False

logger = logging.getLogger(__name__)

INVITE_TTL_SECONDS = 60 * 60 * 24 * 7

router = APIRouter(prefix="/organizer", tags=["organizer"])

# Organizer or admin, scoped to the event in the path.
SetupCtx = Depends(require_event_access(EVENT_ORGANIZER, EVENT_ADMIN))


async def _resolve_base_price(
    db: AsyncSession,
    event: AuctionEvent,
    category_id: int | None,
    requested: int | None = None,
) -> int:
    """The authoritative base price for a player.

    A category's price governs. When a player is in a category, the per-player
    figure and the event default are both out of the picture entirely — the
    whole point of a category is that everyone in it starts at the same number.

    Only when there is no category (or the category has no price set) does a
    per-player figure apply, falling back to the event default.
    """
    if category_id:
        result = await db.execute(
            select(PlayerCategory).where(
                PlayerCategory.id == category_id,
                PlayerCategory.event_id == event.id,
            )
        )
        category = result.scalar_one_or_none()
        if not category:
            raise HTTPException(
                status_code=400, detail="Category not found in this event"
            )
        if category.base_price and category.base_price > 0:
            return category.base_price

    if requested is not None and requested > 0:
        return requested
    return event.player_base_price


def _guard_not_live(ctx: EventContext, what: str) -> None:
    """Block destructive setup changes while bidding is in progress."""
    if ctx.event.status == AuctionStatus.active:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot {what} while the auction is running. Pause it first.",
        )


class AuctioneerInvitePayload(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()


class EventSettingsUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    scheduled_at: datetime | None = None
    team_budget: int | None = Field(default=None, ge=1000)
    team_max_players: int | None = Field(default=None, ge=1, le=50)
    team_min_players: int | None = Field(default=None, ge=0, le=50)
    player_base_price: int | None = Field(default=None, ge=100)
    no_timer: bool | None = None
    auto_advance: bool | None = None
    timer_seconds: int | None = Field(default=None, ge=5, le=600)
    soft_close_threshold_seconds: int | None = Field(default=None, ge=0, le=120)
    soft_close_extension_seconds: int | None = Field(default=None, ge=1, le=120)
    # New in 2.1.0
    auction_mode: AuctionMode | None = None
    rtm_enabled: bool | None = None
    rtm_cards_per_team: int | None = Field(default=None, ge=0, le=20)
    exclude_captains_from_auction: bool | None = None
    auto_reauction_unsold: bool | None = None


# ── Users (for assigning an auctioneer) ───────────────────────────────────────


@router.get("/users/search", response_model=list[UserOut])
async def search_users(
    q: str = Query(..., min_length=1),
    current_user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    term = f"%{q.lower()}%"
    result = await db.execute(
        select(User)
        .where(
            User.deleted_at.is_(None),
            or_(User.email.ilike(term), User.name.ilike(term)),
        )
        .limit(20)
    )
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.get("/events", response_model=list[AuctionEventOut])
async def list_my_events(
    current_user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    query = select(AuctionEvent).where(AuctionEvent.deleted_at.is_(None))
    if not current_user.has_role(ROLE_ADMIN):
        query = query.where(AuctionEvent.organizer_id == current_user.id)
    result = await db.execute(query.order_by(AuctionEvent.created_at.desc()))
    return [AuctionEventOut.model_validate(e) for e in result.scalars().all()]


# ── Players ───────────────────────────────────────────────────────────────────


def _player_with_state(
    player: Player,
    ap: AuctionPlayer | None,
    sale: TeamPlayer | None = None,
) -> PlayerWithAuctionState:
    return PlayerWithAuctionState(
        id=player.id,
        name=player.name,
        email=player.email,
        phone=player.phone,
        age=player.age,
        profile_photo=player.profile_photo,
        notes=player.notes,
        batting_rating=player.batting_rating,
        bowling_rating=player.bowling_rating,
        fielding_rating=player.fielding_rating,
        created_at=player.created_at,
        auction_player_id=ap.id if ap else None,
        base_price=ap.base_price if ap else None,
        category_id=ap.category_id if ap else None,
        status=ap.status if ap else None,
        sold_price=sale.sold_price if sale else None,
        sold_to_team_id=sale.team_id if sale else None,
    )


@router.post(
    "/events/{event_id}/players",
    response_model=PlayerWithAuctionState,
    status_code=201,
)
async def add_player(
    event_id: int,
    payload: PlayerCreate,
    base_price: int | None = None,
    category_id: int | None = None,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    """Create a roster player and register them in this event.

    Only `name` is required. No account is created and no email is sent.
    """
    _guard_not_live(ctx, "add players")

    player = Player(
        name=payload.name.strip(),
        email=payload.email,
        phone=payload.phone,
        age=payload.age,
        notes=payload.notes,
        batting_rating=payload.batting_rating,
        bowling_rating=payload.bowling_rating,
        fielding_rating=payload.fielding_rating,
        created_by_user_id=ctx.user.id,
    )
    db.add(player)
    await db.flush()

    resolved_base = await _resolve_base_price(db, ctx.event, category_id, base_price)

    ap = AuctionPlayer(
        event_id=event_id,
        player_id=player.id,
        base_price=resolved_base,
        category_id=category_id,
    )
    db.add(ap)
    await db.commit()
    await db.refresh(player)
    await db.refresh(ap)

    return _player_with_state(player, ap)


@router.get("/events/{event_id}/players", response_model=list[PlayerWithAuctionState])
async def list_event_players(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuctionPlayer, Player)
        .join(Player, Player.id == AuctionPlayer.player_id)
        .where(AuctionPlayer.event_id == event_id, Player.deleted_at.is_(None))
        .order_by(AuctionPlayer.auction_order, Player.name)
    )
    rows = result.all()

    sales_result = await db.execute(
        select(TeamPlayer).where(TeamPlayer.event_id == event_id)
    )
    sales = {tp.player_id: tp for tp in sales_result.scalars().all()}

    return [_player_with_state(p, ap, sales.get(p.id)) for ap, p in rows]


@router.patch(
    "/events/{event_id}/players/{auction_player_id}",
    response_model=PlayerWithAuctionState,
)
async def edit_player(
    event_id: int,
    auction_player_id: int,
    payload: PlayerUpdate,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    """Edit a player's details. Allowed mid-auction only for players not yet sold."""
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.id == auction_player_id, AuctionPlayer.event_id == event_id
        )
    )
    ap = result.scalar_one_or_none()
    if not ap:
        raise HTTPException(status_code=404, detail="Player not found in this event")
    if ap.status == PlayerAuctionStatus.sold:
        raise HTTPException(status_code=409, detail="Cannot edit a player already sold")
    if ap.status == PlayerAuctionStatus.active:
        raise HTTPException(status_code=409, detail="Cannot edit a player on the block")

    player_result = await db.execute(select(Player).where(Player.id == ap.player_id))
    player = player_result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player record missing")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(player, field, value)

    await db.commit()
    await db.refresh(player)
    return _player_with_state(player, ap)


@router.patch(
    "/events/{event_id}/players/{auction_player_id}/auction-settings",
    response_model=AuctionPlayerOut,
)
async def edit_player_auction_settings(
    event_id: int,
    auction_player_id: int,
    payload: AuctionPlayerUpdate,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    """Change a player's base price, category or running order for this event."""
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.id == auction_player_id, AuctionPlayer.event_id == event_id
        )
    )
    ap = result.scalar_one_or_none()
    if not ap:
        raise HTTPException(status_code=404, detail="Player not found in this event")
    if ap.status in (PlayerAuctionStatus.sold, PlayerAuctionStatus.active):
        raise HTTPException(
            status_code=409, detail="Cannot change auction settings for this player now"
        )

    data = payload.model_dump(exclude_unset=True)

    # Right to Match rights: the team entitled to reclaim this player. Must be a
    # team in this event. New in 2.1.0.
    if "rtm_team_id" in data and data["rtm_team_id"] is not None:
        holder = await db.execute(
            select(Team).where(
                Team.id == data["rtm_team_id"], Team.event_id == event_id
            )
        )
        if not holder.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail="RTM rights must belong to a team in this event"
            )

    for field, value in data.items():
        setattr(ap, field, value)

    # Recompute whenever either input changed. The edit form carries whatever
    # base price was last stored, so without this a player moved into a
    # category would keep the old figure — which is exactly the bug: assigning
    # "Diamond (1000000)" left the player sitting at a base price of 1000.
    if "category_id" in data or "base_price" in data:
        ap.base_price = await _resolve_base_price(
            db,
            ctx.event,
            ap.category_id,
            data.get("base_price") if "base_price" in data else ap.base_price,
        )

    ap.version += 1

    await db.commit()
    await db.refresh(ap)
    return AuctionPlayerOut.model_validate(ap)


@router.delete("/events/{event_id}/players/{auction_player_id}", status_code=204)
async def remove_player(
    event_id: int,
    auction_player_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    """Remove a player from this event. Refuses if they have already been sold."""
    _guard_not_live(ctx, "remove players")

    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.id == auction_player_id, AuctionPlayer.event_id == event_id
        )
    )
    ap = result.scalar_one_or_none()
    if not ap:
        raise HTTPException(status_code=404, detail="Player not found in this event")
    if ap.status == PlayerAuctionStatus.sold:
        raise HTTPException(
            status_code=409,
            detail="This player has been sold. Reset the auction to undo the sale first.",
        )

    captain_result = await db.execute(
        select(Team).where(Team.event_id == event_id, Team.captain_id == ap.player_id)
    )
    captain_of = captain_result.scalars().first()
    if captain_of:
        raise HTTPException(
            status_code=409,
            detail=f"This player is captain of {captain_of.name}. Change that first.",
        )

    await db.execute(Bid.__table__.delete().where(Bid.auction_player_id == ap.id))
    await db.delete(ap)
    await db.commit()


@router.post("/events/{event_id}/players/{auction_player_id}/photo", response_model=PlayerOut)
async def upload_player_photo(
    event_id: int,
    auction_player_id: int,
    file: UploadFile = File(...),
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.id == auction_player_id, AuctionPlayer.event_id == event_id
        )
    )
    ap = result.scalar_one_or_none()
    if not ap:
        raise HTTPException(status_code=404, detail="Player not found in this event")

    player_result = await db.execute(select(Player).where(Player.id == ap.player_id))
    player = player_result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player record missing")

    url = await save_upload(file, folder="players")
    player.profile_photo = url
    await db.commit()
    await db.refresh(player)
    return PlayerOut.model_validate(player)


@router.post("/events/{event_id}/import-players", response_model=PlayerImportResult)
async def import_players_csv(
    event_id: int,
    file: UploadFile = File(...),
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    """Bulk-create players from CSV.

    `name` is the only required column. Recognised optional columns: email,
    phone, age, batting_rating, bowling_rating, fielding_rating, category,
    base_price. 1.x required email on every row and created a User per player.
    """
    _guard_not_live(ctx, "import players")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded CSV")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no header row")

    headers = {h.strip().lower(): h for h in reader.fieldnames}
    if "name" not in headers:
        raise HTTPException(status_code=400, detail="CSV must have a 'name' column")

    cats_result = await db.execute(
        select(PlayerCategory).where(PlayerCategory.event_id == event_id)
    )
    categories = {c.name.strip().lower(): c for c in cats_result.scalars().all()}

    imported = 0
    skipped = 0
    errors: list[str] = []

    def get(row: dict, key: str):
        col = headers.get(key)
        if not col:
            return None
        value = (row.get(col) or "").strip()
        return value or None

    def as_float(value, default=5.0):
        try:
            return max(0.0, min(10.0, float(value))) if value else default
        except (TypeError, ValueError):
            return default

    for line_no, row in enumerate(reader, start=2):
        name = get(row, "name")
        if not name:
            skipped += 1
            errors.append(f"Row {line_no}: missing name")
            continue

        category = None
        category_name = get(row, "category")
        if category_name:
            category = categories.get(category_name.lower())
            if not category:
                errors.append(
                    f"Row {line_no}: unknown category '{category_name}' — player added without one"
                )

        requested_base = None
        raw_base = get(row, "base_price")
        if raw_base:
            try:
                requested_base = int(float(raw_base))
            except ValueError:
                errors.append(f"Row {line_no}: invalid base_price '{raw_base}' — ignored")
        if raw_base and category and category.base_price > 0:
            errors.append(
                f"Row {line_no}: base_price ignored — category "
                f"'{category.name}' sets it to {category.base_price}"
            )
        base_price = await _resolve_base_price(
            db, ctx.event, category.id if category else None, requested_base
        )

        age = None
        raw_age = get(row, "age")
        if raw_age:
            try:
                age = int(float(raw_age))
            except ValueError:
                errors.append(f"Row {line_no}: invalid age '{raw_age}' — left blank")

        player = Player(
            name=name,
            email=(get(row, "email") or "").lower() or None,
            phone=get(row, "phone"),
            age=age,
            batting_rating=as_float(get(row, "batting_rating")),
            bowling_rating=as_float(get(row, "bowling_rating")),
            fielding_rating=as_float(get(row, "fielding_rating")),
            created_by_user_id=ctx.user.id,
        )
        db.add(player)
        await db.flush()

        db.add(
            AuctionPlayer(
                event_id=event_id,
                player_id=player.id,
                base_price=base_price,
                category_id=category.id if category else None,
            )
        )
        imported += 1

    await db.commit()
    return PlayerImportResult(imported=imported, skipped=skipped, errors=errors[:50])


@router.get("/events/{event_id}/players/export-xlsx")
async def export_players_xlsx(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    if not HAS_OPENPYXL:
        raise HTTPException(status_code=501, detail="openpyxl is not installed")

    result = await db.execute(
        select(AuctionPlayer, Player)
        .join(Player, Player.id == AuctionPlayer.player_id)
        .where(AuctionPlayer.event_id == event_id)
        .order_by(Player.name)
    )
    rows = result.all()

    teams_result = await db.execute(select(Team).where(Team.event_id == event_id))
    teams = {t.id: t.name for t in teams_result.scalars().all()}

    sales_result = await db.execute(select(TeamPlayer).where(TeamPlayer.event_id == event_id))
    sales = {tp.player_id: tp for tp in sales_result.scalars().all()}

    wb = Workbook()
    ws = wb.active
    ws.title = "Players"

    headers = [
        "Name", "Phone", "Email", "Age", "Batting", "Bowling", "Fielding",
        "Base Price", "Status", "Sold Price", "Team",
    ]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F2937")
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for ap, player in rows:
        sale = sales.get(player.id)
        ws.append([
            player.name,
            player.phone or "",
            player.email or "",
            player.age or "",
            player.batting_rating,
            player.bowling_rating,
            player.fielding_rating,
            ap.base_price,
            ap.status.value if hasattr(ap.status, "value") else str(ap.status),
            sale.sold_price if sale else "",
            teams.get(sale.team_id, "") if sale else "",
        ])

    for col in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 16
    ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"players-event-{event_id}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Teams ─────────────────────────────────────────────────────────────────────


@router.post("/events/{event_id}/teams", response_model=TeamOut, status_code=201)
async def create_team(
    event_id: int,
    payload: TeamCreate,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    _guard_not_live(ctx, "create teams")

    if payload.captain_id is not None:
        await _validate_captain(db, event_id, payload.captain_id)

    team = Team(
        event_id=event_id,
        name=payload.name.strip(),
        color=payload.color,
        captain_id=payload.captain_id,
        budget=payload.budget if payload.budget is not None else ctx.event.team_budget,
        max_players=(
            payload.max_players
            if payload.max_players is not None
            else ctx.event.team_max_players
        ),
    )
    db.add(team)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f"A team named '{payload.name}' already exists in this event"
        )
    await db.refresh(team)
    return TeamOut.model_validate(team)


async def _validate_captain(db: AsyncSession, event_id: int, player_id: int) -> None:
    """A captain must be a player registered in this event."""
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id, AuctionPlayer.player_id == player_id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="A captain must be a player registered in this event",
        )
    existing = await db.execute(
        select(Team).where(Team.event_id == event_id, Team.captain_id == player_id)
    )
    other = existing.scalars().first()
    if other:
        raise HTTPException(
            status_code=409, detail=f"That player already captains {other.name}"
        )


@router.get("/events/{event_id}/teams", response_model=list[TeamOut])
async def list_teams(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Team).where(Team.event_id == event_id))
    return [TeamOut.model_validate(t) for t in result.scalars().all()]


@router.patch("/events/{event_id}/teams/{team_id}", response_model=TeamOut)
async def update_team(
    event_id: int,
    team_id: int,
    payload: TeamUpdate,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).where(Team.id == team_id, Team.event_id == event_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    data = payload.model_dump(exclude_unset=True)

    if "captain_id" in data and data["captain_id"] != team.captain_id:
        _guard_not_live(ctx, "change a team captain")
        if data["captain_id"] is not None:
            await _validate_captain(db, event_id, data["captain_id"])

    if "budget" in data and data["budget"] is not None and data["budget"] < team.spent:
        raise HTTPException(
            status_code=400,
            detail=f"Budget cannot be below what the team has already spent ({team.spent})",
        )

    if "max_players" in data and data["max_players"] is not None:
        if data["max_players"] < len(team.players or []):
            raise HTTPException(
                status_code=400,
                detail=f"Team already has {len(team.players)} players",
            )

    for field, value in data.items():
        setattr(team, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A team with that name already exists")
    await db.refresh(team)
    return TeamOut.model_validate(team)


@router.delete("/events/{event_id}/teams/{team_id}", status_code=204)
async def delete_team(
    event_id: int,
    team_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    _guard_not_live(ctx, "delete teams")

    result = await db.execute(
        select(Team).where(Team.id == team_id, Team.event_id == event_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if team.players:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{team.name} has {len(team.players)} player(s). "
                "Reset the auction before deleting this team."
            ),
        )

    await db.execute(Bid.__table__.delete().where(Bid.team_id == team.id))
    await db.delete(team)
    await db.commit()


# ── Event settings ────────────────────────────────────────────────────────────


@router.patch("/events/{event_id}/settings", response_model=AuctionEventOut)
async def update_settings(
    event_id: int,
    payload: EventSettingsUpdatePayload,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    event = ctx.event
    data = payload.model_dump(exclude_unset=True)

    if event.status == AuctionStatus.active:
        # Timing can be tuned live; money and squad rules cannot.
        locked = {
            "team_budget", "team_max_players", "team_min_players", "player_base_price",
        }
        attempted = locked & data.keys()
        if attempted:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot change {', '.join(sorted(attempted))} while the auction is running",
            )

    if "team_min_players" in data and data["team_min_players"] is not None:
        max_players = data.get("team_max_players", event.team_max_players)
        if data["team_min_players"] > max_players:
            raise HTTPException(
                status_code=400, detail="Minimum squad cannot exceed maximum squad"
            )

    for field, value in data.items():
        setattr(event, field, value)

    # Keep team budgets in step with the event budget during setup.
    if "team_budget" in data and data["team_budget"] is not None:
        teams_result = await db.execute(select(Team).where(Team.event_id == event_id))
        for team in teams_result.scalars().all():
            if team.spent == 0:
                team.budget = data["team_budget"]

    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


@router.post("/events/{event_id}/logo", response_model=AuctionEventOut)
async def upload_event_logo(
    event_id: int,
    file: UploadFile = File(...),
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    ctx.event.logo = await save_upload(file, folder="event-logos")
    await db.commit()
    await db.refresh(ctx.event)
    return AuctionEventOut.model_validate(ctx.event)


@router.delete("/events/{event_id}/logo", response_model=AuctionEventOut)
async def delete_event_logo(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    ctx.event.logo = None
    await db.commit()
    await db.refresh(ctx.event)
    return AuctionEventOut.model_validate(ctx.event)


@router.patch("/events/{event_id}/ready", response_model=AuctionEventOut)
async def mark_ready(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    event = ctx.event
    if event.status not in (AuctionStatus.draft, AuctionStatus.ready):
        raise HTTPException(status_code=400, detail=f"Event is already {event.status}")

    teams_result = await db.execute(select(Team).where(Team.event_id == event_id))
    teams = teams_result.scalars().all()
    players_result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    players = players_result.scalars().all()

    problems = []
    if len(teams) < 2:
        problems.append("at least two teams")
    if not event.auctioneer_id:
        problems.append("an assigned auctioneer")
    if len(players) < len(teams):
        problems.append("more players than teams")
    if problems:
        raise HTTPException(
            status_code=400, detail="Event needs " + ", ".join(problems)
        )

    event.status = AuctionStatus.ready
    await db.commit()
    await db.refresh(event)
    return AuctionEventOut.model_validate(event)


@router.patch("/events/{event_id}/unpublish", response_model=AuctionEventOut)
async def unpublish_event(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    if ctx.event.status != AuctionStatus.ready:
        raise HTTPException(status_code=400, detail="Only a ready event can be unpublished")
    ctx.event.status = AuctionStatus.draft
    await db.commit()
    await db.refresh(ctx.event)
    return AuctionEventOut.model_validate(ctx.event)


@router.post("/events/{event_id}/invite-auctioneer", response_model=dict)
async def invite_auctioneer(
    event_id: int,
    payload: AuctioneerInvitePayload,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    """Assign an auctioneer. They are a User — auctioneers do log in."""
    result = await db.execute(
        select(User).where(User.email == payload.email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    if user:
        user.add_role(ROLE_AUCTIONEER)
        ctx.event.auctioneer_id = user.id
        await db.commit()
        await enqueue(
            "task_send_event_invitation",
            user.email, user.name, ctx.event.name, event_id, "auctioneer",
        )
        return {"detail": "Auctioneer assigned", "user_id": user.id}

    token = secrets.token_urlsafe(32)
    redis = await get_redis()
    await redis.set(
        f"invite:{token}",
        f"{payload.email}|{event_id}|auctioneer",
        ex=INVITE_TTL_SECONDS,
    )
    await enqueue("task_send_organizer_invite", payload.email, ctx.event.name, token, "auctioneer")
    return {"detail": "Invitation sent", "pending": True}


# ── Bid slabs ─────────────────────────────────────────────────────────────────


@router.get("/events/{event_id}/bid-slabs", response_model=list[BidSlabOut])
async def list_bid_slabs(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BidSlab).where(BidSlab.event_id == event_id).order_by(BidSlab.sort_order)
    )
    return [BidSlabOut.model_validate(s) for s in result.scalars().all()]


@router.post("/events/{event_id}/bid-slabs", response_model=BidSlabOut, status_code=201)
async def create_bid_slab(
    event_id: int,
    payload: BidSlabCreate,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    if payload.max_value and payload.max_value <= payload.min_value:
        raise HTTPException(status_code=400, detail="max_value must exceed min_value")

    slab = BidSlab(event_id=event_id, **payload.model_dump())
    db.add(slab)
    await db.commit()
    await db.refresh(slab)
    return BidSlabOut.model_validate(slab)


@router.patch("/events/{event_id}/bid-slabs/{slab_id}", response_model=BidSlabOut)
async def update_bid_slab(
    event_id: int,
    slab_id: int,
    payload: BidSlabUpdate,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BidSlab).where(BidSlab.id == slab_id, BidSlab.event_id == event_id)
    )
    slab = result.scalar_one_or_none()
    if not slab:
        raise HTTPException(status_code=404, detail="Bid slab not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(slab, field, value)
    if slab.max_value and slab.max_value <= slab.min_value:
        raise HTTPException(status_code=400, detail="max_value must exceed min_value")

    await db.commit()
    await db.refresh(slab)
    return BidSlabOut.model_validate(slab)


@router.delete("/events/{event_id}/bid-slabs/{slab_id}", status_code=204)
async def delete_bid_slab(
    event_id: int,
    slab_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BidSlab).where(BidSlab.id == slab_id, BidSlab.event_id == event_id)
    )
    slab = result.scalar_one_or_none()
    if not slab:
        raise HTTPException(status_code=404, detail="Bid slab not found")
    await db.delete(slab)
    await db.commit()


# ── Player categories ─────────────────────────────────────────────────────────


@router.get("/events/{event_id}/categories", response_model=list[PlayerCategoryOut])
async def list_player_categories(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PlayerCategory)
        .where(PlayerCategory.event_id == event_id)
        .order_by(PlayerCategory.sort_order)
    )
    return [PlayerCategoryOut.model_validate(c) for c in result.scalars().all()]


@router.post("/events/{event_id}/categories", response_model=PlayerCategoryOut, status_code=201)
async def create_player_category(
    event_id: int,
    payload: PlayerCategoryCreate,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    category = PlayerCategory(event_id=event_id, **payload.model_dump())
    db.add(category)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail=f"A category named '{payload.name}' already exists"
        )
    await db.refresh(category)
    return PlayerCategoryOut.model_validate(category)


@router.patch("/events/{event_id}/categories/{cat_id}", response_model=PlayerCategoryOut)
async def update_player_category(
    event_id: int,
    cat_id: int,
    payload: PlayerCategoryUpdate,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PlayerCategory).where(
            PlayerCategory.id == cat_id, PlayerCategory.event_id == event_id
        )
    )
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(category, field, value)

    # The category's price governs every player in it, so a change here has to
    # reach them. Without this the roster keeps showing the old figure and the
    # two drift apart. Players already sold or on the block are left alone —
    # their price is settled.
    repriced = 0
    if "base_price" in data and category.base_price and category.base_price > 0:
        members = await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.event_id == event_id,
                AuctionPlayer.category_id == cat_id,
                AuctionPlayer.status.in_(
                    [PlayerAuctionStatus.pending, PlayerAuctionStatus.unsold]
                ),
            )
        )
        for ap in members.scalars().all():
            if ap.base_price != category.base_price:
                ap.base_price = category.base_price
                ap.version += 1
                repriced += 1

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A category with that name already exists")
    await db.refresh(category)

    if repriced:
        logger.info(
            "Category %s reprice: %s player(s) moved to %s",
            category.name, repriced, category.base_price,
        )
    return PlayerCategoryOut.model_validate(category)


@router.delete("/events/{event_id}/categories/{cat_id}", status_code=204)
async def delete_player_category(
    event_id: int,
    cat_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    _guard_not_live(ctx, "delete categories")

    result = await db.execute(
        select(PlayerCategory).where(
            PlayerCategory.id == cat_id, PlayerCategory.event_id == event_id
        )
    )
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    # Detach players rather than orphaning a foreign key.
    players_result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id, AuctionPlayer.category_id == cat_id
        )
    )
    for ap in players_result.scalars().all():
        ap.category_id = None
        # With no category, the event default applies again.
        if ap.status in (PlayerAuctionStatus.pending, PlayerAuctionStatus.unsold):
            ap.base_price = ctx.event.player_base_price
            ap.version += 1

    await db.delete(category)
    await db.commit()

@router.delete(
    "/events/{event_id}/players/{auction_player_id}/photo",
    response_model=PlayerOut,
)
async def delete_player_photo(
    event_id: int,
    auction_player_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.id == auction_player_id, AuctionPlayer.event_id == event_id
        )
    )
    ap = result.scalar_one_or_none()
    if not ap:
        raise HTTPException(status_code=404, detail="Player not found in this event")

    player_result = await db.execute(select(Player).where(Player.id == ap.player_id))
    player = player_result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player record missing")

    player.profile_photo = None
    await db.commit()
    await db.refresh(player)
    return PlayerOut.model_validate(player)


@router.post("/events/{event_id}/teams/{team_id}/logo", response_model=TeamOut)
async def upload_team_logo(
    event_id: int,
    team_id: int,
    file: UploadFile = File(...),
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    _guard_not_live(ctx, "update team logos")

    result = await db.execute(
        select(Team).where(Team.id == team_id, Team.event_id == event_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    team.logo = await save_upload(file, folder="team-logos")
    await db.commit()
    await db.refresh(team)
    return TeamOut.model_validate(team)


@router.delete("/events/{event_id}/teams/{team_id}/logo", response_model=TeamOut)
async def delete_team_logo(
    event_id: int,
    team_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    _guard_not_live(ctx, "update team logos")

    result = await db.execute(
        select(Team).where(Team.id == team_id, Team.event_id == event_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    team.logo = None
    await db.commit()
    await db.refresh(team)
    return TeamOut.model_validate(team)


@router.post("/events/{event_id}/background", response_model=AuctionEventOut)
async def upload_event_background(
    event_id: int,
    file: UploadFile = File(...),
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    ctx.event.background_image = await save_upload(file, folder="event-backgrounds")
    await db.commit()
    await db.refresh(ctx.event)
    return AuctionEventOut.model_validate(ctx.event)


@router.delete("/events/{event_id}/background", response_model=AuctionEventOut)
async def delete_event_background(
    event_id: int,
    ctx: EventContext = SetupCtx,
    db: AsyncSession = Depends(get_db),
):
    ctx.event.background_image = None
    await db.commit()
    await db.refresh(ctx.event)
    return AuctionEventOut.model_validate(ctx.event)