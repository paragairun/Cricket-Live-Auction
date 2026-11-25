"""Tests for the money path: bidding, hammering, budgets and authorization.

New in 2.0.0 (2026-11-20). The 1.x suite had 12 tests and not one of them
touched the auction engine — place_bid, hammer_player, the timer, budget
enforcement and concurrency were all untested, which is where every P0 defect
turned out to live.

The first test in this file is the regression guard for the lost-update race:
it fails against the 1.x engine and passes against this one.
"""
import asyncio

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.auction import (
    AuctionEvent,
    AuctionPlayer,
    AuctionStatus,
    Bid,
    PlayerAuctionStatus,
    Team,
    TeamPlayer,
)
from app.services import auction_service
from app.services.auction_service import AuctionError, ConcurrentUpdate


async def _activate(event, ap):
    """Put a player on the block without going through the HTTP layer."""
    async with AsyncSessionLocal() as session:
        row = await session.get(AuctionPlayer, ap.id)
        row.status = PlayerAuctionStatus.active
        row.current_bid = row.base_price
        row.current_bid_team_id = None
        await session.commit()
        await session.refresh(row)
        return row


# ── The race ──────────────────────────────────────────────────────────────────


async def test_concurrent_bids_produce_exactly_one_winner(
    make_event, make_team, register_player, make_slab
):
    """Two teams bidding the same amount at the same instant: one must lose.

    Against 1.x both bids were accepted, two Bid rows existed at the same price,
    and the player went to whichever transaction committed last.
    """
    event, _admin, _org, _auctioneer = await make_event(status=AuctionStatus.active)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team_a = await make_team(event, name="Team A")
    team_b = await make_team(event, name="Team B")
    _player, ap = await register_player(event, name="Contested", base_price=10000)
    ap = await _activate(event, ap)

    # Both bidders read the same version, then both try to write.
    async def bid(team_id: int):
        async with AsyncSessionLocal() as session:
            try:
                await auction_service.place_bid(
                    event.id,
                    team_id,
                    11000,
                    session,
                    expected_version=ap.version,
                    idempotency_key=f"race-{team_id}",
                )
                return "accepted"
            except ConcurrentUpdate:
                return "rejected"
            except AuctionError as exc:
                return f"error: {exc}"

    results = await asyncio.gather(bid(team_a.id), bid(team_b.id))

    assert results.count("accepted") == 1, (
        f"Exactly one bid must win, got: {results}"
    )
    assert results.count("rejected") == 1, (
        f"The losing bid must be told the price moved, got: {results}"
    )

    async with AsyncSessionLocal() as session:
        bids = (
            await session.execute(select(Bid).where(Bid.auction_player_id == ap.id))
        ).scalars().all()
        row = await session.get(AuctionPlayer, ap.id)

    assert len(bids) == 1, f"Only one bid should be recorded, found {len(bids)}"
    assert row.current_bid == 11000
    assert row.current_bid_team_id == bids[0].team_id


async def test_stale_expected_version_is_rejected(
    make_event, make_team, register_player, make_slab
):
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team_a = await make_team(event, name="A")
    team_b = await make_team(event, name="B")
    _p, ap = await register_player(event, base_price=10000)
    ap = await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team_a.id, 11000, session)

    # Team B still holds the version from before A's bid.
    async with AsyncSessionLocal() as session:
        with pytest.raises(ConcurrentUpdate):
            await auction_service.place_bid(
                event.id, team_b.id, 12000, session, expected_version=ap.version
            )


# ── Idempotency ───────────────────────────────────────────────────────────────


