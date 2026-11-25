"""Live auction endpoints.

Version: 2.0.0 (2026-11-20)

Changed from 1.x:
  - Every operating endpoint now depends on `require_event_access`, which loads
    the event and checks the caller's role ON THAT EVENT. Previously they used
    `require_role(ROLE_AUCTIONEER)`, which only checked the global roles array —
    so any user holding the auctioneer role anywhere could hammer, pause, finish,
    re-pick players or rewrite bids on ANY event on the platform. `reset-mock-
    auction` was worse still: it accepted any user with the organizer role, with
    no guard against running mid-auction.
  - The WebSocket rejects a missing or invalid token. It used to declare
    `token: str = ""`, resolve an invalid token to None, accept the connection
    anyway and immediately push full auction state — every team's budget, every
    bid — to anyone who knew an event id.
  - Bids are placed for a TEAM by an operator. The captain bidding path is gone.
"""
import asyncio
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db
from app.core.deps import (
    AUCTION_OPERATORS,
    EventContext,
    authorize_event_socket,
    get_current_user,
    require_event_access,
)
from app.models.auction import (
    AuctionAuditLog,
    AuctionEvent,
    AuctionPlayer,
    AuctionStatus,
    PlayerAuctionStatus,
    Team,
)
from app.models.user import User
from app.schemas.auction import (
    AuctionEventOut,
    AuctionPlayerOut,
    AuditLogOut,
    BidCorrection,
    BidCreate,
    BidOut,
    HammerRequest,
    NextPlayerRequest,
    NominationCreate,
    NominationOut,
    RtmRaiseRequest,
    TeamOut,
)
from app.services import auction_service
from app.services.auction_service import AuctionError, ConcurrentUpdate
from app.services.rtm_service import RtmConflict, RtmError
from app.ws.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auction", tags=["auction"])

# Operator-only dependency, reused across every mutating endpoint below.
OperatorCtx = Depends(require_event_access(*AUCTION_OPERATORS))
# Any authenticated user, with their event-scoped roles resolved.
ViewerCtx = Depends(require_event_access())


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, (ConcurrentUpdate, RtmConflict)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ── Read ──────────────────────────────────────────────────────────────────────


@router.get("/events/{event_id}", response_model=AuctionEventOut)
async def get_event(ctx: EventContext = ViewerCtx):
    return AuctionEventOut.model_validate(ctx.event)


@router.get("/events/{event_id}/state")
async def get_state(
    event_id: int,
    ctx: EventContext = ViewerCtx,
    db: AsyncSession = Depends(get_db),
):
    return await auction_service.get_auction_state(event_id, db)


@router.get("/events/{event_id}/summary")
async def get_summary(
    event_id: int,
    ctx: EventContext = ViewerCtx,
    db: AsyncSession = Depends(get_db),
):
    if ctx.event.status != AuctionStatus.completed and not ctx.is_operator:
        raise HTTPException(
            status_code=400, detail="Summary is available once the auction is complete"
        )
    return await auction_service.get_auction_summary(event_id, db)


@router.get("/events/{event_id}/players", response_model=list[AuctionPlayerOut])
async def list_players(
    event_id: int,
    ctx: EventContext = ViewerCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    return [AuctionPlayerOut.model_validate(p) for p in result.scalars().all()]


@router.get("/events/{event_id}/players-info")
async def list_players_info(
    event_id: int,
    ctx: EventContext = ViewerCtx,
    db: AsyncSession = Depends(get_db),
):
    """Lightweight roster for the auctioneer console and spectator view."""
    result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    return [
        {
            "auction_player_id": ap.id,
            "player_id": ap.player_id,
            "name": ap.player.name if ap.player else f"Player #{ap.player_id}",
            "profile_photo": ap.player.profile_photo if ap.player else None,
            "batting_rating": ap.player.batting_rating if ap.player else None,
            "bowling_rating": ap.player.bowling_rating if ap.player else None,
            "fielding_rating": ap.player.fielding_rating if ap.player else None,
            "base_price": ap.base_price,
            "category_id": ap.category_id,
            "status": ap.status,
        }
        for ap in result.scalars().all()
    ]


@router.get("/events/{event_id}/teams", response_model=list[TeamOut])
async def list_teams(
    event_id: int,
    ctx: EventContext = ViewerCtx,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Team).where(Team.event_id == event_id))
    return [TeamOut.model_validate(t) for t in result.scalars().all()]


