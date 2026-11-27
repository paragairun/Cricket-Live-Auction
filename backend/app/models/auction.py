"""Auction domain models.

Version: 2.0.0 (2026-11-20)

Changed from 1.x — three structural changes, all driven by the move to a live,
in-room auction with no captain dashboard:

1. Players are no longer User rows. A `Player` is a roster record the organizer
   creates: a name, optionally a phone/email, ratings. No login, no password, no
   mandatory unique email. Previously every player needed a User account with a
   unique email, which forced invite emails on an event that happens in a room.

2. Bids and sales key on `team_id`, not `captain_id`. The captain is still
   recorded on the team (Team.captain_id now points at a Player), but a captain
   is a person on the roster, not a system actor who bids. Attributing a bid to
   a captain was always a slight lie; with the captain dashboard gone it would
   be a plain one.

3. Concurrency and audit. AuctionPlayer carries a `version` column for optimistic
   concurrency control; Bid carries an idempotency key and is soft-reverted
   rather than deleted; every auctioneer override writes an AuctionAuditLog row.
   Unique constraints now make a double-sale impossible at the database level
   even if the application were to produce one.
"""
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    text,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.types import EnumString, StringList


class AuctionStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    active = "active"
    paused = "paused"
    completed = "completed"


class PlayerAuctionStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    queued = "queued"  # Sale reversal requested while another player is on the block.
    # Bidding has closed but a Right-to-Match decision is outstanding, so the
    # player is neither sold nor back in the pool. New in 2.1.0.
    rtm_pending = "rtm_pending"
    sold = "sold"
    unsold = "unsold"


class RtmStage(str, enum.Enum):
    """Where an RTM negotiation has got to.

    The sequence follows the current IPL rules: the rights holder is asked
    whether to exercise, the highest bidder then gets one chance to raise, and
    the holder must match that raised figure to acquire the player.
    """

    # Waiting on the rights holder: exercise or decline.
    offered = "offered"
    # Holder exercised; the top bidder may raise once, or stand pat.
    raise_offered = "raise_offered"
    # Top bidder raised; the holder must match the new figure or lose the player.
    match_offered = "match_offered"


class AuctionMode(str, enum.Enum):
    """How the next player is chosen."""

    # Drawn at random from the pending pool, honouring auction_first categories.
    random = "random"
    # Teams nominate who comes up next — the "accelerated round" in IPL terms.
    nomination = "nomination"


class AuditAction(str, enum.Enum):
    """Every operator action that changes money or ownership."""

    auction_started = "auction_started"
    auction_paused = "auction_paused"
    auction_resumed = "auction_resumed"
    auction_finished = "auction_finished"
    player_put_up = "player_put_up"
    player_pulled = "player_pulled"
    bid_placed = "bid_placed"
    bid_reverted = "bid_reverted"
    bid_corrected = "bid_corrected"
    player_sold = "player_sold"
    player_unsold = "player_unsold"
    sale_reversed = "sale_reversed"
    auction_reset = "auction_reset"
    # Right to Match (new in 2.1.0)
    rtm_offered = "rtm_offered"
    rtm_exercised = "rtm_exercised"
    rtm_declined = "rtm_declined"
    rtm_raised = "rtm_raised"
    rtm_raise_skipped = "rtm_raise_skipped"
    rtm_matched = "rtm_matched"
    rtm_not_matched = "rtm_not_matched"
    # Nomination round (new in 2.1.0)
    player_nominated = "player_nominated"
    nomination_withdrawn = "nomination_withdrawn"