async def test_replayed_bid_key_does_not_place_a_second_bid(
    make_event, make_team, register_player, make_slab
):
    """A retry from a flaky console must not bid twice."""
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team = await make_team(event, name="A")
    _p, ap = await register_player(event, base_price=10000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        first = await auction_service.place_bid(
            event.id, team.id, 11000, session, idempotency_key="same-key"
        )
    async with AsyncSessionLocal() as session:
        second = await auction_service.place_bid(
            event.id, team.id, 11000, session, idempotency_key="same-key"
        )

    assert first.id == second.id

    async with AsyncSessionLocal() as session:
        bids = (
            await session.execute(select(Bid).where(Bid.auction_player_id == ap.id))
        ).scalars().all()
    assert len(bids) == 1


async def test_double_hammer_sells_once(make_event, make_team, register_player):
    """Timer and operator hammering together must not double-charge the team.

    1.x guarded only on `status != active` with no lock, so both callers could
    read `active` and both apply the sale.
    """
    event, *_ = await make_event(status=AuctionStatus.active, team_budget=100000)
    team = await make_team(event, name="A")
    _p, ap = await register_player(event, base_price=10000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team.id, 11000, session)

    async def hammer():
        async with AsyncSessionLocal() as session:
            return await auction_service.hammer_player(event.id, ap.id, session)

    await asyncio.gather(hammer(), hammer())

    async with AsyncSessionLocal() as session:
        sales = (
            await session.execute(select(TeamPlayer).where(TeamPlayer.event_id == event.id))
        ).scalars().all()
        team_row = await session.get(Team, team.id)

    assert len(sales) == 1, f"Player sold {len(sales)} times"
    assert team_row.spent == 11000, f"Purse charged {team_row.spent}, expected 11000"


# ── Budget and squad rules ────────────────────────────────────────────────────


async def test_bid_cannot_exceed_remaining_purse(
    make_event, make_team, register_player, make_slab
):
    event, *_ = await make_event(status=AuctionStatus.active, team_budget=20000)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team = await make_team(event, name="A", budget=20000)
    _p, ap = await register_player(event, base_price=19000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="Insufficient purse"):
            await auction_service.place_bid(event.id, team.id, 25000, session)


async def test_minimum_squad_budget_is_reserved(
    make_event, make_team, register_player, make_slab
):
    """A team must keep enough back to fill its minimum squad.

    New rule in 2.0.0 — nothing in 1.x stopped a team spending its whole purse
    on two players and then being unable to field a side.
    """
    event, *_ = await make_event(
        status=AuctionStatus.active,
        team_budget=100000,
        team_min_players=5,
        player_base_price=1000,
    )
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team = await make_team(event, name="A", budget=100000)
    _p, ap = await register_player(event, base_price=10000)
    await _activate(event, ap)

    # 5 min squad, 0 bought, this player fills one -> must reserve 4 * 1000.
    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="minimum squad"):
            await auction_service.place_bid(event.id, team.id, 99000, session)

    # Just under the reserve is fine.
    async with AsyncSessionLocal() as session:
        bid = await auction_service.place_bid(event.id, team.id, 96000, session)
    assert bid.amount == 96000


async def test_team_cannot_outbid_itself(
    make_event, make_team, register_player, make_slab
):
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team = await make_team(event, name="A")
    _p, ap = await register_player(event, base_price=10000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team.id, 11000, session)
    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="already holds the top bid"):
            await auction_service.place_bid(event.id, team.id, 12000, session)


async def test_roster_capacity_is_enforced(
    make_event, make_team, register_player, make_slab
):
    event, *_ = await make_event(status=AuctionStatus.active, team_max_players=1)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team = await make_team(event, name="A", max_players=1)

    _p1, ap1 = await register_player(event, name="First", base_price=1000)
    await _activate(event, ap1)
    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team.id, 1000, session)
        await auction_service.hammer_player(event.id, ap1.id, session)

    _p2, ap2 = await register_player(event, name="Second", base_price=1000)
    await _activate(event, ap2)
    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="roster is full"):
            await auction_service.place_bid(event.id, team.id, 1000, session)


# ── Increment slabs ───────────────────────────────────────────────────────────


async def test_increment_follows_the_configured_slab(
    make_event, make_team, register_player, make_slab
):
    """The server's ladder is the configured one, not a hardcoded default."""
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_slab(event, min_value=0, max_value=50000, increment=500, sort_order=0)
    await make_slab(event, min_value=50000, max_value=0, increment=5000, sort_order=1)
    team_a = await make_team(event, name="A")
    team_b = await make_team(event, name="B")
    _p, ap = await register_player(event, base_price=10000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team_a.id, 10000, session)

    # +300 is below the 500 slab step.
    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="Minimum increment"):
            await auction_service.place_bid(event.id, team_b.id, 10300, session)

    # +500 is exactly the slab step.
    async with AsyncSessionLocal() as session:
        bid = await auction_service.place_bid(event.id, team_b.id, 10500, session)
    assert bid.amount == 10500


