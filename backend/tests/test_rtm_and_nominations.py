"""Tests for Right to Match and the nomination round.

New in 2.1.0 (2026-11-20).

The RTM sequence has four terminal outcomes and the player must end up with
exactly one team in every one of them, with exactly one purse debited and a card
spent only when the rights holder actually acquires the player. Those are the
assertions that matter, so each path gets its own test.
"""
import asyncio

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.auction import (
    AuctionEvent,
    AuctionMode,
    AuctionPlayer,
    AuctionStatus,
    PlayerAuctionStatus,
    PlayerNomination,
    RtmStage,
    Team,
    TeamPlayer,
)
from app.services import auction_service
from app.services.auction_service import AuctionError, ConcurrentUpdate
from app.services.rtm_service import RtmConflict, RtmError


async def _activate(ap, bid: int | None = None, team_id: int | None = None):
    async with AsyncSessionLocal() as session:
        row = await session.get(AuctionPlayer, ap.id)
        row.status = PlayerAuctionStatus.active
        row.current_bid = bid if bid is not None else row.base_price
        row.current_bid_team_id = team_id
        await session.commit()
        await session.refresh(row)
        return row


async def _set_rtm_rights(ap, team_id: int):
    async with AsyncSessionLocal() as session:
        row = await session.get(AuctionPlayer, ap.id)
        row.rtm_team_id = team_id
        await session.commit()


async def _enable_rtm(event, cards: int = 2, mode: AuctionMode = AuctionMode.random):
    async with AsyncSessionLocal() as session:
        row = await session.get(AuctionEvent, event.id)
        row.rtm_enabled = True
        row.rtm_cards_per_team = cards
        row.auction_mode = mode
        await session.commit()
        await session.refresh(row)
        return row


async def _sales(event_id: int):
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(TeamPlayer).where(TeamPlayer.event_id == event_id)
            )
        ).scalars().all()


async def _team(team_id: int) -> Team:
    async with AsyncSessionLocal() as session:
        return await session.get(Team, team_id)


@pytest.fixture
async def rtm_setup(make_event, make_team, register_player, make_slab):
    """Team A has the top bid of 11000; team B holds the rights."""
    event, *_ = await make_event(status=AuctionStatus.active, team_budget=100000)
    await make_slab(event, min_value=0, max_value=0, increment=1000)
    team_a = await make_team(event, name="Bidder A")
    team_b = await make_team(event, name="Rights B")
    _player, ap = await register_player(event, name="Contested", base_price=10000)
    await _enable_rtm(event)
    await _set_rtm_rights(ap, team_b.id)
    ap = await _activate(ap, bid=11000, team_id=team_a.id)
    return event, team_a, team_b, ap


# ── Opening the negotiation ───────────────────────────────────────────────────


async def test_hammer_opens_rtm_instead_of_selling(rtm_setup):
    event, team_a, team_b, ap = rtm_setup

    async with AsyncSessionLocal() as session:
        result = await auction_service.hammer_player(event.id, ap.id, session)

    assert result.status == PlayerAuctionStatus.rtm_pending
    assert result.rtm_stage == RtmStage.offered
    assert result.rtm_top_team_id == team_a.id
    assert result.rtm_top_amount == 11000
    # Nothing is sold and no purse is touched until the decision is made.
    assert await _sales(event.id) == []
    assert (await _team(team_a.id)).spent == 0


async def test_rtm_is_skipped_when_disabled(make_event, make_team, register_player):
    event, *_ = await make_event(status=AuctionStatus.active)
    team_a = await make_team(event, name="A")
    team_b = await make_team(event, name="B")
    _p, ap = await register_player(event, base_price=10000)
    await _set_rtm_rights(ap, team_b.id)  # rights recorded but rtm_enabled is false
    await _activate(ap, bid=11000, team_id=team_a.id)

    async with AsyncSessionLocal() as session:
        result = await auction_service.hammer_player(event.id, ap.id, session)

    assert result.status == PlayerAuctionStatus.sold
    sales = await _sales(event.id)
    assert len(sales) == 1 and sales[0].team_id == team_a.id