class Player(Base):
    """A person on the roster. Not a user; never logs in.

    Global rather than event-scoped so a recurring league can reuse its player
    pool across seasons. AuctionPlayer is the per-event registration.
    """

    __tablename__ = "players"
    __table_args__ = (
        Index("ix_players_name", "name"),
        Index("ix_players_created_by", "created_by_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # All optional. Email is deliberately NOT unique and NOT required: a live
    # auction does not need to reach these people by email.
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)

    profile_photo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    batting_rating: Mapped[float] = mapped_column(Float, default=5.0)
    bowling_rating: Mapped[float] = mapped_column(Float, default=5.0)
    fielding_rating: Mapped[float] = mapped_column(Float, default=5.0)

    # The organizer/admin who created this record, for scoping their player pool.
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # Set when a player was migrated from the old User-based model, so historical
    # events keep working and a player can still be matched to an account later.
    legacy_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def overall_rating(self) -> float:
        return round(
            (self.batting_rating + self.bowling_rating + self.fielding_rating) / 3, 2
        )

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(timezone.utc)


class AuctionEvent(Base):
    __tablename__ = "auction_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    organizer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    auctioneer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    status: Mapped[AuctionStatus] = mapped_column(
        EnumString(AuctionStatus, length=20), default=AuctionStatus.draft
    )
    allowed_domains: Mapped[list[str]] = mapped_column(StringList, default=list)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    team_budget: Mapped[int] = mapped_column(Integer, default=100000)
    team_max_players: Mapped[int] = mapped_column(Integer, default=15)
    # Minimum squad size. Budget is reserved so a team that spends heavily early
    # can still fill its XI. 0 disables the reservation.
    team_min_players: Mapped[int] = mapped_column(Integer, default=0)

    player_base_price: Mapped[int] = mapped_column(Integer, default=1000)

    # Legacy compatibility field. Server-side timing is now always enabled.
    no_timer: Mapped[bool | None] = mapped_column(default=False, nullable=True)
    # The database is authoritative for every auction clock and progression.
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    advance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_remaining_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_advance: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Per-event timing overrides; None falls back to the application settings.
    timer_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    soft_close_threshold_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    soft_close_extension_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # How the next player is picked. New in 2.1.0.
    auction_mode: Mapped[AuctionMode] = mapped_column(
        EnumString(AuctionMode, length=20), default=AuctionMode.random
    )

    # Right to Match. New in 2.1.0. Off by default: a club league that has never
    # had a previous season has no rights to match.
    rtm_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Cards per team. 0 means unlimited while rtm_enabled is true.
    rtm_cards_per_team: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Keep assigned captains out of the auction pool by default. Automatic
    # unsold re-auction may repeat until every eligible player is sold.
    exclude_captains_from_auction: Mapped[bool] = mapped_column(default=True, nullable=False)
    auto_reauction_unsold: Mapped[bool] = mapped_column(default=False, nullable=False)
    unsold_reauction_started: Mapped[bool] = mapped_column(default=False, nullable=False)

    total_viewers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    background_image: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    teams: Mapped[list["Team"]] = relationship(
        "Team", back_populates="event", lazy="selectin"
    )
    auction_players: Mapped[list["AuctionPlayer"]] = relationship(
        "AuctionPlayer", back_populates="event", lazy="selectin"
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("event_id", "name", name="uq_team_event_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(7), default="#3B82F6")
    logo: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The team's captain is a PLAYER on the roster, not a system user. Kept for
    # display and squad identity; captains no longer log in or place bids.
    captain_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)

    budget: Mapped[int] = mapped_column(Integer, default=1000)
    spent: Mapped[int] = mapped_column(Integer, default=0)
    max_players: Mapped[int] = mapped_column(Integer, default=11)

    # Right-to-Match cards this team has spent. New in 2.1.0.
    rtm_cards_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    event: Mapped["AuctionEvent"] = relationship("AuctionEvent", back_populates="teams")
    players: Mapped[list["TeamPlayer"]] = relationship(
        "TeamPlayer", back_populates="team", lazy="selectin"
    )
    captain: Mapped["Player | None"] = relationship("Player", lazy="selectin")

    @property
    def remaining(self) -> int:
        return self.budget - self.spent


class TeamPlayer(Base):
    """A completed sale: this player belongs to this team at this price."""

    __tablename__ = "team_players"
    __table_args__ = (
        # A player cannot be sold twice in the same event. This is the database
        # backstop against a double-hammer; previously nothing prevented it.
        UniqueConstraint("event_id", "player_id", name="uq_team_player_event_player"),
        UniqueConstraint("team_id", "player_id", name="uq_team_player_team_player"),
        Index("ix_team_players_team", "team_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    sold_price: Mapped[int] = mapped_column(Integer, default=0)
    sold_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    team: Mapped["Team"] = relationship("Team", back_populates="players")
    player: Mapped["Player"] = relationship("Player", lazy="selectin")


class AuctionPlayer(Base):
    """A player's registration in one event, and their live auction state."""

    __tablename__ = "auction_players"
    __table_args__ = (
        UniqueConstraint("event_id", "player_id", name="uq_auction_player_event_player"),
        Index("ix_auction_players_event_status", "event_id", "status"),
        Index(
            "uq_auction_player_one_active_per_event",
            "event_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)

    base_price: Mapped[int] = mapped_column(Integer, default=1000)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("player_categories.id"), nullable=True
    )

    current_bid: Mapped[int] = mapped_column(Integer, default=0)
    # The TEAM holding the current highest bid. Was current_bidder_id -> users.id.
    current_bid_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id"), nullable=True
    )

    status: Mapped[PlayerAuctionStatus] = mapped_column(
        EnumString(PlayerAuctionStatus, length=20), default=PlayerAuctionStatus.pending
    )
    auction_order: Mapped[int] = mapped_column(Integer, default=0)

    # Optimistic concurrency control. Every accepted bid increments this; a bid
    # that carries a stale version is rejected rather than silently overwriting.
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # How many soft-close extensions this player has already been granted, used
    # to shrink each successive extension so a bidding war terminates.
    extension_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Right to Match (new in 2.1.0) ──────────────────────────────────────
    # The team holding RTM rights on this player — typically the side they
    # played for last season. Set during event setup, not during the auction.
    rtm_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id"), nullable=True
    )
    # Where the negotiation has got to; None when no RTM is in progress.
    rtm_stage: Mapped[RtmStage | None] = mapped_column(
        EnumString(RtmStage, length=20), nullable=True, default=None
    )
    # Snapshot of the bid that closed, carried through the negotiation. The
    # live current_bid is left alone so a reverted RTM can restore it.
    rtm_top_team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id"), nullable=True
    )
    rtm_top_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)

    event: Mapped["AuctionEvent"] = relationship("AuctionEvent", back_populates="auction_players")
    player: Mapped["Player"] = relationship("Player", lazy="selectin")
    category: Mapped["PlayerCategory | None"] = relationship("PlayerCategory", lazy="selectin")


