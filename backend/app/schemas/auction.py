"""Auction request/response schemas.

Version: 2.0.0 (2026-11-20)

Changed from 1.x:
  - BidCreate takes `team_id` instead of `captain_id`, and gains
    `expected_version` plus `idempotency_key`. The version lets the server
    reject a bid computed against a price that has since moved, rather than
    silently accepting it; the key makes a retry or double-tap a no-op, which
    matters with two operator consoles on one auction.
  - Player schemas moved to schemas/player.py — players are no longer users.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.auction import AuctionMode, AuctionStatus, PlayerAuctionStatus, RtmStage


class AuctionEventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    allowed_domains: list[str] = []
    scheduled_at: Optional[datetime] = None


class AuctionEventUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    organizer_id: Optional[int] = None
    auctioneer_id: Optional[int] = None
    allowed_domains: Optional[list[str]] = None
    scheduled_at: Optional[datetime] = None
    team_budget: Optional[int] = Field(default=None, ge=1000)
    team_max_players: Optional[int] = Field(default=None, ge=1, le=50)
    team_min_players: Optional[int] = Field(default=None, ge=0, le=50)
    player_base_price: Optional[int] = Field(default=None, ge=100)
    no_timer: Optional[bool] = None
    timer_seconds: Optional[int] = Field(default=None, ge=5, le=600)
    soft_close_threshold_seconds: Optional[int] = Field(default=None, ge=0, le=120)
    soft_close_extension_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    # New in 2.1.0
    auction_mode: Optional[AuctionMode] = None
    rtm_enabled: Optional[bool] = None
    rtm_cards_per_team: Optional[int] = Field(default=None, ge=0, le=20)


class AuctionEventOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    admin_id: int
    organizer_id: Optional[int]
    auctioneer_id: Optional[int]
    status: AuctionStatus
    allowed_domains: list[str]
    scheduled_at: Optional[datetime]
    team_budget: int
    team_max_players: int
    team_min_players: int
    player_base_price: int
    no_timer: bool | None = False
    timer_seconds: Optional[int] = None
    soft_close_threshold_seconds: Optional[int] = None
    soft_close_extension_seconds: Optional[int] = None
    auction_mode: AuctionMode = AuctionMode.random
    rtm_enabled: bool = False
    rtm_cards_per_team: int = 0
    logo: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Teams ─────────────────────────────────────────────────────────────────────


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = "#3B82F6"
    budget: Optional[int] = Field(default=None, ge=0)
    max_players: Optional[int] = Field(default=None, ge=1, le=50)
    captain_id: Optional[int] = None  # a players.id


class TeamUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    color: Optional[str] = None
    logo: Optional[str] = None
    captain_id: Optional[int] = None  # a players.id
    budget: Optional[int] = Field(default=None, ge=0)
    max_players: Optional[int] = Field(default=None, ge=1, le=50)


class TeamPlayerOut(BaseModel):
    id: int
    event_id: int
    team_id: int
    player_id: int
    sold_price: int

    model_config = {"from_attributes": True}


class TeamOut(BaseModel):
    id: int
    event_id: int
    name: str
    color: str
    logo: Optional[str] = None
    captain_id: Optional[int]
    budget: int
    spent: int
    max_players: int
    players: list[TeamPlayerOut] = []

    model_config = {"from_attributes": True}


# ── Auction players ───────────────────────────────────────────────────────────


class AuctionPlayerOut(BaseModel):
    id: int
    event_id: int
    player_id: int
    base_price: int
    category_id: Optional[int] = None
    current_bid: int
    current_bid_team_id: Optional[int]
    status: PlayerAuctionStatus
    auction_order: int
    version: int
    # New in 2.1.0
    rtm_team_id: Optional[int] = None
    rtm_stage: Optional[RtmStage] = None
    rtm_top_team_id: Optional[int] = None
    rtm_top_amount: Optional[int] = None

    model_config = {"from_attributes": True}


class AuctionPlayerCreate(BaseModel):
    player_id: int
    base_price: Optional[int] = Field(default=None, ge=0)
    category_id: Optional[int] = None


class AuctionPlayerUpdate(BaseModel):
    base_price: Optional[int] = Field(default=None, ge=0)
    category_id: Optional[int] = None
    auction_order: Optional[int] = None
    # The team holding Right to Match on this player. New in 2.1.0.
    # Set during setup; None clears the rights.
    rtm_team_id: Optional[int] = None


# ── Bidding ───────────────────────────────────────────────────────────────────


class BidCreate(BaseModel):
    team_id: int
    amount: int = Field(gt=0)
    # The AuctionPlayer.version the bidder's screen was showing. When supplied
    # and stale, the server rejects with 409 rather than accepting a bid
    # computed against a price that has already moved.
    expected_version: Optional[int] = None
    # Client-generated. Replaying the same key returns the original bid instead
    # of placing a second one.
    idempotency_key: Optional[str] = Field(default=None, max_length=64)


class BidCorrection(BaseModel):
    amount: int = Field(ge=0)


class BidOut(BaseModel):
    id: int
    event_id: int
    auction_player_id: int
    team_id: int
    amount: int
    reverted_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NextPlayerRequest(BaseModel):
    auction_player_id: Optional[int] = None  # None = random from the pending pool
    idempotency_key: Optional[str] = Field(default=None, max_length=64)


class HammerRequest(BaseModel):
    idempotency_key: Optional[str] = Field(default=None, max_length=64)


# ── Bid slabs ─────────────────────────────────────────────────────────────────


class BidSlabBase(BaseModel):
    min_value: int = Field(default=0, ge=0)
    max_value: int = Field(default=0, ge=0)  # 0 = no upper limit
    increment: int = Field(default=1000, gt=0)
    sort_order: int = 0


class BidSlabCreate(BidSlabBase):
    pass


class BidSlabUpdate(BaseModel):
    min_value: Optional[int] = Field(default=None, ge=0)
    max_value: Optional[int] = Field(default=None, ge=0)
    increment: Optional[int] = Field(default=None, gt=0)
    sort_order: Optional[int] = None


class BidSlabOut(BidSlabBase):
    id: int
    event_id: int

    model_config = {"from_attributes": True}


# ── Player categories ─────────────────────────────────────────────────────────


class PlayerCategoryBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_price: int = Field(default=1000, ge=0)
    sort_order: int = 0
    auction_first: bool = False


class PlayerCategoryCreate(PlayerCategoryBase):
    pass


class PlayerCategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    base_price: Optional[int] = Field(default=None, ge=0)
    sort_order: Optional[int] = None
    auction_first: Optional[bool] = None


class PlayerCategoryOut(PlayerCategoryBase):
    id: int
    event_id: int

    model_config = {"from_attributes": True}


# ── Audit ─────────────────────────────────────────────────────────────────────


class AuditLogOut(BaseModel):
    id: int
    event_id: int
    action: str
    actor_user_id: Optional[int]
    auction_player_id: Optional[int]
    team_id: Optional[int]
    amount: Optional[int]
    previous_amount: Optional[int]
    detail: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Right to Match (new in 2.1.0) ────────────────────────────────────────────


class RtmRaiseRequest(BaseModel):
    """The top bidder's single permitted raise after RTM is exercised."""

    amount: int = Field(gt=0)


class RtmStateOut(BaseModel):
    auction_player_id: int
    player_id: int
    player_name: Optional[str] = None
    stage: Optional[RtmStage]
    rights_team_id: Optional[int]
    rights_team_name: Optional[str] = None
    top_team_id: Optional[int]
    top_team_name: Optional[str] = None
    top_amount: Optional[int]
    version: int


# ── Nomination round (new in 2.1.0) ──────────────────────────────────────────


class NominationCreate(BaseModel):
    team_id: int
    auction_player_id: int


class NominationOut(BaseModel):
    id: int
    auction_player_id: int
    player_id: Optional[int] = None
    player_name: Optional[str] = None
    team_id: int
    team_name: Optional[str] = None
    created_at: Optional[str] = None
