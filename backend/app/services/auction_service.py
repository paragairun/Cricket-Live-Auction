"""Live auction engine.

Version: 2.0.0 (2026-11-20)

Rewritten from 1.x. The four substantive changes:

1. CONCURRENCY. `place_bid` previously did SELECT -> validate -> mutate -> COMMIT
   with no lock held across the read and the write, and no version column. Two
   bids at the same instant both passed validation against the same snapshot,
   both were written, and the player went to whoever committed last rather than
   whoever bid first. This was reproducible. Acceptance is now a conditional
   UPDATE guarded by a version column (optimistic concurrency control), with a
   row lock on backends that support it. A losing bid gets a clear "the price
   moved" rejection instead of silently winning or silently losing.

2. IDEMPOTENCY. Every mutating operation takes an idempotency key. With two
   operator consoles driving one auction (auctioneer plus assistant), a retry, a
   double-tap or a reconnect must not place a second bid or sell a player twice.
   Unique constraints in the schema are the backstop.

3. DURABLE TIMER. The countdown was an asyncio.Task inside one worker process,
   coordinated by a Redis key holding a PID. If the worker restarted the task
   died, the player stayed `active` forever and the hammer never fell. PIDs also
   collide across containers. Redis now holds an absolute deadline; the hammer
   fires from a deferred ARQ job, with a watchdog sweep as a second line. The
   per-second tick broadcast is gone — clients count down from the deadline.

4. AUDIT. Undo hard-deleted the bid row and bid-edit rewrote the amount with no
   record, so a disputed sale could not be reconstructed. Bids are now reverted,
   not deleted, and every operator action writes an AuctionAuditLog row.
"""
import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.background import dispatch_email
from app.models.auction import (
    AuctionAuditLog,
    AuctionEvent,
    AuctionMode,
    AuctionPlayer,
    AuctionStatus,
    AuditAction,
    Bid,
    BidSlab,
    Player,
    PlayerAuctionStatus,
    PlayerCategory,
    PlayerNomination,
    RtmStage,
    Team,
    TeamPlayer,
)
from app.models.user import User
from app.services import bid_rules, rtm_service
from app.services.bid_rules import Slab
from app.ws.manager import manager

logger = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────────


class AuctionError(ValueError):
    """A rule was broken. Surfaces to the client as 400."""


class ConcurrentUpdate(AuctionError):
    """Another operator's action landed first. Surfaces as 409."""


# ── Timing ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Timing:
    base_seconds: int
    threshold_seconds: int
    extension_seconds: int
    shrink_factor: float
    min_extension_seconds: int

    @classmethod
    def for_event(cls, event: AuctionEvent) -> "Timing":
        return cls(
            base_seconds=event.timer_seconds or settings.AUCTION_TIMER_SECONDS,
            threshold_seconds=(
                event.soft_close_threshold_seconds
                if event.soft_close_threshold_seconds is not None
                else settings.SOFT_CLOSE_THRESHOLD_SECONDS
            ),
            extension_seconds=(
                event.soft_close_extension_seconds
                or settings.SOFT_CLOSE_EXTENSION_SECONDS
            ),
            shrink_factor=settings.SOFT_CLOSE_SHRINK_FACTOR,
            min_extension_seconds=settings.SOFT_CLOSE_MIN_EXTENSION_SECONDS,
        )

    def extension_for(self, extension_count: int) -> int:
        """Each successive extension on the same player is shorter.

        Without this a two-team war never ends: each bid buys another full
        window. 1.x reset to the full 180s on every bid, so a contested player
        could run for twenty minutes.
        """
        value = self.extension_seconds * (self.shrink_factor ** max(0, extension_count))
        return max(self.min_extension_seconds, int(round(value)))


def _now() -> float:
    return time.time()


# ── Audit ─────────────────────────────────────────────────────────────────────


async def record_audit(
    db: AsyncSession,
    *,
    event_id: int,
    action: AuditAction,
    actor_user_id: int | None = None,
    auction_player_id: int | None = None,
    team_id: int | None = None,
    amount: int | None = None,
    previous_amount: int | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        AuctionAuditLog(
            event_id=event_id,
            action=action,
            actor_user_id=actor_user_id,
            auction_player_id=auction_player_id,
            team_id=team_id,
            amount=amount,
            previous_amount=previous_amount,
            detail=detail,
        )
    )


# ── Loading helpers ───────────────────────────────────────────────────────────


async def _load_slabs(db: AsyncSession, event_id: int) -> list[Slab]:
    result = await db.execute(
        select(BidSlab).where(BidSlab.event_id == event_id).order_by(BidSlab.sort_order)
    )
    return [
        Slab(
            min_value=s.min_value,
            max_value=s.max_value,
            increment=s.increment,
            sort_order=s.sort_order,
        )
        for s in result.scalars().all()
    ]


async def _effective_base_price(ap: AuctionPlayer, db: AsyncSession) -> int:
    """A category's base price overrides the player's own, when set."""
    if ap.category_id:
        result = await db.execute(
            select(PlayerCategory).where(PlayerCategory.id == ap.category_id)
        )
        category = result.scalar_one_or_none()
        if category and category.base_price and category.base_price > 0:
            return category.base_price
    return ap.base_price


async def _get_event(db: AsyncSession, event_id: int) -> AuctionEvent:
    result = await db.execute(
        select(AuctionEvent).where(
            AuctionEvent.id == event_id, AuctionEvent.deleted_at.is_(None)
        )
    )
    event = result.scalar_one_or_none()
    if not event:
        raise AuctionError("Event not found")
    return event


async def _squad_size(db: AsyncSession, event_id: int, team_id: int) -> int:
    result = await db.execute(
        select(TeamPlayer).where(
            TeamPlayer.event_id == event_id, TeamPlayer.team_id == team_id
        )
    )
    return len(result.scalars().all())


# ── State ─────────────────────────────────────────────────────────────────────