async def test_rtm_is_skipped_when_the_holder_is_the_top_bidder(
    make_event, make_team, register_player
):
    """A team cannot match its own bid — that would just raise its own price."""
    event, *_ = await make_event(status=AuctionStatus.active)
    team_a = await make_team(event, name="A")
    _p, ap = await register_player(event, base_price=10000)
    await _enable_rtm(event)
    await _set_rtm_rights(ap, team_a.id)
    await _activate(ap, bid=11000, team_id=team_a.id)

    async with AsyncSessionLocal() as session:
        result = await auction_service.hammer_player(event.id, ap.id, session)

    assert result.status == PlayerAuctionStatus.sold


async def test_rtm_is_skipped_when_the_holder_has_no_cards_left(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        team = await session.get(Team, team_b.id)
        team.rtm_cards_used = 2  # event allows 2
        await session.commit()

    async with AsyncSessionLocal() as session:
        result = await auction_service.hammer_player(event.id, ap.id, session)

    assert result.status == PlayerAuctionStatus.sold


async def test_rtm_is_skipped_when_the_holder_cannot_afford_it(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        team = await session.get(Team, team_b.id)
        team.spent = team.budget - 500  # cannot reach 11000
        await session.commit()

    async with AsyncSessionLocal() as session:
        result = await auction_service.hammer_player(event.id, ap.id, session)

    assert result.status == PlayerAuctionStatus.sold


async def test_unsold_player_never_opens_rtm(rtm_setup):
    """Nobody bid, so there is nothing to match."""
    event, team_a, team_b, ap = rtm_setup
    await _activate(ap, bid=10000, team_id=None)

    async with AsyncSessionLocal() as session:
        result = await auction_service.hammer_player(event.id, ap.id, session)

    assert result.status == PlayerAuctionStatus.unsold


# ── The four terminal outcomes ────────────────────────────────────────────────


async def test_decline_sells_to_the_top_bidder(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)
    async with AsyncSessionLocal() as session:
        result = await auction_service.rtm_decline(event.id, session)

    assert result.status == PlayerAuctionStatus.sold
    sales = await _sales(event.id)
    assert len(sales) == 1
    assert sales[0].team_id == team_a.id
    assert sales[0].sold_price == 11000
    assert (await _team(team_a.id)).spent == 11000
    # No card is spent when the holder passes.
    assert (await _team(team_b.id)).rtm_cards_used == 0


async def test_exercise_then_no_raise_gives_the_player_to_the_holder(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)
    async with AsyncSessionLocal() as session:
        await auction_service.rtm_exercise(event.id, session)
    async with AsyncSessionLocal() as session:
        result = await auction_service.rtm_skip_raise(event.id, session)

    assert result.status == PlayerAuctionStatus.sold
    sales = await _sales(event.id)
    assert len(sales) == 1
    assert sales[0].team_id == team_b.id
    assert sales[0].sold_price == 11000
    assert (await _team(team_b.id)).spent == 11000
    assert (await _team(team_a.id)).spent == 0
    # A card is spent only when the holder actually acquires the player.
    assert (await _team(team_b.id)).rtm_cards_used == 1


async def test_raise_then_match_gives_the_player_to_the_holder_at_the_raise(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)
    async with AsyncSessionLocal() as session:
        await auction_service.rtm_exercise(event.id, session)
    async with AsyncSessionLocal() as session:
        await auction_service.rtm_raise(event.id, 13000, session)
    async with AsyncSessionLocal() as session:
        result = await auction_service.rtm_match(event.id, session)

    assert result.status == PlayerAuctionStatus.sold
    sales = await _sales(event.id)
    assert len(sales) == 1
    assert sales[0].team_id == team_b.id
    assert sales[0].sold_price == 13000, "The holder pays the raised figure"
    assert (await _team(team_b.id)).spent == 13000
    assert (await _team(team_b.id)).rtm_cards_used == 1


async def test_raise_then_no_match_gives_the_player_to_the_bidder_at_the_raise(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)
    async with AsyncSessionLocal() as session:
        await auction_service.rtm_exercise(event.id, session)
    async with AsyncSessionLocal() as session:
        await auction_service.rtm_raise(event.id, 13000, session)
    async with AsyncSessionLocal() as session:
        result = await auction_service.rtm_no_match(event.id, session)

    assert result.status == PlayerAuctionStatus.sold
    sales = await _sales(event.id)
    assert len(sales) == 1
    assert sales[0].team_id == team_a.id
    assert sales[0].sold_price == 13000
    assert (await _team(team_a.id)).spent == 13000
    assert (await _team(team_b.id)).rtm_cards_used == 0


# ── Guard rails ───────────────────────────────────────────────────────────────


async def test_steps_cannot_be_taken_out_of_order(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)

    # At 'offered' a match or a raise is not yet available.
    async with AsyncSessionLocal() as session:
        with pytest.raises(RtmConflict):
            await auction_service.rtm_match(event.id, session)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RtmConflict):
            await auction_service.rtm_raise(event.id, 12000, session)

    async with AsyncSessionLocal() as session:
        await auction_service.rtm_exercise(event.id, session)

    # At 'raise_offered' the holder cannot match until the bidder has acted.
    async with AsyncSessionLocal() as session:
        with pytest.raises(RtmConflict):
            await auction_service.rtm_match(event.id, session)


async def test_a_raise_must_respect_the_increment(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)
    async with AsyncSessionLocal() as session:
        await auction_service.rtm_exercise(event.id, session)

    async with AsyncSessionLocal() as session:
        with pytest.raises(RtmError, match="Minimum increment"):
            await auction_service.rtm_raise(event.id, 11500, session)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RtmError, match="must exceed"):
            await auction_service.rtm_raise(event.id, 11000, session)


