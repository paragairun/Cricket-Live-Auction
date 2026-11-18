import asyncio
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, WebSocketException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.core.database import get_db
from app.core.deps import get_current_user, require_role
from app.core.redis import get_redis
from app.core.security import decode_token
from app.models.user import User, ROLE_AUCTIONEER, ROLE_CAPTAIN
from app.models.auction import AuctionEvent, AuctionPlayer, AuctionStatus, PlayerAuctionStatus, Team
from app.schemas.auction import (
    AuctionPlayerOut,
    AuctionEventOut,
    BidCreate,
    BidOut,
    NextPlayerRequest,
    TeamOut,
)
from app.services import auction_service
from app.ws.manager import manager
from app.models.user import User, ROLE_AUCTIONEER, ROLE_CAPTAIN, ROLE_ORGANIZER
from app.models.auction import (
    AuctionEvent,
    AuctionPlayer,
    AuctionStatus,
    PlayerAuctionStatus,
    Team,
    TeamPlayer,
)

router = APIRouter(prefix="/auction", tags=["auction"])


async def _get_active_player(event_id: int, db: AsyncSession) -> AuctionPlayer | None:
    # Prefer Redis pointer when available to avoid ambiguity if bad historical data
    # has more than one row marked as active.
    redis = await get_redis()
    active_player_id = await redis.get(f"auction:{event_id}:active_player")
    if active_player_id:
        by_id = await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.id == int(active_player_id),
                AuctionPlayer.event_id == event_id,
            )
        )
        ap = by_id.scalar_one_or_none()
        if ap:
            return ap

    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.active,
        )
    )
    return result.scalars().first()