async def test_non_multiple_increment_is_rejected(
    make_event, make_team, register_player, make_slab
):
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team_a = await make_team(event, name="A")
    team_b = await make_team(event, name="B")
    _p, ap = await register_player(event, base_price=10000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team_a.id, 10000, session)
    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="multiple of"):
            await auction_service.place_bid(event.id, team_b.id, 11500, session)


# ── Hammer outcomes ───────────────────────────────────────────────────────────


async def test_hammer_with_no_bids_marks_unsold(make_event, make_team, register_player):
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_team(event, name="A")
    _p, ap = await register_player(event, base_price=5000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        result = await auction_service.hammer_player(event.id, ap.id, session)

    assert result.status == PlayerAuctionStatus.unsold
    async with AsyncSessionLocal() as session:
        sales = (
            await session.execute(select(TeamPlayer).where(TeamPlayer.event_id == event.id))
        ).scalars().all()
    assert sales == []


async def test_sale_records_price_and_team(make_event, make_team, register_player):
    event, *_ = await make_event(status=AuctionStatus.active)
    team = await make_team(event, name="Winners")
    player, ap = await register_player(event, name="Star", base_price=5000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team.id, 6000, session)
        await auction_service.hammer_player(event.id, ap.id, session)

    async with AsyncSessionLocal() as session:
        sale = (
            await session.execute(select(TeamPlayer).where(TeamPlayer.event_id == event.id))
        ).scalars().one()
        team_row = await session.get(Team, team.id)

    assert sale.player_id == player.id
    assert sale.team_id == team.id
    assert sale.sold_price == 6000
    assert team_row.spent == 6000


# ── Undo keeps the audit trail ────────────────────────────────────────────────


async def test_undo_reverts_without_deleting_the_bid(
    make_event, make_team, register_player, make_slab
):
    """1.x called db.delete() on the bid row, destroying the record."""
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team_a = await make_team(event, name="A")
    team_b = await make_team(event, name="B")
    _p, ap = await register_player(event, base_price=10000)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        await auction_service.place_bid(event.id, team_a.id, 10000, session)
        await auction_service.place_bid(event.id, team_b.id, 11000, session)
        result = await auction_service.undo_last_bid(event.id, session)

    assert result["current_bid"] == 10000
    assert result["current_bid_team_id"] == team_a.id

    async with AsyncSessionLocal() as session:
        bids = (
            await session.execute(select(Bid).where(Bid.auction_player_id == ap.id))
        ).scalars().all()

    assert len(bids) == 2, "Both bids must still exist in the record"
    assert sum(1 for b in bids if b.reverted_at is not None) == 1


# ── Putting players up ────────────────────────────────────────────────────────


async def test_cannot_put_up_a_player_while_one_is_on_the_block(
    make_event, make_team, register_player
):
    """1.x silently flipped the live player to unsold with no broadcast."""
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_team(event, name="A")
    _p1, ap1 = await register_player(event, name="First")
    _p2, ap2 = await register_player(event, name="Second")
    await _activate(event, ap1)

    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="still on the block"):
            await auction_service.set_next_player(event.id, ap2.id, session)

    async with AsyncSessionLocal() as session:
        row = await session.get(AuctionPlayer, ap1.id)
    assert row.status == PlayerAuctionStatus.active, "The live player must not be dropped"


async def test_pull_player_takes_them_off_the_block(
    make_event, make_team, register_player
):
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_team(event, name="A")
    _p, ap = await register_player(event)
    await _activate(event, ap)

    async with AsyncSessionLocal() as session:
        result = await auction_service.pull_player(event.id, session)

    assert result.status == PlayerAuctionStatus.unsold


# ── Starting the auction puts a player up (new in 2.1.4) ─────────────────────


async def test_start_puts_a_player_on_the_block(
    make_event, make_team, register_player
):
    """Pressing Start should not leave the auctioneer on an empty stage."""
    event, _admin, _org, auctioneer = await make_event(status=AuctionStatus.ready)
    await make_team(event, name="A")
    await make_team(event, name="B")
    _p1, ap1 = await register_player(event, name="One")
    _p2, ap2 = await register_player(event, name="Two")

    async with AsyncSessionLocal() as session:
        await auction_service.start_auction(event.id, session, actor=auctioneer)

    async with AsyncSessionLocal() as session:
        active = await auction_service._load_active_player(session, event.id)

    assert active is not None, "A player must be up as soon as the auction starts"
    assert active.id in (ap1.id, ap2.id)
    assert active.status == PlayerAuctionStatus.active


