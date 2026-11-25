"""Add organiser controls for captain exclusion and one unsold re-auction pass.

Revision ID: add_captain_unsold_round
Revises: v2_1_rtm_nominations
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_captain_unsold_round"
down_revision: Union[str, None] = "v2_1_rtm_nominations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "auction_events",
        sa.Column("exclude_captains_from_auction", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "auction_events",
        sa.Column("auto_reauction_unsold", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "auction_events",
        sa.Column("unsold_reauction_started", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("auction_events", "unsold_reauction_started")
    op.drop_column("auction_events", "auto_reauction_unsold")
    op.drop_column("auction_events", "exclude_captains_from_auction")