@router.get("/events/{event_id}/audit", response_model=list[AuditLogOut])
async def list_audit_log(
    event_id: int,
    limit: int = 200,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Every operator action on this event, newest first. New in 2.0.0."""
    result = await db.execute(
        select(AuctionAuditLog)
        .where(AuctionAuditLog.event_id == event_id)
        .order_by(AuctionAuditLog.id.desc())
        .limit(min(limit, 1000))
    )
    return [AuditLogOut.model_validate(row) for row in result.scalars().all()]


# ── Lifecycle ─────────────────────────────────────────────────────────────────


@router.post("/events/{event_id}/start", response_model=AuctionEventOut)
async def start_auction(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    try:
        event = await auction_service.start_auction(event_id, db, actor=ctx.user)
        return AuctionEventOut.model_validate(event)
    except AuctionError as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/pause", response_model=AuctionEventOut)
async def pause_auction(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    try:
        event = await auction_service.pause_auction(event_id, db, actor=ctx.user)
        return AuctionEventOut.model_validate(event)
    except AuctionError as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/finish", response_model=AuctionEventOut)
async def finish_auction(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    try:
        event = await auction_service.finish_auction(event_id, db, actor=ctx.user)
        return AuctionEventOut.model_validate(event)
    except AuctionError as exc:
        raise _handle(exc)


# ── Running the auction ───────────────────────────────────────────────────────


@router.post("/events/{event_id}/next-player", response_model=AuctionPlayerOut)
async def next_player(
    event_id: int,
    payload: NextPlayerRequest,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    try:
        ap = await auction_service.set_next_player(
            event_id,
            payload.auction_player_id,
            db,
            actor=ctx.user,
            idempotency_key=payload.idempotency_key,
        )
        return AuctionPlayerOut.model_validate(ap)
    except AuctionError as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/pull-player", response_model=AuctionPlayerOut)
async def pull_player(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Take the current player off the block without selling. New in 2.0.0."""
    try:
        ap = await auction_service.pull_player(event_id, db, actor=ctx.user)
        return AuctionPlayerOut.model_validate(ap)
    except AuctionError as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/bid", response_model=BidOut)
async def place_bid(
    event_id: int,
    payload: BidCreate,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Place a bid on behalf of a team.

    Bids are entered by an operator at the auction. There is no captain bidding
    path: captains are roster players, not system actors.
    """
    try:
        bid = await auction_service.place_bid(
            event_id,
            payload.team_id,
            payload.amount,
            db,
            actor=ctx.user,
            idempotency_key=payload.idempotency_key,
            expected_version=payload.expected_version,
        )
        return BidOut.model_validate(bid)
    except AuctionError as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/hammer")
async def hammer(
    event_id: int,
    payload: HammerRequest | None = None,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    redis_key = payload.idempotency_key if payload else None
    active = await auction_service._load_active_player(db, event_id)
    if not active:
        raise HTTPException(status_code=400, detail="No player is on the block")
    try:
        ap = await auction_service.hammer_player(
            event_id, active.id, db, actor=ctx.user, idempotency_key=redis_key
        )
    except AuctionError as exc:
        raise _handle(exc)
    return {
        "detail": "hammered",
        "status": ap.status if ap else None,
        "sold_price": ap.current_bid if ap else None,
        "sold_to_team_id": ap.current_bid_team_id if ap else None,
    }


@router.post("/events/{event_id}/bid-down")
async def bid_down(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Revert the last bid. The bid row is marked reverted, never deleted."""
    try:
        return await auction_service.undo_last_bid(event_id, db, actor=ctx.user)
    except AuctionError as exc:
        raise _handle(exc)


@router.patch("/events/{event_id}/bid-edit")
async def correct_bid(
    event_id: int,
    payload: BidCorrection,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await auction_service.correct_current_bid(
            event_id, payload.amount, db, actor=ctx.user
        )
    except AuctionError as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/re-auction-all-unsold")
async def re_auction_all_unsold(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Return unsold players to the pending pool for a second round."""
    if ctx.event.status != AuctionStatus.active:
        raise HTTPException(status_code=400, detail="Auction is not active")

    active = await auction_service._load_active_player(db, event_id)
    if active:
        raise HTTPException(
            status_code=400, detail="Cannot re-auction while a player is on the block"
        )

    pending_result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.pending,
        )
    )
    pending = pending_result.scalars().all()
    captain_ids = await auction_service._captain_player_ids(db, event_id)
    if any(player.player_id not in captain_ids for player in pending):
        raise HTTPException(status_code=409, detail="Auction all eligible players before re-auctioning unsold players")

    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.unsold,
        )
    )
    unsold = result.scalars().all()
    if not unsold:
        raise HTTPException(status_code=400, detail="There are no unsold players to re-auction")
    for ap in unsold:
        ap.status = PlayerAuctionStatus.pending
        ap.version += 1
        ap.extension_count = 0
    ctx.event.unsold_reauction_started = True
    await db.commit()

    state = await auction_service.get_auction_state(event_id, db)
    await manager.broadcast(event_id, state)

    return {"detail": f"{len(unsold)} player(s) returned to the pool", "count": len(unsold)}


@router.post("/events/{event_id}/reset")
async def reset_auction(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Clear all bids, rosters and purses and return the event to `ready`.

    1.x exposed this as `reset-mock-auction` guarded only by the global
    organizer role, so any organizer on the platform could wipe any event — and
    nothing stopped it running mid-auction.
    """
    # Reset returns a LIVE (active/paused) or READY event back to READY so the
    # organiser can re-draft. A completed auction is a record and is not reset.
    if ctx.event.status == AuctionStatus.completed:
        raise HTTPException(
            status_code=400,
            detail="Cannot reset a completed auction",
        )
    # A live (active) auction can be reset straight back to ready; the service
    # already returns every player to pending and clears rosters and purses.
    try:
        await auction_service.reset_auction(event_id, db, actor=ctx.user)
    except AuctionError as exc:
        raise _handle(exc)
    return {"status": "success", "message": "Auction reset. Budgets and rosters cleared."}


# ── Viewer stats ──────────────────────────────────────────────────────────────


@router.get("/events/{event_id}/viewer-stats")
async def get_viewer_stats(
    event_id: int,
    ctx: EventContext = ViewerCtx,
):
    """Requires authentication — 1.x had no auth dependency on this at all."""
    if ctx.event.status == AuctionStatus.completed:
        return {"live_viewers": 0, "total_unique_viewers": ctx.event.total_viewers or 0}
    return await manager.get_viewer_stats(event_id)


# ── WebSocket ─────────────────────────────────────────────────────────────────


@router.websocket("/ws/{event_id}")
async def websocket_endpoint(event_id: int, websocket: WebSocket, token: str = ""):
    """Live auction feed.

    A valid token for a user permitted to see this event is mandatory. 1.x
    accepted anonymous connections and pushed the full state to them.
    """
    async with AsyncSessionLocal() as db:
        user = await authorize_event_socket(event_id, token, db)

    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(event_id, websocket, user.id)

    heartbeat_task: asyncio.Task | None = None

    async def send_heartbeat():
        """Server ping every 25s. The client replies and runs its own watchdog."""
        try:
            while True:
                await asyncio.sleep(25)
                await websocket.send_json({"type": "ping", "server_time": asyncio.get_event_loop().time()})
        except Exception:
            pass

    try:
        heartbeat_task = asyncio.create_task(send_heartbeat())

        async with AsyncSessionLocal() as db:
            state = await auction_service.get_auction_state(event_id, db)
        await manager.send_personal(websocket, state)

        while True:
            raw = await websocket.receive_text()
            # Clients send {"type":"pong"} or {"type":"resync"}.
            if '"resync"' in raw:
                async with AsyncSessionLocal() as db:
                    state = await auction_service.get_auction_state(event_id, db)
                await manager.send_personal(websocket, state)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.info("WebSocket closed for event %s: %s", event_id, exc)
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        await manager.disconnect(event_id, websocket)


# ── Right to Match (new in 2.1.0) ────────────────────────────────────────────
#
# Each step is its own endpoint so the console can only offer the decision that
# is actually available, and every transition lands in the audit log.


@router.get("/events/{event_id}/rtm")
async def get_rtm(
    event_id: int,
    ctx: EventContext = ViewerCtx,
    db: AsyncSession = Depends(get_db),
):
    """The pending Right-to-Match negotiation, or null."""
    return await auction_service.get_rtm_state(event_id, db)


@router.post("/events/{event_id}/rtm/exercise")
async def rtm_exercise(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Rights holder exercises. The top bidder then gets one chance to raise."""
    try:
        return await auction_service.rtm_exercise(event_id, db, actor=ctx.user)
    except (AuctionError, RtmError) as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/rtm/decline", response_model=AuctionPlayerOut)
async def rtm_decline(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Rights holder passes. The player goes to the top bidder at their bid."""
    try:
        ap = await auction_service.rtm_decline(event_id, db, actor=ctx.user)
        return AuctionPlayerOut.model_validate(ap)
    except (AuctionError, RtmError) as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/rtm/raise")
async def rtm_raise(
    event_id: int,
    payload: RtmRaiseRequest,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """The top bidder's single raise. The rights holder must then match it."""
    try:
        return await auction_service.rtm_raise(event_id, payload.amount, db, actor=ctx.user)
    except (AuctionError, RtmError) as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/rtm/skip-raise", response_model=AuctionPlayerOut)
async def rtm_skip_raise(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """The top bidder stands pat. The rights holder takes the player at the bid."""
    try:
        ap = await auction_service.rtm_skip_raise(event_id, db, actor=ctx.user)
        return AuctionPlayerOut.model_validate(ap)
    except (AuctionError, RtmError) as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/rtm/match", response_model=AuctionPlayerOut)
async def rtm_match(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """The rights holder matches the raised figure and takes the player."""
    try:
        ap = await auction_service.rtm_match(event_id, db, actor=ctx.user)
        return AuctionPlayerOut.model_validate(ap)
    except (AuctionError, RtmError) as exc:
        raise _handle(exc)


@router.post("/events/{event_id}/rtm/no-match", response_model=AuctionPlayerOut)
async def rtm_no_match(
    event_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """The rights holder will not match. The top bidder keeps the player."""
    try:
        ap = await auction_service.rtm_no_match(event_id, db, actor=ctx.user)
        return AuctionPlayerOut.model_validate(ap)
    except (AuctionError, RtmError) as exc:
        raise _handle(exc)


# ── Nomination round (new in 2.1.0) ──────────────────────────────────────────


@router.get("/events/{event_id}/nominations", response_model=list[NominationOut])
async def list_nominations(
    event_id: int,
    ctx: EventContext = ViewerCtx,
    db: AsyncSession = Depends(get_db),
):
    """The queue, oldest first — the order players will come up."""
    rows = await auction_service.list_nominations(event_id, db)
    return [NominationOut.model_validate(r) for r in rows]


@router.post("/events/{event_id}/nominations", response_model=NominationOut, status_code=201)
async def nominate_player(
    event_id: int,
    payload: NominationCreate,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    """Record a team's nomination of who comes up next."""
    try:
        nomination = await auction_service.nominate_player(
            event_id, payload.team_id, payload.auction_player_id, db, actor=ctx.user
        )
    except AuctionError as exc:
        raise _handle(exc)

    rows = await auction_service.list_nominations(event_id, db)
    match = next((r for r in rows if r["id"] == nomination.id), None)
    return NominationOut.model_validate(
        match
        or {
            "id": nomination.id,
            "auction_player_id": nomination.auction_player_id,
            "team_id": nomination.team_id,
        }
    )


@router.delete("/events/{event_id}/nominations/{nomination_id}", status_code=204)
async def withdraw_nomination(
    event_id: int,
    nomination_id: int,
    ctx: EventContext = OperatorCtx,
    db: AsyncSession = Depends(get_db),
):
    try:
        await auction_service.withdraw_nomination(event_id, nomination_id, db, actor=ctx.user)
    except AuctionError as exc:
        raise _handle(exc)