async def test_start_still_works_with_no_players(make_event, make_team):
    """An empty pool must not stop the auction from starting."""
    event, _admin, _org, auctioneer = await make_event(status=AuctionStatus.ready)
    await make_team(event, name="A")
    await make_team(event, name="B")

    async with AsyncSessionLocal() as session:
        result = await auction_service.start_auction(event.id, session, actor=auctioneer)

    assert result.status == AuctionStatus.active
    async with AsyncSessionLocal() as session:
        assert await auction_service._load_active_player(session, event.id) is None


async def test_resume_does_not_replace_the_player_on_the_block(
    make_event, make_team, register_player
):
    """Pausing mid-player and resuming must keep that player up."""
    event, _admin, _org, auctioneer = await make_event(status=AuctionStatus.ready)
    await make_team(event, name="A")
    _p1, ap1 = await register_player(event, name="One")
    _p2, ap2 = await register_player(event, name="Two")

    async with AsyncSessionLocal() as session:
        await auction_service.start_auction(event.id, session, actor=auctioneer)
    async with AsyncSessionLocal() as session:
        first = await auction_service._load_active_player(session, event.id)
        first_id = first.id
        await auction_service.pause_auction(event.id, session, actor=auctioneer)
    async with AsyncSessionLocal() as session:
        await auction_service.start_auction(event.id, session, actor=auctioneer)
        still_up = await auction_service._load_active_player(session, event.id)

    assert still_up.id == first_id, "Resuming must not swap the player out"


# ── Organiser auction-pool settings ────────────────────────────────────────────


async def test_assigned_captains_are_excluded_when_setting_is_enabled(
    make_event, make_team, register_player
):
    event, *_ = await make_event(status=AuctionStatus.active)
    captain, captain_ap = await register_player(event, name="Captain")
    await make_team(event, name="Team A", captain_id=captain.id)
    _regular, regular_ap = await register_player(event, name="Regular")

    async with AsyncSessionLocal() as session:
        picked = await auction_service._pick_random_pending(session, event.id)

    assert picked.id == regular_ap.id
    assert picked.id != captain_ap.id


async def test_auto_unsold_round_requeues_unsold_players_once(
    make_event, make_team, register_player
):
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_team(event, name="Team A")
    _player, unsold_ap = await register_player(event, name="Unsold")
    async with AsyncSessionLocal() as session:
        stored_event = await session.get(type(event), event.id)
        stored_event.auto_reauction_unsold = True
        unsold = await session.get(AuctionPlayer, unsold_ap.id)
        unsold.status = PlayerAuctionStatus.unsold
        await session.commit()

    async with AsyncSessionLocal() as session:
        picked = await auction_service._pick_random_pending(session, event.id)
        stored_event = await session.get(type(event), event.id)
        assert picked.id == unsold_ap.id
        assert picked.status == PlayerAuctionStatus.pending
        assert stored_event.unsold_reauction_started is True

    # Once that single extra pass is exhausted, the same unsold player is not
    # recycled indefinitely.
    async with AsyncSessionLocal() as session:
        picked.status = PlayerAuctionStatus.unsold
        session.add(picked)
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="No pending players left"):
            await auction_service._pick_random_pending(session, event.id)


async def test_player_up_persists_deadline_without_external_timer_queue(
    make_event, register_player, monkeypatch
):
    """The database owns the deadline; publishing a player needs no queue service."""
    event, *_ = await make_event(status=AuctionStatus.active, no_timer=False)
    _player, ap = await register_player(event, name="Local timer", base_price=5000)
    broadcasts = []

    async def capture_broadcast(event_id, message):
        broadcasts.append(message)

    monkeypatch.setattr(auction_service.manager, "broadcast", capture_broadcast)
    async with AsyncSessionLocal() as session:
        result = await auction_service.set_next_player(event.id, ap.id, session)

    assert result.id == ap.id
    assert broadcasts and broadcasts[-1]["type"] == "player_up"
    assert broadcasts[-1]["player_name"] == "Local timer"
    async with AsyncSessionLocal() as session:
        stored_event = await session.get(AuctionEvent, event.id)
        assert stored_event.deadline_at is not None
        assert stored_event.no_timer is False


