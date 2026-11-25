"""Database-backed one-time invitation and login tokens."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (
        Index("ix_auth_tokens_email_purpose", "email", "purpose"),
        Index("ix_auth_tokens_expires_at", "expires_at"),
    )

    token: Mapped[str] = mapped_column(String(255), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
