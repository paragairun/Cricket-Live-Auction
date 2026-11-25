"""Durable idempotency records for mutating auction commands."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuctionCommand(Base):
    __tablename__ = "auction_commands"
    __table_args__ = (
        UniqueConstraint("event_id", "idempotency_key", name="uq_auction_command_event_key"),
        Index("ix_auction_commands_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("auction_events.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    result_auction_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("auction_players.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