@router.get("/events/{event_id}", response_model=AuctionEventOut)
async def get_event(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return AuctionEventOut.model_validate(event)


@router.get("/events/{event_id}/state")
async def get_state(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await auction_service.get_auction_state(event_id, db)


@router.get("/events/{event_id}/summary")
async def get_summary(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status != AuctionStatus.completed:
        raise HTTPException(status_code=400, detail="Summary is available after auction completion")
    return await auction_service.get_auction_summary(event_id, db)


@router.get("/events/{event_id}/players", response_model=list[AuctionPlayerOut])
async def list_players(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    return [AuctionPlayerOut.model_validate(p) for p in result.scalars().all()]


@router.get("/events/{event_id}/players-info")
async def list_players_info(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Lightweight player info for captain/spectator views.
    """
    from app.models.user import User as UserModel

    result = await db.execute(
        select(AuctionPlayer, UserModel)
        .join(UserModel, UserModel.id == AuctionPlayer.player_id)
        .where(AuctionPlayer.event_id == event_id)
    )
    rows = result.all()
    return [
        {
            "auction_player_id": ap.id,
            "player_id": user.id,
            "name": user.name,
            "email": user.email,
            "profile_photo": user.profile_photo,
        }
        for ap, user in rows
    ]


@router.get("/events/{event_id}/teams", response_model=list[TeamOut])
async def list_teams(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Team).where(Team.event_id == event_id))
    return [TeamOut.model_validate(t) for t in result.scalars().all()]


@router.get("/events/{event_id}/my-team", response_model=TeamOut)
async def my_team(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_CAPTAIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).where(Team.event_id == event_id, Team.captain_id == current_user.id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="No team found for this captain")
    return TeamOut.model_validate(team)


@router.post("/events/{event_id}/start", response_model=AuctionEventOut)
async def start_auction(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_AUCTIONEER)),
    db: AsyncSession = Depends(get_db),
):
    try:
        event = await auction_service.start_auction(event_id, db)
        return AuctionEventOut.model_validate(event)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/events/{event_id}/pause", response_model=AuctionEventOut)
async def pause_auction(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_AUCTIONEER)),
    db: AsyncSession = Depends(get_db),
):
    try:
        event = await auction_service.pause_auction(event_id, db)
        return AuctionEventOut.model_validate(event)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/events/{event_id}/finish", response_model=AuctionEventOut)
async def finish_auction(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_AUCTIONEER)),
    db: AsyncSession = Depends(get_db),
):
    try:
        event = await auction_service.finish_auction(event_id, db)
        return AuctionEventOut.model_validate(event)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/events/{event_id}/next-player", response_model=AuctionPlayerOut)
async def next_player(
    event_id: int,
    payload: NextPlayerRequest,
    current_user: User = Depends(require_role(ROLE_AUCTIONEER)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event or event.status != AuctionStatus.active:
        raise HTTPException(status_code=400, detail="Auction is not active")

    try:
        ap = await auction_service.set_next_player(event_id, payload.player_id, db)
        return AuctionPlayerOut.model_validate(ap)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/events/{event_id}/bid", response_model=BidOut)
async def place_bid(
    event_id: int,
    payload: BidCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event or event.status != AuctionStatus.active:
        raise HTTPException(status_code=400, detail="Auction is not active")

    if current_user.has_role(ROLE_AUCTIONEER) or current_user.has_role(ROLE_ORGANIZER):
        if not payload.captain_id:
            raise HTTPException(
                status_code=400,
                detail="Auctioneer must specify which team/captain is bidding",
            )
        effective_captain_id = payload.captain_id
    elif current_user.has_role(ROLE_CAPTAIN):
        team_res = await db.execute(
            select(Team).where(
                Team.event_id == event_id, Team.captain_id == current_user.id
            )
        )
        if not team_res.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="You are not a captain in this event")
        effective_captain_id = current_user.id
    else:
        raise HTTPException(status_code=403, detail="Not authorized to bid")

    active_player = await _get_active_player(event_id, db)
    if not active_player:
        raise HTTPException(status_code=400, detail="No active player to bid on")

    try:
        bid = await auction_service.place_bid(
            event_id, active_player.id, effective_captain_id, payload.amount, db
        )
        return BidOut.model_validate(bid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/events/{event_id}/hammer")
async def hammer(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_AUCTIONEER)),
    db: AsyncSession = Depends(get_db),
):
    active_player = await _get_active_player(event_id, db)
    if not active_player:
        raise HTTPException(status_code=400, detail="No active player")

    await auction_service.hammer_player(event_id, active_player.id, db)
    return {"detail": "hammered"}


@router.post("/events/{event_id}/re-auction-all-unsold")
async def re_auction_all_unsold(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_AUCTIONEER)),
    db: AsyncSession = Depends(get_db),
):
    """
    Return all unsold players to the pending pool so they can be
    re-auctioned via 'Random Next' or picked individually.
    """
    result = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status != AuctionStatus.active:
        raise HTTPException(status_code=400, detail="Auction is not active")

    # Don't re-auction while a player is currently being bid on
    active_player = await _get_active_player(event_id, db)
    if active_player:
        raise HTTPException(status_code=400, detail="Cannot re-auction while a player is active")

    # Reset all unsold players to pending
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.unsold,
        )
    )
    unsold = result.scalars().all()

    count = len(unsold)
    for ap in unsold:
        ap.status = PlayerAuctionStatus.pending

    await db.commit()

    return {
        "detail": f"{count} unsold player(s) returned to the pool",
        "count": count,
    }


# WebSocket endpoint
@router.websocket("/ws/{event_id}")
async def websocket_endpoint(event_id: int, websocket: WebSocket, token: str = ""):
    # Validate event exists before accepting connection

    async with AsyncSessionLocal() as db:
        event_result = await db.execute(
            select(AuctionEvent).where(AuctionEvent.id == event_id)
        )
        event = event_result.scalar_one_or_none()
        if not event:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    # Validate token for WS connections
    user_id = decode_token(token) if token else None

    await manager.connect(event_id, websocket, user_id)
    
    # Heartbeat task to detect dead connections
    heartbeat_task = None
    
    async def send_heartbeat():
        """Send ping every 30 seconds to keep connection alive and detect dead clients."""
        try:
            while True:
                await asyncio.sleep(30)
                await websocket.send_json({"type": "ping"})
        except Exception:
            pass
    
    try:
        # Start heartbeat task
        heartbeat_task = asyncio.create_task(send_heartbeat())
        
        # Send current state on connect
        async with AsyncSessionLocal() as db:
            state = await auction_service.get_auction_state(event_id, db)
        await manager.send_personal(websocket, state)

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_text()
            # Clients can send ping/pong to keep alive
    except WebSocketDisconnect:
        pass
    except Exception as e:
        # Log unexpected errors but don't crash
        print(f"WebSocket error for event {event_id}: {e}")
    finally:
        # Always clean up - cancel heartbeat and disconnect
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        await manager.disconnect(event_id, websocket)


@router.get("/events/{event_id}/viewer-stats")
async def get_viewer_stats(event_id: int, db: AsyncSession = Depends(get_db)):
    """Get live and total unique viewer counts for an event.
    For completed events, returns persisted count from database.
    """
    # Check if event is completed - use persisted DB count
    result = await db.execute(
        select(AuctionEvent).where(AuctionEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    
    if event and event.status == AuctionStatus.completed:
        # Return persisted count for completed events
        return {
            "live_viewers": 0,
            "total_unique_viewers": event.total_viewers or 0,
        }
    
    # For active/non-completed events, get from Redis
    stats = await manager.get_viewer_stats(event_id)
    return stats

@router.post("/events/{event_id}/reset-mock-auction", response_model=dict)
async def reset_mock_auction(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Reset all players, team budgets, rosters, Redis active pointers, and event status
    back to ready state. Allowed for the event's organizer or assigned auctioneer.
    """
    event_res = await db.execute(select(AuctionEvent).where(AuctionEvent.id == event_id))
    event = event_res.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    is_organizer = event.organizer_id == current_user.id
    is_assigned_auctioneer = event.auctioneer_id == current_user.id
    if not (is_organizer or is_assigned_auctioneer or current_user.has_role(ROLE_ORGANIZER)):
        raise HTTPException(status_code=403, detail="Not authorized to reset this auction")

    # 1. Reset event status back to ready so auctioneer can start fresh
    event.status = AuctionStatus.ready

    # 2. Reset all AuctionPlayers back to pending
    players_res = await db.execute(select(AuctionPlayer).where(AuctionPlayer.event_id == event_id))
    players = players_res.scalars().all()
    for ap in players:
        ap.status = PlayerAuctionStatus.pending
        ap.current_bid = 0
        ap.current_bidder_id = None

    # 3. Clear all TeamPlayers (sold player rosters)
    await db.execute(
        TeamPlayer.__table__.delete().where(
            TeamPlayer.team_id.in_(
                select(Team.id).where(Team.event_id == event_id)
            )
        )
    )

    # 4. Reset all Team budgets and re-add captains
    teams_res = await db.execute(select(Team).where(Team.event_id == event_id))
    teams = teams_res.scalars().all()
    for team in teams:
        team.budget = event.team_budget
        if team.captain_id:
            db.add(TeamPlayer(team_id=team.id, player_id=team.captain_id, sold_price=0))

            cap_ap_res = await db.execute(
                select(AuctionPlayer).where(
                    AuctionPlayer.event_id == event_id,
                    AuctionPlayer.player_id == team.captain_id,
                )
            )
            cap_ap = cap_ap_res.scalar_one_or_none()
            if cap_ap:
                cap_ap.status = PlayerAuctionStatus.sold
                cap_ap.current_bid = 0
                cap_ap.current_bidder_id = team.captain_id

    await db.commit()

    # 5. Clear Redis active player pointer & broadcast fresh state to all WebSockets
    redis = await get_redis()
    await redis.delete(f"auction:{event_id}:active_player")

    fresh_state = await auction_service.get_auction_state(event_id, db)
    await manager.broadcast(event_id, fresh_state)

    return {
        "status": "success",
        "message": "Mock auction reset! All bids cleared, budgets restored, and screens synced.",
    }