"""Add player_categories table and category_id to auction_players

Revision ID: add_player_categories
Revises: add_bid_slabs
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_player_categories'
down_revision = 'add_bid_slabs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'player_categories',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('auction_events.id'), index=True, nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('base_price', sa.Integer(), default=1000, nullable=False),
        sa.Column('sort_order', sa.Integer(), default=0, nullable=False),
        sa.Column('auction_first', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column('auction_players', sa.Column('category_id', sa.Integer(), sa.ForeignKey('player_categories.id'), nullable=True))


def downgrade() -> None:
    op.drop_column('auction_players', 'category_id')
    op.drop_table('player_categories')
