"""Add structured tournament profile details to auction events.

Revision ID: event_profile_fields
Revises: merge_event_profile_heads
"""
import sqlalchemy as sa
from alembic import op

revision = "event_profile_fields"
down_revision = "merge_event_profile_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auction_events",
        sa.Column("event_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("auction_events", "event_details")
