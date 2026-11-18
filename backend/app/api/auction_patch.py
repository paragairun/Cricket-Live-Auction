# Add/Update these endpoints in backend/app/api/auction.py

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


@router.post("/events/{event_id}/bid-down")
async def bid_down(
    event_id: int,
    current_user: User = Depends(require_role(ROLE_AUCTIONEER)),
    db: AsyncSession = Depends(get_db),
):
    """Revert the last bid on the active player (Bid Down / Undo)."""
    active_player = await _get_active_player(event_id, db)
    if not active_player:
        raise HTTPException(status_code=400, detail="No active player")
    try:
        state = await auction_service.undo_last_bid(event_id, active_player.id, db)
        return state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/events/{event_id}/bid-edit")
async def edit_current_bid(
    event_id: int,
    payload: BidUpdateAmount,
    current_user: User = Depends(require_role(ROLE_AUCTIONEER)),
    db: AsyncSession = Depends(get_db),
):
    """Manually override/edit the current bid amount on the active player."""
    active_player = await _get_active_player(event_id, db)
    if not active_player:
        raise HTTPException(status_code=400, detail="No active player")
    try:
        state = await auction_service.edit_current_bid(event_id, active_player.id, payload.amount, db)
        return state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