class Bid(Base):
    __tablename__ = "bids"
    __table_args__ = (
        # Makes a retried or duplicated submission a no-op instead of a second
        # bid. Essential with two operator consoles driving one auction.
        UniqueConstraint("event_id", "idempotency_key", name="uq_bid_event_idempotency"),
        Index("ix_bids_auction_player", "auction_player_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id"), index=True)
    auction_player_id: Mapped[int] = mapped_column(ForeignKey("auction_players.id"))
    # Was captain_id -> users.id.
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)

    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Which operator entered it (auctioneer or assistant).
    placed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Undo soft-deletes rather than hard-deletes, so a disputed sale can be
    # reconstructed. Previously undo_last_bid() called db.delete() on the row.
    reverted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    reverted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    @property
    def is_reverted(self) -> bool:
        return self.reverted_at is not None


class BidSlab(Base):
    __tablename__ = "bid_slabs"
    __table_args__ = (Index("ix_bid_slabs_event_sort", "event_id", "sort_order"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id"), index=True)
    min_value: Mapped[int] = mapped_column(Integer, default=0)  # inclusive
    max_value: Mapped[int] = mapped_column(Integer, default=0)  # exclusive, 0 = no limit
    increment: Mapped[int] = mapped_column(Integer, default=1000)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    event: Mapped["AuctionEvent"] = relationship("AuctionEvent")


class PlayerCategory(Base):
    __tablename__ = "player_categories"
    __table_args__ = (
        UniqueConstraint("event_id", "name", name="uq_category_event_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    base_price: Mapped[int] = mapped_column(Integer, default=1000)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    auction_first: Mapped[bool] = mapped_column(default=False)

    event: Mapped["AuctionEvent"] = relationship("AuctionEvent")


class PlayerNomination(Base):
    """A team's nomination of who comes up next.

    New in 2.1.0. The IPL accelerated round works this way: rather than drawing
    from the pool at random, franchises nominate players from the remaining
    list. 1.x only had `re-auction-all-unsold`, which dumped everyone back into
    the random pool at once.
    """

    __tablename__ = "player_nominations"
    __table_args__ = (
        # One live nomination per player; a consumed one no longer blocks.
        Index("ix_nominations_event_consumed", "event_id", "consumed_at"),
        Index("ix_nominations_player", "auction_player_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    auction_player_id: Mapped[int] = mapped_column(ForeignKey("auction_players.id"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Set when the player was actually put on the block.
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    team: Mapped["Team"] = relationship("Team", lazy="selectin")
    auction_player: Mapped["AuctionPlayer"] = relationship("AuctionPlayer", lazy="selectin")


class AuctionAuditLog(Base):
    """Append-only record of every operator action that moves money or ownership.

    New in 2.0.0. Previously an auctioneer could undo a bid (hard-deleting the
    row) or rewrite the current bid to any number, leaving no trace — so a
    disputed sale could not be reconstructed afterwards.
    """

    __tablename__ = "auction_audit_log"
    __table_args__ = (Index("ix_audit_event_created", "event_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id"), index=True)
    action: Mapped[AuditAction] = mapped_column(EnumString(AuditAction, length=40))
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    auction_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("auction_players.id"), nullable=True
    )
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