async def test_local_timer_hammers_expired_player_once(
    make_event, register_player
):
    import datetime as dt

    event, *_ = await make_event(status=AuctionStatus.active, no_timer=False)
    _player, ap = await register_player(event, name="Expired lot", base_price=5000)
    async with AsyncSessionLocal() as session:
        await auction_service.set_next_player(event.id, ap.id, session)
        stored_event = await session.get(AuctionEvent, event.id)
        stored_event.deadline_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=2)
        await session.commit()

    async with AsyncSessionLocal() as session:
        first = await auction_service.run_due_hammers(session)
    async with AsyncSessionLocal() as session:
        second = await auction_service.run_due_hammers(session)

    assert first == 1
    assert second == 0
    async with AsyncSessionLocal() as session:
        stored_player = await session.get(AuctionPlayer, ap.id)
        assert stored_player.status == PlayerAuctionStatus.unsold


async def test_pause_resume_preserves_remaining_timer(
    make_event, register_player
):
    import datetime as dt
    event, *_ = await make_event(status=AuctionStatus.ready, no_timer=False)
    _player, ap = await register_player(event, name="Paused lot", base_price=5000)
    async with AsyncSessionLocal() as session:
        await auction_service.start_auction(event.id, session)
    async with AsyncSessionLocal() as session:
        stored_event = await session.get(AuctionEvent, event.id)
        original_deadline = stored_event.deadline_at
        await auction_service.pause_auction(event.id, session)
        assert stored_event.deadline_at is None
        assert stored_event.paused_remaining_seconds is not None
    async with AsyncSessionLocal() as session:
        await auction_service.start_auction(event.id, session)
        stored_event = await session.get(AuctionEvent, event.id)
        assert stored_event.deadline_at is not None
        assert stored_event.paused_remaining_seconds is None
        assert stored_event.deadline_at >= original_deadline - dt.timedelta(seconds=30)


async def test_automatic_progression_is_driven_by_local_server_timer(
    make_event, register_player
):
    import datetime as dt

    event, *_ = await make_event(status=AuctionStatus.active, no_timer=False)
    _first, first_ap = await register_player(event, name="First lot", base_price=5000)
    _second, second_ap = await register_player(event, name="Second lot", base_price=5000)
    async with AsyncSessionLocal() as session:
        await auction_service.set_next_player(event.id, first_ap.id, session)
        stored_event = await session.get(AuctionEvent, event.id)
        stored_event.deadline_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await session.commit()

    async with AsyncSessionLocal() as session:
        await auction_service.run_due_hammers(session)
    async with AsyncSessionLocal() as session:
        stored_event = await session.get(AuctionEvent, event.id)
        assert stored_event.advance_at is not None
        stored_event.advance_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await session.commit()

    async with AsyncSessionLocal() as session:
        await auction_service.run_due_hammers(session)
    async with AsyncSessionLocal() as session:
        first = await session.get(AuctionPlayer, first_ap.id)
        second = await session.get(AuctionPlayer, second_ap.id)
        assert first.status == PlayerAuctionStatus.unsold
        assert second.status == PlayerAuctionStatus.active


async def test_manual_progression_waits_for_auctioneer_next_player(
    make_event, register_player
):
    import datetime as dt

    event, *_ = await make_event(status=AuctionStatus.active, no_timer=False)
    _first, first_ap = await register_player(event, name="Manual first", base_price=5000)
    _second, second_ap = await register_player(event, name="Manual second", base_price=5000)
    async with AsyncSessionLocal() as session:
        stored_event = await session.get(AuctionEvent, event.id)
        stored_event.auto_advance = False
        await session.commit()
        await auction_service.set_next_player(event.id, first_ap.id, session)
        stored_event = await session.get(AuctionEvent, event.id)
        stored_event.deadline_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await session.commit()

    async with AsyncSessionLocal() as session:
        await auction_service.run_due_hammers(session)
    async with AsyncSessionLocal() as session:
        stored_event = await session.get(AuctionEvent, event.id)
        second = await session.get(AuctionPlayer, second_ap.id)
        assert stored_event.advance_at is None
        assert second.status == PlayerAuctionStatus.pending
        await auction_service.set_next_player(event.id, second_ap.id, session)
        await session.refresh(second)
        assert second.status == PlayerAuctionStatus.active
