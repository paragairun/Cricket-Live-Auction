"""add_enum_types

Revision ID: add_enum_types
Revises: 01d8b47714c9
Create Date: 2026-03-13

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'add_enum_types'
down_revision = '01d8b47714c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op on SQLite.
    #
    # On PostgreSQL this migration created native ENUM types (auctionstatus,
    # playerauctionstatus) and converted the status columns to them. SQLite has
    # no native ENUM type, so the status columns stay as plain VARCHAR columns
    # (as created in the initial schema) and the valid values are enforced by
    # the Python-side SQLAlchemy Enum types (create_type=False).
    pass


def downgrade() -> None:
    # Nothing to revert on SQLite.
    pass