async def get_auction_state(event_id: int, db: AsyncSession) -> dict:
    """Full snapshot sent on WebSocket connect and on demand.

    Carries `deadline_epoch` rather than a seconds-remaining integer, so a
    client that reconnects mid-player computes the correct countdown itself and
    the server does not have to broadcast a tick every second.
    """
    event_result = await db.execute(
        select(AuctionEvent)
        .where(AuctionEvent.id == event_id)
        .options(selectinload(AuctionEvent.auction_players).selectinload(AuctionPlayer.player))
    )
    event = event_result.scalar_one_or_none()
    if not event:
        return {}

    teams_result = await db.execute(
        select(Team)
        .where(Team.event_id == event_id)
        .options(selectinload(Team.players).selectinload(TeamPlayer.player), selectinload(Team.captain))
    )
    teams = teams_result.scalars().all()

    auction_players = event.auction_players

    slabs = await _load_slabs(db, event_id)

    cats_result = await db.execute(
        select(PlayerCategory)
        .where(PlayerCategory.event_id == event_id)
        .order_by(PlayerCategory.sort_order)
    )
    categories = cats_result.scalars().all()
    categories_by_id = {c.id: c for c in categories}

    active_player = next(
        (ap for ap in auction_players if ap.status == PlayerAuctionStatus.active),
        None,
    )
    deadline_dt = event.deadline_at
    if deadline_dt is not None and deadline_dt.tzinfo is None:
        deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
    deadline = deadline_dt.timestamp() if deadline_dt else None
    active_player_id = active_player.id if active_player else None

    timing = Timing.for_event(event)

    def base_for(ap: AuctionPlayer) -> int:
        category = categories_by_id.get(ap.category_id) if ap.category_id else None
        if category and category.base_price > 0:
            return category.base_price
        return ap.base_price

    return {
        "type": "auction_state",
        "event_id": event_id,
        "status": event.status,
        "scheduled_at": event.scheduled_at.isoformat() if event.scheduled_at else None,
        "server_time": _now(),
        "deadline_epoch": deadline,
        "active_auction_player_id": int(active_player_id) if active_player_id else None,
        "no_timer": False,
        "auto_advance": event.auto_advance,
        "timing": {
            "base_seconds": timing.base_seconds,
            "soft_close_threshold_seconds": timing.threshold_seconds,
            "soft_close_extension_seconds": timing.extension_seconds,
        },
        "rules": {
            "team_budget": event.team_budget,
            "team_max_players": event.team_max_players,
            "team_min_players": event.team_min_players,
            "player_base_price": event.player_base_price,
            "auction_mode": event.auction_mode,
            "rtm_enabled": event.rtm_enabled,
            "rtm_cards_per_team": event.rtm_cards_per_team,
            "exclude_captains_from_auction": event.exclude_captains_from_auction,
            "auto_reauction_unsold": event.auto_reauction_unsold,
        },
        "rtm": await get_rtm_state(event_id, db),
        "nominations": (
            await list_nominations(event_id, db)
            if event.auction_mode == AuctionMode.nomination
            else []
        ),
        "bid_slabs": [
            {
                "id": idx,
                "min_value": s.min_value,
                "max_value": s.max_value,
                "increment": s.increment,
                "sort_order": s.sort_order,
            }
            for idx, s in enumerate(slabs)
        ],
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "base_price": c.base_price,
                "sort_order": c.sort_order,
                "auction_first": c.auction_first,
            }
            for c in categories
        ],
        "teams": [
            {
                "id": t.id,
                "name": t.name,
                "color": t.color,
                "logo": t.logo,
                "captain_id": t.captain_id,
                "captain_name": t.captain.name if t.captain else None,
                "budget": t.budget,
                "spent": t.spent,
                "remaining": t.budget - t.spent,
                "max_players": t.max_players,
                "player_count": len(t.players),
                "rtm_cards_used": t.rtm_cards_used,
            }
            for t in teams
        ],
        "players": [
            {
                "id": ap.id,
                "player_id": ap.player_id,
                "name": ap.player.name if ap.player else f"Player #{ap.player_id}",
                "profile_photo": ap.player.profile_photo if ap.player else None,
                "base_price": base_for(ap),
                "category_id": ap.category_id,
                "current_bid": (
                    base_for(ap)
                    if (ap.status == PlayerAuctionStatus.active and ap.current_bid_team_id is None)
                    else ap.current_bid
                ),
                "current_bid_team_id": ap.current_bid_team_id,
                "status": ap.status,
                "version": ap.version,
                "rtm_team_id": ap.rtm_team_id,
                "rtm_stage": ap.rtm_stage,
                "rtm_top_team_id": ap.rtm_top_team_id,
                "rtm_top_amount": ap.rtm_top_amount,
            }
            for ap in auction_players
        ],
    }


async def get_auction_summary(event_id: int, db: AsyncSession) -> dict:
    event = await _get_event(db, event_id)

    teams_result = await db.execute(select(Team).where(Team.event_id == event_id))
    teams = teams_result.scalars().all()

    aps_result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    auction_players = aps_result.scalars().all()

    sold = [ap for ap in auction_players if ap.status == PlayerAuctionStatus.sold]
    unsold = [ap for ap in auction_players if ap.status == PlayerAuctionStatus.unsold]

    teams_by_id = {t.id: t for t in teams}

    highest_bid_player = None
    if sold:
        top = max(sold, key=lambda ap: ap.current_bid)
        top_team = teams_by_id.get(top.current_bid_team_id)
        highest_bid_player = {
            "player_id": top.player_id,
            "player_name": top.player.name if top.player else f"Player #{top.player_id}",
            "sold_price": top.current_bid,
            "team_name": top_team.name if top_team else "Unknown Team",
        }

    teams_summary = []
    strongest_team = None
    best_rating = -1.0

    for team in teams:
        roster = []
        batting_total = bowling_total = fielding_total = rating_total = 0.0

        for tp in team.players or []:
            player = tp.player
            batting = player.batting_rating if player else 0.0
            bowling = player.bowling_rating if player else 0.0
            fielding = player.fielding_rating if player else 0.0
            score = batting + bowling + fielding
            rating_total += score
            batting_total += batting
            bowling_total += bowling
            fielding_total += fielding
            roster.append(
                {
                    "player_id": tp.player_id,
                    "name": player.name if player else f"Player #{tp.player_id}",
                    "sold_price": tp.sold_price,
                    "rating_score": round(score, 2),
                    "is_captain": tp.player_id == team.captain_id,
                }
            )

        roster.sort(key=lambda r: r["sold_price"], reverse=True)
        count = len(roster)
        batting_avg = batting_total / count if count else 0.0
        bowling_avg = bowling_total / count if count else 0.0
        fielding_avg = fielding_total / count if count else 0.0
        overall = (batting_avg + bowling_avg + fielding_avg) / 3 if count else 0.0

        teams_summary.append(
            {
                "team_id": team.id,
                "team_name": team.name,
                "captain_id": team.captain_id,
                "captain_name": team.captain.name if team.captain else None,
                "spent": team.spent,
                "budget": team.budget,
                "remaining": team.budget - team.spent,
                "player_count": count,
                "average_rating": round(overall, 2),
                "total_rating": round(rating_total, 2),
                "batting_avg": round(batting_avg, 2),
                "bowling_avg": round(bowling_avg, 2),
                "fielding_avg": round(fielding_avg, 2),
                "overall_rating": round(overall, 2),
                "players": roster,
            }
        )

        if count and overall > best_rating:
            best_rating = overall
            strongest_team = {
                "team_id": team.id,
                "team_name": team.name,
                "overall_rating": round(overall, 2),
                "batting_avg": round(batting_avg, 2),
                "bowling_avg": round(bowling_avg, 2),
                "fielding_avg": round(fielding_avg, 2),
                "player_count": count,
            }

    if event.status == AuctionStatus.completed and event.total_viewers is not None:
        viewer_stats = {"live_viewers": 0, "total_unique_viewers": event.total_viewers}
    else:
        viewer_stats = await manager.get_viewer_stats(event_id)

    return {
        "event_id": event.id,
        "event_name": event.name,
        "status": event.status,
        "highest_bid_player": highest_bid_player,
        "strongest_team": strongest_team,
        "teams": teams_summary,
        "unsold_players": [
            {
                "player_id": ap.player_id,
                "name": ap.player.name if ap.player else f"Player #{ap.player_id}",
                "base_price": ap.base_price,
                "last_bid": ap.current_bid,
            }
            for ap in unsold
        ],
        "stats": {
            "total_players": len(auction_players),
            "sold_count": len(sold),
            "unsold_count": len(unsold),
        },
        "viewer_stats": viewer_stats,
    }


