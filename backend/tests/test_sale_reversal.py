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


@pytest.mark.parametrize("current_outcome", ["sold", "no_bid"])
async def test_reverse_sale_queues_during_live_lot_and_auto_activates_after_finalisation(
    make_event, make_team, register_player, current_outcome
):
    from datetime import datetime, timedelta, timezone

    event, _admin, _organizer, auctioneer = await make_event(status=AuctionStatus.active)
    event.auto_advance = False  # A queued correction must advance even when normal auto-advance is off.
    wrong_team = await make_team(event, name="Wrong Team")
    winning_team = await make_team(event, name="Current Winner")
    _sold_player, sold_ap = await register_player(event, name="Queued player", base_price=10000)
    _current_player, current_ap = await register_player(event, name="Current lot", base_price=5000)

    async with AsyncSessionLocal() as session:
        sold_row = await session.get(AuctionPlayer, sold_ap.id)
        sold_row.status = PlayerAuctionStatus.sold
        sold_row.current_bid = 12000
        sold_row.current_bid_team_id = wrong_team.id
        active_row = await session.get(AuctionPlayer, current_ap.id)
        active_row.status = PlayerAuctionStatus.active
        active_row.current_bid = 7000
        active_row.current_bid_team_id = winning_team.id if current_outcome == "sold" else None
        session.add(TeamPlayer(
            event_id=event.id, team_id=wrong_team.id,
            player_id=sold_ap.player_id, sold_price=12000
        ))
        wrong_team_row = await session.get(Team, wrong_team.id)
        wrong_team_row.spent = 12000
        await session.commit()

    async with AsyncSessionLocal() as session:
        queued_player = await auction_service.reverse_sale_and_reauction(
            event.id, sold_ap.id, session, actor=auctioneer
        )
        assert queued_player.status == PlayerAuctionStatus.queued
        assert queued_player.current_bid == 10000
        assert queued_player.current_bid_team_id is None
        assert await session.get(AuctionPlayer, current_ap.id) is not None

    async with AsyncSessionLocal() as session:
        wrong_team_row = await session.get(Team, wrong_team.id)
        current_event = await session.get(type(event), event.id)
        current_event.advance_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        assert wrong_team_row.spent == 0

    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, current_ap.id, session, actor=auctioneer)

    async with AsyncSessionLocal() as session:
        current_event = await session.get(type(event), event.id)
        current_event.advance_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        await auction_service.run_due_hammers(session)

    async with AsyncSessionLocal() as session:
        queued_row = await session.get(AuctionPlayer, sold_ap.id)
        current_row = await session.get(AuctionPlayer, current_ap.id)
        current_event = await session.get(type(event), event.id)

    assert current_row.status == (
        PlayerAuctionStatus.sold if current_outcome == "sold" else PlayerAuctionStatus.unsold
    )
    assert queued_row.status == PlayerAuctionStatus.active
    assert current_event.deadline_at is not None or event.no_timer
