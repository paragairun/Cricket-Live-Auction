"""Regression tests for correcting an incorrectly assigned sale."""
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.auction import (
    AuctionAuditLog,
    AuditAction,
    AuctionPlayer,
    AuctionStatus,
    PlayerAuctionStatus,
    Bid,
    Team,
    TeamPlayer,
)
from app.services import auction_service
from app.services.auction_service import AuctionError


async def test_reverse_sale_refunds_team_and_immediately_reopens_player(
    make_event, make_team, register_player, make_slab
):
    event, _admin, _organizer, auctioneer = await make_event(status=AuctionStatus.active)
    team = await make_team(event, name="Wrong Team")
    _player, ap = await register_player(event, name="Wrongly assigned", base_price=10000)
    await make_slab(event, min_value=0, max_value=0, increment=1000)

    async with AsyncSessionLocal() as session:
        row = await session.get(AuctionPlayer, ap.id)
        row.status = PlayerAuctionStatus.active
        row.current_bid = row.base_price
        await session.commit()
    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team.id, 11000, session)
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session, actor=auctioneer)

    async with AsyncSessionLocal() as session:
        reversed_player = await auction_service.reverse_sale_and_reauction(
            event.id, ap.id, session, actor=auctioneer
        )
        assert reversed_player.status == PlayerAuctionStatus.active
        assert reversed_player.current_bid == 10000
        assert reversed_player.current_bid_team_id is None
        assert reversed_player.version == 3

    async with AsyncSessionLocal() as session:
        team_row = await session.get(Team, team.id)
        roster = (await session.execute(
            select(TeamPlayer).where(TeamPlayer.event_id == event.id)
        )).scalars().all()
        bids = (await session.execute(
            select(Bid).where(Bid.event_id == event.id, Bid.auction_player_id == ap.id)
        )).scalars().all()
        audits = (await session.execute(
            select(AuctionAuditLog).where(
                AuctionAuditLog.event_id == event.id,
                AuctionAuditLog.auction_player_id == ap.id,
                AuctionAuditLog.action == AuditAction.sale_reversed,
            )
        )).scalars().all()

    assert team_row.spent == 0
    assert roster == []
    assert len(bids) == 1 and bids[0].reverted_at is not None
    assert len(audits) == 1
    assert audits[0].team_id == team.id and audits[0].amount == 11000


async def test_reverse_sale_is_blocked_while_another_player_is_active(
    make_event, make_team, register_player
):
    event, _admin, _organizer, auctioneer = await make_event(status=AuctionStatus.active)
    team = await make_team(event, name="Wrong Team")
    _sold_player, sold_ap = await register_player(event, name="Sold player", base_price=10000)
    _active_player, active_ap = await register_player(event, name="Current player", base_price=5000)

    async with AsyncSessionLocal() as session:
        sold_row = await session.get(AuctionPlayer, sold_ap.id)
        sold_row.status = PlayerAuctionStatus.sold
        sold_row.current_bid = 12000
        sold_row.current_bid_team_id = team.id
        active_row = await session.get(AuctionPlayer, active_ap.id)
        active_row.status = PlayerAuctionStatus.active
        session.add(TeamPlayer(
            event_id=event.id, team_id=team.id, player_id=sold_ap.player_id, sold_price=12000
        ))
        team_row = await session.get(Team, team.id)
        team_row.spent = 12000
        await session.commit()

    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="another player is on the block"):
            await auction_service.reverse_sale_and_reauction(
                event.id, sold_ap.id, session, actor=auctioneer
            )

    async with AsyncSessionLocal() as session:
        team_row = await session.get(Team, team.id)
        sold_row = await session.get(AuctionPlayer, sold_ap.id)
    assert team_row.spent == 12000
    assert sold_row.status == PlayerAuctionStatus.sold
