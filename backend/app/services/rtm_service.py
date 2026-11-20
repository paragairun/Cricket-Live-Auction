"""Right to Match.

New in 2.1.0 (2026-11-20).

Lets the team that holds rights on a player reclaim them by matching the winning
bid. The sequence follows the current IPL rules:

    bidding closes, top bid X by team A
      -> the rights holder B is asked: exercise, or decline?
         declined  -> sold to A at X
         exercised -> A gets ONE chance to raise
                        stands pat -> sold to B at X        (B spends a card)
                        raises to Y -> B must match Y
                                         matched     -> sold to B at Y (card spent)
                                         not matched -> sold to A at Y

While a decision is outstanding the player sits in `rtm_pending`: neither sold
nor back in the pool, and the clock is stopped. Every transition is an explicit
operator action with its own endpoint, writes an audit row and broadcasts a
distinct message, so a spectator screen can follow the negotiation.

Each transition is guarded by the same optimistic-concurrency check the bid path
uses, so two operator consoles cannot both advance the negotiation.
"""
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auction import (
    AuctionEvent,
    AuctionPlayer,
    AuditAction,
    PlayerAuctionStatus,
    RtmStage,
    Team,
)
from app.models.user import User
from app.services import bid_rules

logger = logging.getLogger(__name__)


class RtmError(ValueError):
    """An RTM rule was broken. Surfaces as 400."""


class RtmConflict(RtmError):
    """The negotiation moved on before this call landed. Surfaces as 409."""


async def rights_holder_for(
    db: AsyncSession, event: AuctionEvent, ap: AuctionPlayer, winning_team_id: int | None
) -> Team | None:
    """The team entitled to match this sale, or None when RTM does not apply.

    Returns None — meaning "sell normally" — when RTM is off for the event, no
    rights are recorded, the rights holder is the winning bidder anyway, the
    holder is out of cards, the holder cannot afford the price, or the holder's
    roster is already full.
    """
    if not event.rtm_enabled:
        return None
    if ap.rtm_team_id is None:
        return None
    if winning_team_id is None:
        # Nobody bid; there is nothing to match.
        return None
    if ap.rtm_team_id == winning_team_id:
        return None

    holder = (
        await db.execute(select(Team).where(Team.id == ap.rtm_team_id))
    ).scalar_one_or_none()
    if holder is None:
        return None

    if event.rtm_cards_per_team and holder.rtm_cards_used >= event.rtm_cards_per_team:
        return None

    if holder.budget - holder.spent < ap.current_bid:
        return None

    if len(holder.players or []) >= holder.max_players:
        return None

    return holder


def _require_stage(ap: AuctionPlayer, expected: RtmStage) -> None:
    if ap.status != PlayerAuctionStatus.rtm_pending:
        raise RtmError("No Right-to-Match decision is pending for this player")
    if ap.rtm_stage != expected:
        raise RtmConflict(
            f"This decision is no longer available (the negotiation is at "
            f"'{ap.rtm_stage}', not '{expected.value}')"
        )


async def _advance(
    db: AsyncSession,
    ap: AuctionPlayer,
    *,
    stage: RtmStage | None,
    top_amount: int | None = None,
) -> None:
    """Move the negotiation on, guarded against a concurrent console."""
    values: dict = {"rtm_stage": stage, "version": AuctionPlayer.version + 1}
    if top_amount is not None:
        values["rtm_top_amount"] = top_amount

    claim = await db.execute(
        update(AuctionPlayer)
        .where(
            AuctionPlayer.id == ap.id,
            AuctionPlayer.version == ap.version,
            AuctionPlayer.status == PlayerAuctionStatus.rtm_pending,
        )
        .values(**values)
    )
    if claim.rowcount == 0:
        await db.rollback()
        raise RtmConflict("The other console advanced this decision first")


async def validate_raise(
    db: AsyncSession,
    event: AuctionEvent,
    ap: AuctionPlayer,
    amount: int,
) -> None:
    """Check a top bidder's raise against increments, purse and roster."""
    current = ap.rtm_top_amount or ap.current_bid
    if amount <= current:
        raise RtmError(f"A raise must exceed the current figure ({current})")

    from app.services.auction_service import _load_slabs, _squad_size

    slabs = await _load_slabs(db, event.id)
    step = bid_rules.increment_for(slabs, current)
    increase = amount - current
    if increase < step:
        raise RtmError(f"Minimum increment at this price is {step}")
    if increase % step != 0:
        raise RtmError(f"The raise must be a multiple of {step}")

    bidder = (
        await db.execute(select(Team).where(Team.id == ap.rtm_top_team_id))
    ).scalar_one_or_none()
    if bidder is None:
        raise RtmError("The winning bidder is no longer in this event")

    squad = await _squad_size(db, event.id, bidder.id)
    remaining = bidder.budget - bidder.spent
    if amount > remaining:
        raise RtmError(f"{bidder.name} has only {remaining} left")

    reserve = bid_rules.reserved_budget(
        event.team_min_players, squad, event.player_base_price
    )
    if reserve and amount > remaining - reserve:
        raise RtmError(
            f"{bidder.name} must keep {reserve} back to fill the minimum squad"
        )


async def validate_match(
    db: AsyncSession, event: AuctionEvent, ap: AuctionPlayer
) -> Team:
    """Check the rights holder can still afford the figure they must match."""
    holder = (
        await db.execute(select(Team).where(Team.id == ap.rtm_team_id))
    ).scalar_one_or_none()
    if holder is None:
        raise RtmError("The rights-holding team is no longer in this event")

    amount = ap.rtm_top_amount or ap.current_bid
    remaining = holder.budget - holder.spent
    if amount > remaining:
        raise RtmError(
            f"{holder.name} cannot match {amount} — only {remaining} left"
        )

    from app.services.auction_service import _squad_size

    squad = await _squad_size(db, event.id, holder.id)
    if squad >= holder.max_players:
        raise RtmError(f"{holder.name}'s roster is full")

    reserve = bid_rules.reserved_budget(
        event.team_min_players, squad, event.player_base_price
    )
    if reserve and amount > remaining - reserve:
        raise RtmError(
            f"{holder.name} must keep {reserve} back to fill the minimum squad"
        )

    return holder


def audit_action_for(stage_change: str) -> AuditAction:
    return {
        "offered": AuditAction.rtm_offered,
        "exercised": AuditAction.rtm_exercised,
        "declined": AuditAction.rtm_declined,
        "raised": AuditAction.rtm_raised,
        "raise_skipped": AuditAction.rtm_raise_skipped,
        "matched": AuditAction.rtm_matched,
        "not_matched": AuditAction.rtm_not_matched,
    }[stage_change]