async def test_a_raise_cannot_exceed_the_bidders_purse(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        team = await session.get(Team, team_a.id)
        team.spent = team.budget - 11500
        await session.commit()

    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)
    async with AsyncSessionLocal() as session:
        await auction_service.rtm_exercise(event.id, session)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RtmError, match="only"):
            await auction_service.rtm_raise(event.id, 20000, session)


async def test_concurrent_resolutions_settle_once(rtm_setup):
    """Both consoles clicking Decline must not sell the player twice."""
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)

    async def decline():
        async with AsyncSessionLocal() as session:
            try:
                await auction_service.rtm_decline(event.id, session)
                return "ok"
            except (ConcurrentUpdate, AuctionError, RtmError):
                return "rejected"

    results = await asyncio.gather(decline(), decline())
    assert results.count("ok") == 1, f"expected one winner, got {results}"
    assert len(await _sales(event.id)) == 1


async def test_rtm_state_is_exposed_for_the_console(rtm_setup):
    event, team_a, team_b, ap = rtm_setup
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)
    async with AsyncSessionLocal() as session:
        state = await auction_service.get_rtm_state(event.id, session)

    assert state is not None
    assert state["stage"] == RtmStage.offered
    assert state["rights_team_name"] == "Rights B"
    assert state["top_team_name"] == "Bidder A"
    assert state["top_amount"] == 11000


# ── Nomination round ──────────────────────────────────────────────────────────


async def test_nominated_player_comes_up_next(
    make_event, make_team, register_player
):
    event, *_ = await make_event(status=AuctionStatus.active)
    team = await make_team(event, name="A")
    _p1, ap1 = await register_player(event, name="First")
    _p2, ap2 = await register_player(event, name="Wanted")
    _p3, ap3 = await register_player(event, name="Third")
    await _enable_rtm(event, mode=AuctionMode.nomination)

    async with AsyncSessionLocal() as session:
        await auction_service.nominate_player(event.id, team.id, ap2.id, session)
    async with AsyncSessionLocal() as session:
        up = await auction_service.set_next_player(event.id, None, session)

    assert up.id == ap2.id, "The nominated player must come up, not a random one"

    # The nomination is consumed, not reused.
    async with AsyncSessionLocal() as session:
        pending = (
            await session.execute(
                select(PlayerNomination).where(
                    PlayerNomination.event_id == event.id,
                    PlayerNomination.consumed_at.is_(None),
                )
            )
        ).scalars().all()
    assert pending == []