# ── Lifecycle ─────────────────────────────────────────────────────────────────


async def start_auction(event_id: int, db: AsyncSession, actor: User | None = None) -> AuctionEvent:
    event = await _get_event(db, event_id)

    if event.status not in (AuctionStatus.ready, AuctionStatus.paused):
        raise AuctionError(f"Cannot start an auction in status: {event.status}")

    was_first_start = event.status == AuctionStatus.ready

    if event.scheduled_at is not None and was_first_start:
        now = datetime.now(timezone.utc)
        scheduled = event.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        if now < scheduled:
            raise AuctionError(
                f"Auction is scheduled for {scheduled.isoformat()} and cannot start before then"
            )

    active_player = await _load_active_player(db, event_id)
    event.status = AuctionStatus.active
    if active_player and not was_first_start:
        remaining = event.paused_remaining_seconds
        if remaining is None:
            remaining = Timing.for_event(event).base_seconds
        event.deadline_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, remaining))
        event.paused_remaining_seconds = None
    await record_audit(
        db,
        event_id=event_id,
        action=AuditAction.auction_started if was_first_start else AuditAction.auction_resumed,
        actor_user_id=actor.id if actor else None,
    )
    await db.commit()
    await db.refresh(event)

    # Initial player selection is handled by the backend, never by a browser timer.
    await manager.broadcast(
        event_id,
        {"type": "auction_started" if was_first_start else "auction_resumed", "event_id": event_id},
    )

    if was_first_start:
        await _notify_participants_starting(event, db)

    if not active_player:
        try:
            await set_next_player(event_id, None, db, actor=actor)
        except AuctionError as exc:
            logger.info(
                "Event %s started with no player on the block: %s", event_id, exc
            )

    return event


async def _notify_participants_starting(event: AuctionEvent, db: AsyncSession) -> None:
    """Email the operators. Players have no accounts and may have no email."""
    recipient_ids = {
        uid
        for uid in (event.auctioneer_id, event.organizer_id, event.admin_id)
        if uid is not None
    }
    if not recipient_ids:
        return
    users = await db.execute(select(User).where(User.id.in_(recipient_ids)))

    async def notify(user: User) -> None:
        role = (
            "auctioneer" if user.id == event.auctioneer_id
            else "organizer" if user.id == event.organizer_id
            else "admin"
        )
        try:
            await asyncio.wait_for(
                dispatch_email("task_send_auction_starting", user.email, user.name, event.name, event.id, role),
                timeout=1.5,
            )
        except Exception as exc:
            logger.warning("Skipping/slow start email enqueue for %s: %s", user.email, exc)

    # Email is dispatched locally and never delays auction state changes.
    for user in users.scalars().all():
        asyncio.create_task(notify(user))


async def pause_auction(event_id: int, db: AsyncSession, actor: User | None = None) -> AuctionEvent:
    event = await _get_event(db, event_id)
    if event.status != AuctionStatus.active:
        raise AuctionError("Auction is not active")

    deadline = event.deadline_at
    if deadline is not None and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    event.paused_remaining_seconds = (
        max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))
        if deadline is not None else None
    )
    event.deadline_at = None
    event.advance_at = None
    event.status = AuctionStatus.paused
    await record_audit(
        db, event_id=event_id, action=AuditAction.auction_paused,
        actor_user_id=actor.id if actor else None,
    )
    await db.commit()
    await db.refresh(event)

    await manager.broadcast(event_id, {"type": "auction_paused", "event_id": event_id})
    return event


async def finish_auction(event_id: int, db: AsyncSession, actor: User | None = None) -> AuctionEvent:
    event = await _get_event(db, event_id)
    if event.status not in (AuctionStatus.active, AuctionStatus.paused):
        raise AuctionError("Auction must be active or paused to finish")

    active_result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.active,
        )
    )
    for ap in active_result.scalars().all():
        ap.status = PlayerAuctionStatus.unsold

    viewer_stats = await manager.get_viewer_stats(event_id)
    event.total_viewers = viewer_stats.get("total_unique_viewers", 0)
    event.status = AuctionStatus.completed
    event.deadline_at = None
    event.advance_at = None
    event.paused_remaining_seconds = None

    await record_audit(
        db, event_id=event_id, action=AuditAction.auction_finished,
        actor_user_id=actor.id if actor else None,
    )
    await db.commit()
    await db.refresh(event)

    summary = await get_auction_summary(event_id, db)

    recipient_ids = {
        uid
        for uid in (event.auctioneer_id, event.organizer_id, event.admin_id)
        if uid is not None
    }
    if recipient_ids:
        users = await db.execute(select(User).where(User.id.in_(recipient_ids)))
        for user in users.scalars().all():
            try:
                await asyncio.wait_for(
                    dispatch_email(
                        "task_send_event_completion_summary",
                        user.email, user.name, event.name, event_id, summary,
                    ),
                    timeout=1.5,
                )
            except Exception as exc:
                logger.warning("Skipping/slow summary email enqueue for %s: %s", user.email, exc)

    await manager.broadcast(
        event_id, {"type": "auction_completed", "event_id": event_id, "summary": summary}
    )
    return event


# ── Putting a player on the block ─────────────────────────────────────────────


