"""v2.1.0 — Right to Match and the nomination round.

Revision ID: v2_1_rtm_nominations
Revises: v2_player_model
Create Date: 2026-11-20

Additive only: new columns with server defaults, plus one new table. No data is
rewritten, so this is safe to run against a live event between sessions.

  auction_events  auction_mode ('random' | 'nomination'), rtm_enabled,
                  rtm_cards_per_team
  teams           rtm_cards_used
  auction_players rtm_team_id, rtm_stage, rtm_top_team_id, rtm_top_amount
  players_nominations  new table: the accelerated-round queue

`rtm_enabled` defaults to false, so existing events behave exactly as before
until an organizer turns it on.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v2_1_rtm_nominations"
down_revision: Union[str, None] = "v2_player_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── auction_events ────────────────────────────────────────────────────────
    op.add_column(
        "auction_events",
        sa.Column(
            "auction_mode",
            sa.String(length=20),
            nullable=False,
            server_default="random",
        ),
    )
    op.add_column(
        "auction_events",
        sa.Column("rtm_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "auction_events",
        sa.Column("rtm_cards_per_team", sa.Integer(), nullable=False, server_default="0"),
    )

    # ── teams ─────────────────────────────────────────────────────────────────
    op.add_column(
        "teams",
        sa.Column("rtm_cards_used", sa.Integer(), nullable=False, server_default="0"),
    )

    # ── auction_players ───────────────────────────────────────────────────────
    op.add_column("auction_players", sa.Column("rtm_team_id", sa.Integer(), nullable=True))
    op.add_column(
        "auction_players", sa.Column("rtm_stage", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "auction_players", sa.Column("rtm_top_team_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "auction_players", sa.Column("rtm_top_amount", sa.Integer(), nullable=True)
    )
    with op.batch_alter_table("auction_players") as batch:
        batch.create_foreign_key(
            "fk_auction_player_rtm_team", "teams", ["rtm_team_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_auction_player_rtm_top_team", "teams", ["rtm_top_team_id"], ["id"]
        )

    # ── player_nominations ────────────────────────────────────────────────────
    op.create_table(
        "player_nominations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("auction_player_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["auction_events.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["auction_player_id"], ["auction_players.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_player_nominations_id", "player_nominations", ["id"])
    op.create_index("ix_player_nominations_event_id", "player_nominations", ["event_id"])
    op.create_index(
        "ix_nominations_event_consumed", "player_nominations", ["event_id", "consumed_at"]
    )
    op.create_index("ix_nominations_player", "player_nominations", ["auction_player_id"])


def downgrade() -> None:
    op.drop_index("ix_nominations_player", table_name="player_nominations")
    op.drop_index("ix_nominations_event_consumed", table_name="player_nominations")
    op.drop_index("ix_player_nominations_event_id", table_name="player_nominations")
    op.drop_index("ix_player_nominations_id", table_name="player_nominations")
    op.drop_table("player_nominations")

    with op.batch_alter_table("auction_players") as batch:
        batch.drop_constraint("fk_auction_player_rtm_top_team", type_="foreignkey")
        batch.drop_constraint("fk_auction_player_rtm_team", type_="foreignkey")
        batch.drop_column("rtm_top_amount")
        batch.drop_column("rtm_top_team_id")
        batch.drop_column("rtm_stage")
        batch.drop_column("rtm_team_id")

    op.drop_column("teams", "rtm_cards_used")

    with op.batch_alter_table("auction_events") as batch:
        batch.drop_column("rtm_cards_per_team")
        batch.drop_column("rtm_enabled")
        batch.drop_column("auction_mode")

    # Any player left mid-negotiation becomes unsold: rtm_pending is not a
    # status the v2.0 engine understands.
    op.execute(
        "UPDATE auction_players SET status = 'unsold' WHERE status = 'rtm_pending'"
    )