async def test_nominations_come_up_in_order(make_event, make_team, register_player):
    event, *_ = await make_event(status=AuctionStatus.active)
    team = await make_team(event, name="A")
    _p1, ap1 = await register_player(event, name="One")
    _p2, ap2 = await register_player(event, name="Two")
    await _enable_rtm(event, mode=AuctionMode.nomination)

    async with AsyncSessionLocal() as session:
        await auction_service.nominate_player(event.id, team.id, ap2.id, session)
        await auction_service.nominate_player(event.id, team.id, ap1.id, session)

    async with AsyncSessionLocal() as session:
        first = await auction_service.set_next_player(event.id, None, session)
    assert first.id == ap2.id
    async with AsyncSessionLocal() as session:
        await auction_service.pull_player(event.id, session)
    async with AsyncSessionLocal() as session:
        second = await auction_service.set_next_player(event.id, None, session)
    assert second.id == ap1.id


async def test_an_empty_queue_is_a_clear_error(make_event, make_team, register_player):
    event, *_ = await make_event(status=AuctionStatus.active)
    await make_team(event, name="A")
    await register_player(event, name="Someone")
    await _enable_rtm(event, mode=AuctionMode.nomination)

    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="nomination queue is empty"):
            await auction_service.set_next_player(event.id, None, session)


async def test_a_player_cannot_be_nominated_twice(
    make_event, make_team, register_player
):
    event, *_ = await make_event(status=AuctionStatus.active)
    team_a = await make_team(event, name="A")
    team_b = await make_team(event, name="B")
    _p, ap = await register_player(event, name="Popular")
    await _enable_rtm(event, mode=AuctionMode.nomination)

    async with AsyncSessionLocal() as session:
        await auction_service.nominate_player(event.id, team_a.id, ap.id, session)
    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="already been nominated"):
            await auction_service.nominate_player(event.id, team_b.id, ap.id, session)


async def test_a_sold_player_cannot_be_nominated(
    make_event, make_team, register_player
):
    event, *_ = await make_event(status=AuctionStatus.active)
    team = await make_team(event, name="A")
    _p, ap = await register_player(event, base_price=1000)
    await _activate(ap, bid=1000, team_id=team.id)
    async with AsyncSessionLocal() as session:
        await auction_service.hammer_player(event.id, ap.id, session)

    async with AsyncSessionLocal() as session:
        with pytest.raises(AuctionError, match="not available to nominate"):
            await auction_service.nominate_player(event.id, team.id, ap.id, session)


async def test_a_nomination_can_be_withdrawn(make_event, make_team, register_player):
    event, *_ = await make_event(status=AuctionStatus.active)
    team = await make_team(event, name="A")
    _p, ap = await register_player(event, name="Changed mind")
    await _enable_rtm(event, mode=AuctionMode.nomination)

    async with AsyncSessionLocal() as session:
        nomination = await auction_service.nominate_player(
            event.id, team.id, ap.id, session
        )
    async with AsyncSessionLocal() as session:
        await auction_service.withdraw_nomination(event.id, nomination.id, session)
    async with AsyncSessionLocal() as session:
        queue = await auction_service.list_nominations(event.id, session)
    assert queue == []


async def test_random_mode_ignores_the_queue(make_event, make_team, register_player):
    """A nomination left over from an accelerated round must not hijack the draw."""
    event, *_ = await make_event(status=AuctionStatus.active)
    team = await make_team(event, name="A")
    _p1, ap1 = await register_player(event, name="Only player")
    await _enable_rtm(event, mode=AuctionMode.nomination)
    async with AsyncSessionLocal() as session:
        await auction_service.nominate_player(event.id, team.id, ap1.id, session)

    async with AsyncSessionLocal() as session:
        row = await session.get(AuctionEvent, event.id)
        row.auction_mode = AuctionMode.random
        await session.commit()

    async with AsyncSessionLocal() as session:
        up = await auction_service.set_next_player(event.id, None, session)
    assert up.id == ap1.id  # only candidate, but drawn rather than popped

    async with AsyncSessionLocal() as session:
        queue = await auction_service.list_nominations(event.id, session)
    assert len(queue) == 1, "The nomination should still be queued, not consumed"