async def set_next_player(
    event_id: int,
    auction_player_id: Optional[int],
    db: AsyncSession,
    actor: User | None = None,
    *,
    idempotency_key: str | None = None,
) -> AuctionPlayer:
    request_started = time.perf_counter()
    selection_started = request_started
    event = await _get_event(db, event_id)
    if event.status != AuctionStatus.active:
        raise AuctionError("Auction is not active")

    active_result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.active,
        )
    )
    still_active = active_result.scalars().first()
    if still_active:
        raise AuctionError(
            "A player is still on the block. Hammer the sale or pull the player first."
        )

    if auction_player_id is None:
        if event.auction_mode == AuctionMode.nomination:
            ap = await _next_nominated(db, event_id)
        else:
            ap = await _pick_random_pending(db, event_id)
    else:
        result = await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.id == auction_player_id,
                AuctionPlayer.event_id == event_id,
            )
        )
        ap = result.scalar_one_or_none()
        if not ap:
            raise AuctionError("Player is not registered in this event")
        if ap.status not in (PlayerAuctionStatus.pending, PlayerAuctionStatus.unsold):
            raise AuctionError("Player has already been auctioned")

    if ap.player_id in await _captain_player_ids(db, event_id):
        raise AuctionError("Assigned team captains are excluded from the auction")

    selection_ms = (time.perf_counter() - selection_started) * 1000
    base = await _effective_base_price(ap, db)
    claim = await db.execute(
        update(AuctionPlayer)
        .where(
            AuctionPlayer.id == ap.id,
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status.in_(
                [PlayerAuctionStatus.pending, PlayerAuctionStatus.unsold]
            ),
            AuctionPlayer.version == ap.version,
        )
        .values(
            base_price=base,
            status=PlayerAuctionStatus.active,
            current_bid=base,
            current_bid_team_id=None,
            version=AuctionPlayer.version + 1,
            extension_count=0,
        )
    )
    if claim.rowcount == 0:
        await db.rollback()
        raise ConcurrentUpdate("Another request selected this player first. Refresh the auction state.")
    timing = Timing.for_event(event)
    deadline = _now() + timing.base_seconds
    event.deadline_at = datetime.fromtimestamp(deadline, tz=timezone.utc)
    event.advance_at = None
    event.paused_remaining_seconds = None
    event.no_timer = False

    await record_audit(
        db, event_id=event_id, action=AuditAction.player_put_up,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, amount=base,
    )
    commit_started = time.perf_counter()
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConcurrentUpdate("Another request selected a player first. Refresh the auction state.") from exc
    await db.refresh(ap)
    commit_ms = (time.perf_counter() - commit_started) * 1000

    deadline = event.deadline_at.timestamp() if event.deadline_at else None

    broadcast_started = time.perf_counter()
    await manager.broadcast(
        event_id,
        {
            "type": "player_up",
            "event_id": event_id,
            "auction_player_id": ap.id,
            "player_id": ap.player_id,
            "player_name": ap.player.name if ap.player else None,
            "profile_photo": ap.player.profile_photo if ap.player else None,
            "batting_rating": ap.player.batting_rating if ap.player else None,
            "bowling_rating": ap.player.bowling_rating if ap.player else None,
            "fielding_rating": ap.player.fielding_rating if ap.player else None,
            "age": ap.player.age if ap.player else None,
            "base_price": ap.base_price,
            "current_bid": ap.current_bid,
            "version": ap.version,
            "no_timer": False,
            "deadline_epoch": deadline,
            "server_time": _now(),
        },
    )
    broadcast_ms = (time.perf_counter() - broadcast_started) * 1000
    total_ms = (time.perf_counter() - request_started) * 1000
    logger.info(
        "player_up dispatch event_id=%s auction_player_id=%s "
        "selection_ms=%.1f commit_ms=%.1f broadcast_ms=%.1f total_ms=%.1f",
        event_id, ap.id, selection_ms, commit_ms, broadcast_ms, total_ms,
    )
    return ap


async def _pick_random_pending(db: AsyncSession, event_id: int) -> AuctionPlayer:
    """Categories flagged `auction_first` come up first, in sort order."""
    event = await _get_event(db, event_id)
    captain_ids = await _captain_player_ids(db, event_id)

    categories_result = await db.execute(
        select(PlayerCategory).where(PlayerCategory.event_id == event_id)
    )
    categories = categories_result.scalars().all()

    for category in sorted(
        [c for c in categories if c.auction_first], key=lambda c: c.sort_order
    ):
        result = await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.event_id == event_id,
                AuctionPlayer.status == PlayerAuctionStatus.pending,
                AuctionPlayer.category_id == category.id,
            )
        )
        pending = [p for p in result.scalars().all() if p.player_id not in captain_ids]
        if pending:
            return random.choice(pending)

    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.pending,
        )
    )
    pending = [p for p in result.scalars().all() if p.player_id not in captain_ids]
    if not pending and event.auto_reauction_unsold and not event.unsold_reauction_started:
        unsold_result = await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.event_id == event_id,
                AuctionPlayer.status == PlayerAuctionStatus.unsold,
            )
        )
        unsold = unsold_result.scalars().all()
        if unsold:
            for player in unsold:
                player.status = PlayerAuctionStatus.pending
                player.version += 1
            event.unsold_reauction_started = True
            pending = [p for p in unsold if p.player_id not in captain_ids]
    if not pending:
        raise AuctionError("No pending players left")
    return random.choice(pending)


async def pull_player(
    event_id: int, db: AsyncSession, actor: User | None = None
) -> AuctionPlayer | None:
    """Take the current player off the block without selling."""
    ap = await _load_active_player(db, event_id)
    if not ap:
        raise AuctionError("No player is on the block")

    ap.status = PlayerAuctionStatus.unsold
    ap.version += 1
    await record_audit(
        db, event_id=event_id, action=AuditAction.player_pulled,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, amount=ap.current_bid,
    )
    event = await _get_event(db, event_id)
    event.deadline_at = None
    event.advance_at = (
        datetime.now(timezone.utc) + timedelta(seconds=1.5)
        if event.auto_advance else None
    )
    await db.commit()

    await manager.broadcast(
        event_id,
        {
            "type": "player_pulled",
            "event_id": event_id,
            "auction_player_id": ap.id,
            "player_id": ap.player_id,
            "reason": "withdrawn_by_auctioneer",
        },
    )
    return ap


async def _load_active_player(db: AsyncSession, event_id: int) -> AuctionPlayer | None:
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.active,
        )
    )
    return result.scalars().first()


# ── Bidding ───────────────────────────────────────────────────────────────────


