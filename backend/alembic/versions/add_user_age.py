"""Add age column to users table

Revision ID: add_user_age
Revises: add_user_deleted_at
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_user_age'
down_revision = 'add_player_categories'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('age', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'age')
