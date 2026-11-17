"""Add bid_slabs table

Revision ID: add_bid_slabs
Revises: add_user_deleted_at
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_bid_slabs'
down_revision = 'add_user_deleted_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'bid_slabs',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('auction_events.id'), index=True, nullable=False),
        sa.Column('min_value', sa.Integer(), default=0, nullable=False),
        sa.Column('max_value', sa.Integer(), default=0, nullable=False),
        sa.Column('increment', sa.Integer(), default=1000, nullable=False),
        sa.Column('sort_order', sa.Integer(), default=0, nullable=False),
    )


def downgrade() -> None:
    op.drop_table('bid_slabs')