async def place_bid(
    event_id: int,
    team_id: int,
    amount: int,
    db: AsyncSession,
    actor: User | None = None,
    *,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> Bid:
    if idempotency_key:
        existing = await db.execute(
            select(Bid).where(
                Bid.event_id == event_id, Bid.idempotency_key == idempotency_key
            )
        )
        prior = existing.scalar_one_or_none()
        if prior:
            return prior

    event = await _get_event(db, event_id)
    if event.status != AuctionStatus.active:
        raise AuctionError("Auction is not active")

    ap = await _load_active_player(db, event_id)
    if not ap:
        raise AuctionError("No player is on the block")

    if expected_version is not None and expected_version != ap.version:
        raise ConcurrentUpdate(
            f"The price has moved — current bid is {ap.current_bid}. Review and bid again."
        )

    team_result = await db.execute(
        select(Team).where(Team.id == team_id, Team.event_id == event_id)
    )
    team = team_result.scalar_one_or_none()
    if not team:
        raise AuctionError("That team is not in this event")

    if ap.current_bid_team_id == team_id:
        raise AuctionError(f"{team.name} already holds the top bid")

    if team.captain_id == ap.player_id:
        raise AuctionError("A team cannot bid for its own captain")

    base_price = await _effective_base_price(ap, db)
    slabs = await _load_slabs(db, event_id)
    squad_size = await _squad_size(db, event_id, team_id)

    error = bid_rules.validate_bid(
        amount=amount,
        current_bid=ap.current_bid,
        has_existing_bid=ap.current_bid_team_id is not None,
        base_price=base_price,
        slabs=slabs,
        team_budget=team.budget,
        team_spent=team.spent,
        team_squad_size=squad_size,
        team_max_players=team.max_players,
        min_squad=event.team_min_players,
        event_base_price=event.player_base_price,
    )
    if error:
        raise AuctionError(error)

    timing = Timing.for_event(event)
    current_deadline = event.deadline_at
    if current_deadline is not None and current_deadline.tzinfo is None:
        current_deadline = current_deadline.replace(tzinfo=timezone.utc)
    current_deadline_epoch = current_deadline.timestamp() if current_deadline else None
    if current_deadline_epoch is None:
        new_deadline = _now() + timing.base_seconds
        new_extension_count = ap.extension_count
    else:
        remaining = current_deadline_epoch - _now()
        extension = timing.extension_for(ap.extension_count)
        new_deadline = (
            _now() + extension
            if remaining <= timing.threshold_seconds
            else current_deadline_epoch
        )
        new_extension_count = (
            ap.extension_count + 1
            if remaining <= timing.threshold_seconds
            else ap.extension_count
        )

    stmt = (
        update(AuctionPlayer)
        .where(
            AuctionPlayer.id == ap.id,
            AuctionPlayer.version == ap.version,
            AuctionPlayer.status == PlayerAuctionStatus.active,
        )
        .values(
            current_bid=amount,
            current_bid_team_id=team_id,
            version=AuctionPlayer.version + 1,
            extension_count=new_extension_count,
        )
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        await db.rollback()
        raise ConcurrentUpdate(
            "Another bid landed first. The price has moved — review and bid again."
        )

    bid = Bid(
        event_id=event_id,
        auction_player_id=ap.id,
        team_id=team_id,
        amount=amount,
        idempotency_key=idempotency_key or uuid.uuid4().hex,
        placed_by_user_id=actor.id if actor else None,
    )
    event.deadline_at = datetime.fromtimestamp(new_deadline, tz=timezone.utc)
    db.add(bid)
    await record_audit(
        db, event_id=event_id, action=AuditAction.bid_placed,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, team_id=team_id, amount=amount,
        previous_amount=ap.current_bid,
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.execute(
            select(Bid).where(
                Bid.event_id == event_id, Bid.idempotency_key == idempotency_key
            )
        )
        prior = existing.scalar_one_or_none()
        if prior:
            return prior
        raise

    await db.refresh(bid)

    await manager.broadcast(
        event_id,
        {
            "type": "new_bid",
            "event_id": event_id,
            "auction_player_id": ap.id,
            "team_id": team_id,
            "team_name": team.name,
            "amount": amount,
            "current_bid": amount,
            "version": ap.version + 1,
            "deadline_epoch": new_deadline,
            "server_time": _now(),
        },
    )
    return bid


# ── Hammer ────────────────────────────────────────────────────────────────────


async def hammer_player(
    event_id: int,
    auction_player_id: int,
    db: AsyncSession,
    actor: User | None = None,
    *,
    idempotency_key: str | None = None,
) -> AuctionPlayer | None:
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.id == auction_player_id, AuctionPlayer.event_id == event_id
        )
    )
    ap = result.scalar_one_or_none()
    if not ap:
        return None
    if ap.status != PlayerAuctionStatus.active:
        return ap

    sold = ap.current_bid_team_id is not None
    sale_price = ap.current_bid
    winning_team_id = ap.current_bid_team_id

    event = await _get_event(db, event_id)
    holder = await rtm_service.rights_holder_for(db, event, ap, winning_team_id)
    if holder is not None:
        return await _open_rtm(
            db, event_id=event_id, ap=ap, holder=holder,
            top_team_id=winning_team_id, top_amount=sale_price, actor=actor,
        )

    new_status = PlayerAuctionStatus.sold if sold else PlayerAuctionStatus.unsold

    claim = await db.execute(
        update(AuctionPlayer)
        .where(
            AuctionPlayer.id == ap.id,
            AuctionPlayer.version == ap.version,
            AuctionPlayer.status == PlayerAuctionStatus.active,
        )
        .values(status=new_status, version=AuctionPlayer.version + 1)
    )
    if claim.rowcount == 0:
        await db.rollback()
        await db.refresh(ap)
        return ap

    event.deadline_at = None
    event.paused_remaining_seconds = None
    event.advance_at = (
        datetime.now(timezone.utc) + timedelta(seconds=1.5)
        if event.auto_advance else None
    )

    await _record_outcome(
        db,
        event_id=event_id,
        ap=ap,
        team_id=winning_team_id if sold else None,
        amount=sale_price if sold else None,
        actor=actor,
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning(
            "Duplicate sale blocked by constraint: event=%s auction_player=%s",
            event_id, ap.id,
        )
        await db.refresh(ap)
        return ap

    await db.refresh(ap)
    await _broadcast_outcome(
        event_id, ap, winning_team_id if sold else None, sale_price if sold else None
    )
    return ap


async def _record_outcome(
    db: AsyncSession,
    *,
    event_id: int,
    ap: AuctionPlayer,
    team_id: int | None,
    amount: int | None,
    actor: User | None,
    rtm_used: bool = False,
) -> None:
    if team_id is not None and amount is not None:
        db.add(
            TeamPlayer(
                event_id=event_id,
                team_id=team_id,
                player_id=ap.player_id,
                sold_price=amount,
            )
        )
        values: dict = {"spent": Team.spent + amount}
        if rtm_used:
            values["rtm_cards_used"] = Team.rtm_cards_used + 1
        await db.execute(update(Team).where(Team.id == team_id).values(**values))

        await record_audit(
            db, event_id=event_id, action=AuditAction.player_sold,
            actor_user_id=actor.id if actor else None,
            auction_player_id=ap.id, team_id=team_id, amount=amount,
            detail="Acquired via Right to Match" if rtm_used else None,
        )
    else:
        await record_audit(
            db, event_id=event_id, action=AuditAction.player_unsold,
            actor_user_id=actor.id if actor else None,
            auction_player_id=ap.id,
        )


async def _broadcast_outcome(
    event_id: int,
    ap: AuctionPlayer,
    team_id: int | None,
    amount: int | None,
    *,
    via_rtm: bool = False,
) -> None:
    if team_id is not None:
        await manager.broadcast(
            event_id,
            {
                "type": "player_sold",
                "event_id": event_id,
                "auction_player_id": ap.id,
                "player_id": ap.player_id,
                "player_name": ap.player.name if ap.player else None,
                "sold_to_team_id": team_id,
                "sold_price": amount,
                "via_rtm": via_rtm,
            },
        )
    else:
        await manager.broadcast(
            event_id,
            {
                "type": "player_unsold",
                "event_id": event_id,
                "auction_player_id": ap.id,
                "player_id": ap.player_id,
                "player_name": ap.player.name if ap.player else None,
            },
        )


# ── Operator corrections ──────────────────────────────────────────────────────


async def undo_last_bid(
    event_id: int, db: AsyncSession, actor: User | None = None
) -> dict:
    ap = await _load_active_player(db, event_id)
    if not ap:
        raise AuctionError("No player is on the block")

    bids_result = await db.execute(
        select(Bid)
        .where(
            Bid.event_id == event_id,
            Bid.auction_player_id == ap.id,
            Bid.reverted_at.is_(None),
        )
        .order_by(Bid.id.desc())
    )
    bids = bids_result.scalars().all()
    if not bids:
        raise AuctionError("There are no bids to undo")

    latest = bids[0]
    latest.reverted_at = datetime.now(timezone.utc)
    latest.reverted_by_user_id = actor.id if actor else None

    previous_amount = ap.current_bid
    if len(bids) > 1:
        prior = bids[1]
        ap.current_bid = prior.amount
        ap.current_bid_team_id = prior.team_id
    else:
        ap.current_bid = await _effective_base_price(ap, db)
        ap.current_bid_team_id = None
    ap.version += 1

    await record_audit(
        db, event_id=event_id, action=AuditAction.bid_reverted,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, team_id=latest.team_id,
        amount=ap.current_bid, previous_amount=previous_amount,
        detail=f"Reverted bid #{latest.id} of {latest.amount}",
    )
    await db.commit()
    await db.refresh(ap)

    await manager.broadcast(
        event_id,
        {
            "type": "bid_reverted",
            "event_id": event_id,
            "auction_player_id": ap.id,
            "current_bid": ap.current_bid,
            "current_bid_team_id": ap.current_bid_team_id,
            "reverted_amount": latest.amount,
            "version": ap.version,
        },
    )
    return {
        "current_bid": ap.current_bid,
        "current_bid_team_id": ap.current_bid_team_id,
        "version": ap.version,
    }


async def correct_current_bid(
    event_id: int, new_amount: int, db: AsyncSession, actor: User | None = None
) -> dict:
    ap = await _load_active_player(db, event_id)
    if not ap:
        raise AuctionError("No player is on the block")
    if new_amount < 0:
        raise AuctionError("Bid amount cannot be negative")

    previous = ap.current_bid

    if ap.current_bid_team_id is not None:
        team_result = await db.execute(select(Team).where(Team.id == ap.current_bid_team_id))
        team = team_result.scalar_one_or_none()
        if team and (team.budget - team.spent) < new_amount:
            raise AuctionError(
                f"{team.name} does not have enough purse ({team.budget - team.spent})"
            )

        latest_result = await db.execute(
            select(Bid)
            .where(
                Bid.event_id == event_id,
                Bid.auction_player_id == ap.id,
                Bid.reverted_at.is_(None),
            )
            .order_by(Bid.id.desc())
        )
        latest = latest_result.scalars().first()
        if latest:
            latest.amount = new_amount

    ap.current_bid = new_amount
    if ap.current_bid_team_id is None:
        ap.base_price = new_amount
    ap.version += 1

    await record_audit(
        db, event_id=event_id, action=AuditAction.bid_corrected,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, team_id=ap.current_bid_team_id,
        amount=new_amount, previous_amount=previous,
        detail="Manual correction by operator",
    )
    await db.commit()
    await db.refresh(ap)

    await manager.broadcast(
        event_id,
        {
            "type": "bid_corrected",
            "event_id": event_id,
            "auction_player_id": ap.id,
            "current_bid": ap.current_bid,
            "current_bid_team_id": ap.current_bid_team_id,
            "previous_amount": previous,
            "version": ap.version,
        },
    )
    return {
        "current_bid": ap.current_bid,
        "current_bid_team_id": ap.current_bid_team_id,
        "version": ap.version,
    }


# ── Local database timer and recovery ────────────────────────────────────────


async def run_due_hammers(db: AsyncSession, *, grace_seconds: float = 0.0) -> int:
    """Process expired deadlines and persisted automatic-advance requests.

    The database is the only clock/state store. The loop is safe to run
    repeatedly: hammer_player uses a conditional status/version update and
    the partial unique index permits at most one active player per event.
    """
    now = datetime.now(timezone.utc)

    # Older active auctions may have had their deadline only in Redis. Seed a
    # fresh deadline once from the event's configured base duration; subsequent
    # deadlines are always read from and written to the database.
    missing_result = await db.execute(
        select(AuctionEvent).where(
            AuctionEvent.status == AuctionStatus.active,
            AuctionEvent.deadline_at.is_(None),
        )
    )
    recovered_deadline = False
    for event in missing_result.scalars().all():
        active_player = await _load_active_player(db, event.id)
        if active_player:
            event.deadline_at = now + timedelta(seconds=Timing.for_event(event).base_seconds)
            recovered_deadline = True
    if recovered_deadline:
        await db.commit()

    due_result = await db.execute(
        select(AuctionEvent).where(
            AuctionEvent.status == AuctionStatus.active,
            AuctionEvent.deadline_at.is_not(None),
            AuctionEvent.deadline_at <= now - timedelta(seconds=grace_seconds),
        )
    )
    due_events = due_result.scalars().all()
    processed = 0
    for event in due_events:
        ap = await _load_active_player(db, event.id)
        if not ap:
            event.deadline_at = None
            await db.commit()
            continue
        try:
            await hammer_player(event.id, ap.id, db)
            processed += 1
        except Exception:
            await db.rollback()
            logger.exception("Local timer hammer failed for event %s", event.id)

    advance_result = await db.execute(
        select(AuctionEvent).where(
            AuctionEvent.status == AuctionStatus.active,
            AuctionEvent.advance_at.is_not(None),
            AuctionEvent.advance_at <= now,
        )
    )
    for event in advance_result.scalars().all():
        event.advance_at = None
        await db.commit()
        try:
            await set_next_player(event.id, None, db)
            processed += 1
        except AuctionError as exc:
            logger.info("Automatic progression stopped for event %s: %s", event.id, exc)
            await db.rollback()
            current = await db.get(AuctionEvent, event.id)
            if current:
                current.advance_at = None
                await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Automatic progression failed for event %s", event.id)
    return processed


async def auto_hammer(event_id: int, auction_player_id: int, db: AsyncSession) -> None:
    """Compatibility helper; verifies the persisted deadline before hammering."""
    event = await _get_event(db, event_id)
    deadline = event.deadline_at
    if deadline is None:
        return
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) < deadline:
        return
    await hammer_player(event_id, auction_player_id, db)


# ── Reset (development / mock runs) ───────────────────────────────────────────


async def reset_auction(event_id: int, db: AsyncSession, actor: User | None = None) -> None:
    event = await _get_event(db, event_id)
    event.unsold_reauction_started = False

    players_result = await db.execute(
        select(AuctionPlayer).where(AuctionPlayer.event_id == event_id)
    )
    for ap in players_result.scalars().all():
        ap.status = PlayerAuctionStatus.pending
        ap.current_bid = 0
        ap.current_bid_team_id = None
        ap.version += 1
        ap.extension_count = 0
        ap.rtm_stage = None
        ap.rtm_top_team_id = None
        ap.rtm_top_amount = None

    await db.execute(
        PlayerNomination.__table__.delete().where(PlayerNomination.event_id == event_id)
    )
    await db.execute(
        TeamPlayer.__table__.delete().where(TeamPlayer.event_id == event_id)
    )
    await db.execute(
        Bid.__table__.delete().where(Bid.event_id == event_id)
    )
    await db.execute(
        update(Team)
        .where(Team.event_id == event_id)
        .values(spent=0, budget=event.team_budget, rtm_cards_used=0)
    )

    event.status = AuctionStatus.ready
    await record_audit(
        db, event_id=event_id, action=AuditAction.auction_reset,
        actor_user_id=actor.id if actor else None,
    )
    event.deadline_at = None
    event.advance_at = None
    event.paused_remaining_seconds = None
    await db.commit()

    state = await get_auction_state(event_id, db)
    await manager.broadcast(event_id, {**state, "type": "auction_reset"})


# ── Right to Match (new in 2.1.0) ────────────────────────────────────────────


async def _open_rtm(
    db: AsyncSession,
    *,
    event_id: int,
    ap: AuctionPlayer,
    holder: Team,
    top_team_id: int | None,
    top_amount: int,
    actor: User | None,
) -> AuctionPlayer:
    claim = await db.execute(
        update(AuctionPlayer)
        .where(
            AuctionPlayer.id == ap.id,
            AuctionPlayer.version == ap.version,
            AuctionPlayer.status == PlayerAuctionStatus.active,
        )
        .values(
            status=PlayerAuctionStatus.rtm_pending,
            rtm_stage=RtmStage.offered,
            rtm_top_team_id=top_team_id,
            rtm_top_amount=top_amount,
            version=AuctionPlayer.version + 1,
        )
    )
    if claim.rowcount == 0:
        await db.rollback()
        await db.refresh(ap)
        return ap

    event = await _get_event(db, event_id)
    event.deadline_at = None
    event.advance_at = None
    event.paused_remaining_seconds = None

    await record_audit(
        db, event_id=event_id, action=AuditAction.rtm_offered,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, team_id=holder.id, amount=top_amount,
        detail=f"RTM offered to {holder.name} against a bid of {top_amount}",
    )
    await db.commit()
    await db.refresh(ap)

    await manager.broadcast(
        event_id,
        {
            "type": "rtm_offered",
            "event_id": event_id,
            "auction_player_id": ap.id,
            "player_id": ap.player_id,
            "player_name": ap.player.name if ap.player else None,
            "rights_team_id": holder.id,
            "rights_team_name": holder.name,
            "top_team_id": top_team_id,
            "top_amount": top_amount,
            "stage": RtmStage.offered.value,
            "version": ap.version,
        },
    )
    return ap


async def _load_rtm_player(db: AsyncSession, event_id: int) -> AuctionPlayer:
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.rtm_pending,
        )
    )
    ap = result.scalars().first()
    if not ap:
        raise AuctionError("No Right-to-Match decision is pending")
    return ap


