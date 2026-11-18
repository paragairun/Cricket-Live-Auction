# Add/Update these helpers & functions in backend/app/services/auction_service.py

async def _resolve_effective_base_price(ap: AuctionPlayer, db: AsyncSession) -> int:
    """Returns the player's assigned category base_price if present, otherwise ap.base_price."""
    if ap.category_id:
        cat_res = await db.execute(
            select(PlayerCategory).where(PlayerCategory.id == ap.category_id)
        )
        cat = cat_res.scalar_one_or_none()
        if cat and cat.base_price:
            if ap.base_price != cat.base_price:
                ap.base_price = cat.base_price
            return cat.base_price
    return ap.base_price


# 1) Inside place_bid(...): replace the `if amount <= ap.current_bid:` check with:
    is_opening_bid = ap.current_bidder_id is None
    if is_opening_bid:
        if amount < ap.current_bid:
            raise ValueError(f"Opening bid must be at least the base price ({ap.current_bid})")
    else:
        if amount <= ap.current_bid:
            raise ValueError(f"Bid must be higher than current bid ({ap.current_bid})")


# 2) Inside set_next_player(...): right before marking ap active, resolve category base price:
    effective_base = await _resolve_effective_base_price(ap, db)
    ap.base_price = effective_base
    ap.current_bid = effective_base
    ap.current_bidder_id = None
    ap.status = PlayerAuctionStatus.active


# 3) Inside get_auction_state(...): resolve category base_price for players and return bid_slabs & categories:
    slabs_result = await db.execute(
        select(BidSlab).where(BidSlab.event_id == event_id).order_by(BidSlab.sort_order)
    )
    slabs = slabs_result.scalars().all()

    cats_result = await db.execute(
        select(PlayerCategory).where(PlayerCategory.event_id == event_id).order_by(PlayerCategory.sort_order)
    )
    categories = cats_result.scalars().all()
    cat_base_map = {c.id: c.base_price for c in categories}

    # When serializing each player p in get_auction_state:
    # resolved_base = cat_base_map.get(p.category_id, p.base_price) if p.category_id else p.base_price
    # resolved_bid = resolved_base if (p.status == PlayerAuctionStatus.active and p.current_bidder_id is None) else p.current_bid


async def undo_last_bid(event_id: int, auction_player_id: int, db: AsyncSession) -> dict:
    """Reverts the latest bid on the active player (Bid Down)."""
    ap_res = await db.execute(select(AuctionPlayer).where(AuctionPlayer.id == auction_player_id))
    ap = ap_res.scalar_one_or_none()
    if not ap or ap.status != PlayerAuctionStatus.active:
        raise ValueError("Player is not active")

    effective_base = await _resolve_effective_base_price(ap, db)

    bids_res = await db.execute(
        select(Bid)
        .where(Bid.event_id == event_id, Bid.auction_player_id == auction_player_id)
        .order_by(Bid.id.desc())
    )
    bids = bids_res.scalars().all()
    if not bids:
        raise ValueError("No bids to undo")

    latest_bid = bids[0]
    await db.delete(latest_bid)

    if len(bids) > 1:
        prev_bid = bids[1]
        ap.current_bid = prev_bid.amount
        ap.current_bidder_id = prev_bid.captain_id
    else:
        ap.current_bid = effective_base
        ap.current_bidder_id = None

    await db.commit()
    await manager.broadcast(event_id, {
        "type": "new_bid",
        "auction_player_id": ap.id,
        "amount": ap.current_bid,
        "captain_id": ap.current_bidder_id,
    })
    return {"current_bid": ap.current_bid, "current_bidder_id": ap.current_bidder_id}


async def edit_current_bid(event_id: int, auction_player_id: int, new_amount: int, db: AsyncSession) -> dict:
    """Manually edits the current bid amount on the active player (Pencil Edit button)."""
    ap_res = await db.execute(select(AuctionPlayer).where(AuctionPlayer.id == auction_player_id))
    ap = ap_res.scalar_one_or_none()
    if not ap or ap.status != PlayerAuctionStatus.active:
        raise ValueError("Player is not active")
    if new_amount < 0:
        raise ValueError("Bid amount cannot be negative")

    if ap.current_bidder_id is not None:
        team_res = await db.execute(
            select(Team).where(Team.event_id == event_id, Team.captain_id == ap.current_bidder_id)
        )
        team = team_res.scalar_one_or_none()
        if team and (team.budget - team.spent) < new_amount:
            raise ValueError(f"{team.name} does not have enough remaining purse ({team.budget - team.spent})")

        bids_res = await db.execute(
            select(Bid)
            .where(Bid.event_id == event_id, Bid.auction_player_id == auction_player_id)
            .order_by(Bid.id.desc())
        )
        latest_bid = bids_res.scalars().first()
        if latest_bid:
            latest_bid.amount = new_amount

    ap.current_bid = new_amount
    if ap.current_bidder_id is None:
        ap.base_price = new_amount

    await db.commit()
    await manager.broadcast(event_id, {
        "type": "new_bid",
        "auction_player_id": ap.id,
        "amount": ap.current_bid,
        "captain_id": ap.current_bidder_id,
    })
    return {"current_bid": ap.current_bid, "current_bidder_id": ap.current_bidder_id}
