"""Persist local auction deadlines, hybrid progression and auth tokens.

Revision ID: v2_2_single_source
Revises: v2_1_rtm_nominations
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v2_2_single_source"
down_revision: Union[str, None] = "v2_1_rtm_nominations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("auction_events", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("auction_events", sa.Column("advance_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("auction_events", sa.Column("paused_remaining_seconds", sa.Integer(), nullable=True))
    op.add_column("auction_events", sa.Column("auto_advance", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "auth_tokens",
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("event_name", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=True),
        sa.Column("code", sa.String(length=10), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_auth_tokens_email_purpose", "auth_tokens", ["email", "purpose"])
    op.create_index("ix_auth_tokens_expires_at", "auth_tokens", ["expires_at"])
    op.create_table(
        "auction_commands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("result_auction_player_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["auction_events.id"]),
        sa.ForeignKeyConstraint(["result_auction_player_id"], ["auction_players.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "idempotency_key", name="uq_auction_command_event_key"),
    )
    op.create_index("ix_auction_commands_created_at", "auction_commands", ["created_at"])
    op.create_index(
        "uq_auction_player_one_active_per_event",
        "auction_players",
        ["event_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_auction_player_one_active_per_event", table_name="auction_players")
    op.drop_index("ix_auction_commands_created_at", table_name="auction_commands")
    op.drop_table("auction_commands")
    op.drop_index("ix_auth_tokens_expires_at", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_email_purpose", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_column("auction_events", "auto_advance")
    op.drop_column("auction_events", "paused_remaining_seconds")
    op.drop_column("auction_events", "advance_at")
    op.drop_column("auction_events", "deadline_at")