async def _settle_rtm(
    db: AsyncSession,
    *,
    event_id: int,
    ap: AuctionPlayer,
    team_id: int,
    amount: int,
    actor: User | None,
    rtm_used: bool,
    action: AuditAction,
    detail: str,
) -> AuctionPlayer:
    claim = await db.execute(
        update(AuctionPlayer)
        .where(
            AuctionPlayer.id == ap.id,
            AuctionPlayer.version == ap.version,
            AuctionPlayer.status == PlayerAuctionStatus.rtm_pending,
        )
        .values(
            status=PlayerAuctionStatus.sold,
            current_bid=amount,
            current_bid_team_id=team_id,
            rtm_stage=None,
            version=AuctionPlayer.version + 1,
        )
    )
    if claim.rowcount == 0:
        await db.rollback()
        raise ConcurrentUpdate("The other console resolved this first")

    event = await _get_event(db, event_id)
    event.deadline_at = None
    event.advance_at = (
        datetime.now(timezone.utc) + timedelta(seconds=1.5)
        if event.auto_advance else None
    )
    await record_audit(
        db, event_id=event_id, action=action,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, team_id=team_id, amount=amount, detail=detail,
    )
    await _record_outcome(
        db, event_id=event_id, ap=ap, team_id=team_id, amount=amount,
        actor=actor, rtm_used=rtm_used,
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning("Duplicate RTM sale blocked: event=%s ap=%s", event_id, ap.id)
        await db.refresh(ap)
        return ap

    await db.refresh(ap)
    await _broadcast_outcome(event_id, ap, team_id, amount, via_rtm=rtm_used)
    return ap


async def rtm_decline(
    event_id: int, db: AsyncSession, actor: User | None = None
) -> AuctionPlayer:
    ap = await _load_rtm_player(db, event_id)
    rtm_service._require_stage(ap, RtmStage.offered)

    amount = ap.rtm_top_amount or ap.current_bid
    if ap.rtm_top_team_id is None:
        raise AuctionError("There is no winning bidder to sell to")

    return await _settle_rtm(
        db, event_id=event_id, ap=ap, team_id=ap.rtm_top_team_id, amount=amount,
        actor=actor, rtm_used=False, action=AuditAction.rtm_declined,
        detail="Rights holder declined to match",
    )


async def rtm_exercise(
    event_id: int, db: AsyncSession, actor: User | None = None
) -> dict:
    ap = await _load_rtm_player(db, event_id)
    rtm_service._require_stage(ap, RtmStage.offered)

    event = await _get_event(db, event_id)
    holder = await rtm_service.validate_match(db, event, ap)

    await rtm_service._advance(db, ap, stage=RtmStage.raise_offered)
    await record_audit(
        db, event_id=event_id, action=AuditAction.rtm_exercised,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, team_id=holder.id,
        amount=ap.rtm_top_amount,
        detail=f"{holder.name} exercised RTM; top bidder may raise once",
    )
    await db.commit()
    await db.refresh(ap)

    await manager.broadcast(
        event_id,
        {
            "type": "rtm_exercised",
            "event_id": event_id,
            "auction_player_id": ap.id,
            "rights_team_id": holder.id,
            "top_team_id": ap.rtm_top_team_id,
            "top_amount": ap.rtm_top_amount,
            "stage": RtmStage.raise_offered.value,
            "version": ap.version,
        },
    )
    return {"stage": ap.rtm_stage, "top_amount": ap.rtm_top_amount, "version": ap.version}


async def rtm_raise(
    event_id: int, amount: int, db: AsyncSession, actor: User | None = None
) -> dict:
    ap = await _load_rtm_player(db, event_id)
    rtm_service._require_stage(ap, RtmStage.raise_offered)

    event = await _get_event(db, event_id)
    await rtm_service.validate_raise(db, event, ap, amount)

    previous = ap.rtm_top_amount
    await rtm_service._advance(db, ap, stage=RtmStage.match_offered, top_amount=amount)
    await record_audit(
        db, event_id=event_id, action=AuditAction.rtm_raised,
        actor_user_id=actor.id if actor else None,
        auction_player_id=ap.id, team_id=ap.rtm_top_team_id,
        amount=amount, previous_amount=previous,
        detail="Top bidder raised; rights holder must match",
    )
    await db.commit()
    await db.refresh(ap)

    await manager.broadcast(
        event_id,
        {
            "type": "rtm_raised",
            "event_id": event_id,
            "auction_player_id": ap.id,
            "rights_team_id": ap.rtm_team_id,
            "top_team_id": ap.rtm_top_team_id,
            "top_amount": amount,
            "previous_amount": previous,
            "stage": RtmStage.match_offered.value,
            "version": ap.version,
        },
    )
    return {"stage": ap.rtm_stage, "top_amount": amount, "version": ap.version}


async def rtm_skip_raise(
    event_id: int, db: AsyncSession, actor: User | None = None
) -> AuctionPlayer:
    ap = await _load_rtm_player(db, event_id)
    rtm_service._require_stage(ap, RtmStage.raise_offered)

    event = await _get_event(db, event_id)
    holder = await rtm_service.validate_match(db, event, ap)
    amount = ap.rtm_top_amount or ap.current_bid

    return await _settle_rtm(
        db, event_id=event_id, ap=ap, team_id=holder.id, amount=amount,
        actor=actor, rtm_used=True, action=AuditAction.rtm_raise_skipped,
        detail=f"Top bidder did not raise; {holder.name} matched at {amount}",
    )


async def rtm_match(
    event_id: int, db: AsyncSession, actor: User | None = None
) -> AuctionPlayer:
    ap = await _load_rtm_player(db, event_id)
    rtm_service._require_stage(ap, RtmStage.match_offered)

    event = await _get_event(db, event_id)
    holder = await rtm_service.validate_match(db, event, ap)
    amount = ap.rtm_top_amount or ap.current_bid

    return await _settle_rtm(
        db, event_id=event_id, ap=ap, team_id=holder.id, amount=amount,
        actor=actor, rtm_used=True, action=AuditAction.rtm_matched,
        detail=f"{holder.name} matched the raised figure of {amount}",
    )


async def rtm_no_match(
    event_id: int, db: AsyncSession, actor: User | None = None
) -> AuctionPlayer:
    ap = await _load_rtm_player(db, event_id)
    rtm_service._require_stage(ap, RtmStage.match_offered)

    if ap.rtm_top_team_id is None:
        raise AuctionError("There is no winning bidder to sell to")
    amount = ap.rtm_top_amount or ap.current_bid

    return await _settle_rtm(
        db, event_id=event_id, ap=ap, team_id=ap.rtm_top_team_id, amount=amount,
        actor=actor, rtm_used=False, action=AuditAction.rtm_not_matched,
        detail=f"Rights holder did not match {amount}",
    )


async def get_rtm_state(event_id: int, db: AsyncSession) -> dict | None:
    result = await db.execute(
        select(AuctionPlayer).where(
            AuctionPlayer.event_id == event_id,
            AuctionPlayer.status == PlayerAuctionStatus.rtm_pending,
        )
    )
    ap = result.scalars().first()
    if not ap:
        return None

    teams = (
        await db.execute(
            select(Team).where(Team.id.in_([
                tid for tid in (ap.rtm_team_id, ap.rtm_top_team_id) if tid
            ]))
        )
    ).scalars().all()
    names = {t.id: t.name for t in teams}

    return {
        "auction_player_id": ap.id,
        "player_id": ap.player_id,
        "player_name": ap.player.name if ap.player else None,
        "stage": ap.rtm_stage,
        "rights_team_id": ap.rtm_team_id,
        "rights_team_name": names.get(ap.rtm_team_id),
        "top_team_id": ap.rtm_top_team_id,
        "top_team_name": names.get(ap.rtm_top_team_id),
        "top_amount": ap.rtm_top_amount,
        "version": ap.version,
    }


# ── Nomination round (new in 2.1.0) ──────────────────────────────────────────


async def nominate_player(
    event_id: int,
    team_id: int,
    auction_player_id: int,
    db: AsyncSession,
    actor: User | None = None,
) -> PlayerNomination:
    event = await _get_event(db, event_id)

    team = (
        await db.execute(select(Team).where(Team.id == team_id, Team.event_id == event_id))
    ).scalar_one_or_none()
    if not team:
        raise AuctionError("That team is not in this event")

    ap = (
        await db.execute(
            select(AuctionPlayer).where(
                AuctionPlayer.id == auction_player_id,
                AuctionPlayer.event_id == event_id,
            )
        )
    ).scalar_one_or_none()
    if not ap:
        raise AuctionError("Player is not registered in this event")
    if ap.status not in (PlayerAuctionStatus.pending, PlayerAuctionStatus.unsold):
        raise AuctionError("That player is not available to nominate")

    existing = (
        await db.execute(
            select(PlayerNomination).where(
                PlayerNomination.event_id == event_id,
                PlayerNomination.auction_player_id == auction_player_id,
                PlayerNomination.consumed_at.is_(None),
            )
        )
    ).scalars().first()
    if existing:
        raise AuctionError("That player has already been nominated")

    nomination = PlayerNomination(
        event_id=event_id, team_id=team_id, auction_player_id=auction_player_id
    )
    db.add(nomination)
    await record_audit(
        db, event_id=event_id, action=AuditAction.player_nominated,
        actor_user_id=actor.id if actor else None,
        auction_player_id=auction_player_id, team_id=team_id,
        detail=f"Nominated by {team.name}",
    )
    await db.commit()
    await db.refresh(nomination)

    await manager.broadcast(
        event_id,
        {
            "type": "player_nominated",
            "event_id": event_id,
            "nomination_id": nomination.id,
            "auction_player_id": auction_player_id,
            "player_id": ap.player_id,
            "player_name": ap.player.name if ap.player else None,
            "team_id": team_id,
            "team_name": team.name,
        },
    )
    return nomination


async def withdraw_nomination(
    event_id: int, nomination_id: int, db: AsyncSession, actor: User | None = None
) -> None:
    nomination = (
        await db.execute(
            select(PlayerNomination).where(
                PlayerNomination.id == nomination_id,
                PlayerNomination.event_id == event_id,
                PlayerNomination.consumed_at.is_(None),
            )
        )
    ).scalars().first()
    if not nomination:
        raise AuctionError("That nomination is not in the queue")

    await record_audit(
        db, event_id=event_id, action=AuditAction.nomination_withdrawn,
        actor_user_id=actor.id if actor else None,
        auction_player_id=nomination.auction_player_id, team_id=nomination.team_id,
    )
    await db.delete(nomination)
    await db.commit()

    await manager.broadcast(
        event_id,
        {
            "type": "nomination_withdrawn",
            "event_id": event_id,
            "nomination_id": nomination_id,
        },
    )


async def list_nominations(event_id: int, db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(PlayerNomination)
        .where(
            PlayerNomination.event_id == event_id,
            PlayerNomination.consumed_at.is_(None),
        )
        .order_by(PlayerNomination.id)
    )
    nominations = result.scalars().all()
    return [
        {
            "id": n.id,
            "auction_player_id": n.auction_player_id,
            "player_id": n.auction_player.player_id if n.auction_player else None,
            "player_name": (
                n.auction_player.player.name
                if n.auction_player and n.auction_player.player
                else None
            ),
            "team_id": n.team_id,
            "team_name": n.team.name if n.team else None,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in nominations
    ]


async def _next_nominated(db: AsyncSession, event_id: int) -> AuctionPlayer:
    result = await db.execute(
        select(PlayerNomination)
        .where(
            PlayerNomination.event_id == event_id,
            PlayerNomination.consumed_at.is_(None),
        )
        .order_by(PlayerNomination.id)
    )
    nomination = result.scalars().first()
    if not nomination:
        raise AuctionError(
            "The nomination queue is empty. A team must nominate the next player, "
            "or switch the event back to random order."
        )

    ap = (
        await db.execute(
            select(AuctionPlayer).where(AuctionPlayer.id == nomination.auction_player_id)
        )
    ).scalar_one_or_none()
    if not ap or ap.status not in (
        PlayerAuctionStatus.pending,
        PlayerAuctionStatus.unsold,
    ):
        nomination.consumed_at = datetime.now(timezone.utc)
        await db.flush()
        return await _next_nominated(db, event_id)

    nomination.consumed_at = datetime.now(timezone.utc)
    await db.flush()
    return ap


async def _captain_player_ids(db: AsyncSession, event_id: int) -> set[int]:
    result = await db.execute(
        select(Team.captain_id).where(
            Team.event_id == event_id,
            Team.captain_id.is_not(None),
        )
    )
    return {pid for pid in result.scalars().all() if pid is not None}