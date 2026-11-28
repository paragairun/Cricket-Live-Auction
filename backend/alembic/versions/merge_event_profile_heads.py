"""Merge the three active migration branches before event profile fields.

Revision ID: merge_event_profile_heads
Revises: 266ef44602ef, add_captain_unsold_round, v2_2_single_source
"""
from typing import Sequence, Union

revision: str = "merge_event_profile_heads"
down_revision: Union[str, Sequence[str], None] = (
    "266ef44602ef",
    "add_captain_unsold_round",
    "v2_2_single_source",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
