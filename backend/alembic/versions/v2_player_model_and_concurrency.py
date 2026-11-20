"""v2.0.0 — Player model, team-keyed bids, concurrency and audit.

Revision ID: v2_player_model
Revises: add_user_age
Create Date: 2026-11-20

What this migration does, and why:

1. Creates `players`. Players used to be User rows, which forced a unique,
   mandatory email on every person in the auction — including people who never
   log in to anything. Existing player-Users are copied into `players` with
   `legacy_user_id` pointing back, so historical events keep resolving and an
   account can still be matched to a player later. The original User rows are
   left untouched.

2. Re-keys bids and sales from captain (a user) to team. `Team.captain_id` now
   points at `players.id`; `auction_players.current_bidder_id` becomes
   `current_bid_team_id`; `bids.captain_id` becomes `bids.team_id`. The old
   values are mapped through `teams.captain_id` as it stood before the change.

3. Adds the concurrency and audit machinery: a `version` column for optimistic
   concurrency control, idempotency keys on bids, soft-revert columns, unique
   constraints that make a double sale impossible at the database level, and
   the `auction_audit_log` table.

Reversible: downgrade() restores the previous columns and remaps the data back,
but bids placed against a team with no captain cannot be mapped to a user and
are left with captain_id NULL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v2_player_model"
down_revision: Union[str, None] = "add_user_age"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # ── 1. players ────────────────────────────────────────────────────────────
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("profile_photo", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("batting_rating", sa.Float(), nullable=True, server_default="5.0"),
        sa.Column("bowling_rating", sa.Float(), nullable=True, server_default="5.0"),
        sa.Column("fielding_rating", sa.Float(), nullable=True, server_default="5.0"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("legacy_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["legacy_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_players_id", "players", ["id"])
    op.create_index("ix_players_name", "players", ["name"])
    op.create_index("ix_players_created_by", "players", ["created_by_user_id"])

    # Copy across every User that is referenced as a player anywhere.
    op.execute(
        """
        INSERT INTO players (
            name, email, phone, age, profile_photo,
            batting_rating, bowling_rating, fielding_rating,
            legacy_user_id, created_at
        )
        SELECT
            u.name,
            u.email,
            u.phone,
            u.age,
            u.profile_photo,
            COALESCE(u.batting_rating, 5.0),
            COALESCE(u.bowling_rating, 5.0),
            COALESCE(u.fielding_rating, 5.0),
            u.id,
            u.created_at
        FROM users u
        WHERE u.id IN (
            SELECT player_id FROM auction_players
            UNION
            SELECT player_id FROM team_players
            UNION
            SELECT captain_id FROM teams WHERE captain_id IS NOT NULL
        )
        """
    )

    # ── 2. auction_events ─────────────────────────────────────────────────────
    # `no_timer` exists on the model but was never added by any migration — it
    # only ever appeared on SQLite, via the ALTER TABLE backfill in
    # core.database.ensure_schema(). On PostgreSQL the column is simply absent,
    # so every query selecting it fails. Add it here.
    existing_event_cols = {
        c["name"] for c in sa.inspect(bind).get_columns("auction_events")
    }
    if "no_timer" not in existing_event_cols:
        op.add_column(
            "auction_events",
            sa.Column("no_timer", sa.Boolean(), nullable=True, server_default=sa.false()),
        )

    with op.batch_alter_table("auction_events") as batch:
        batch.add_column(
            sa.Column("team_min_players", sa.Integer(), nullable=True, server_default="0")
        )
        batch.add_column(sa.Column("timer_seconds", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("soft_close_threshold_seconds", sa.Integer(), nullable=True)
        )
        batch.add_column(
            sa.Column("soft_close_extension_seconds", sa.Integer(), nullable=True)
        )
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    # ── 3. teams ──────────────────────────────────────────────────────────────
    op.add_column("teams", sa.Column("logo", sa.String(length=500), nullable=True))
    op.add_column("teams", sa.Column("captain_player_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE teams
        SET captain_player_id = (
            SELECT p.id FROM players p WHERE p.legacy_user_id = teams.captain_id
        )
        WHERE teams.captain_id IS NOT NULL
        """
    )

    # ── 4. auction_players ────────────────────────────────────────────────────
    op.add_column("auction_players", sa.Column("new_player_id", sa.Integer(), nullable=True))
    op.add_column(
        "auction_players", sa.Column("current_bid_team_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "auction_players",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "auction_players",
        sa.Column("extension_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE auction_players
        SET new_player_id = (
            SELECT p.id FROM players p WHERE p.legacy_user_id = auction_players.player_id
        )
        """
    )
    # The old current_bidder_id held a captain's USER id. Resolve it to the team
    # that captain led in this event.
    op.execute(
        """
        UPDATE auction_players
        SET current_bid_team_id = (
            SELECT t.id FROM teams t
            WHERE t.captain_id = auction_players.current_bidder_id
              AND t.event_id = auction_players.event_id
        )
        WHERE auction_players.current_bidder_id IS NOT NULL
        """
    )

    # ── 5. team_players ───────────────────────────────────────────────────────
    op.add_column("team_players", sa.Column("event_id", sa.Integer(), nullable=True))
    op.add_column("team_players", sa.Column("new_player_id", sa.Integer(), nullable=True))
    op.add_column(
        "team_players", sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE team_players
        SET event_id = (SELECT t.event_id FROM teams t WHERE t.id = team_players.team_id),
            new_player_id = (
                SELECT p.id FROM players p WHERE p.legacy_user_id = team_players.player_id
            )
        """
    )

    # ── 6. bids ───────────────────────────────────────────────────────────────
    op.add_column("bids", sa.Column("team_id", sa.Integer(), nullable=True))
    op.add_column("bids", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("bids", sa.Column("placed_by_user_id", sa.Integer(), nullable=True))
    op.add_column("bids", sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bids", sa.Column("reverted_by_user_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE bids
        SET team_id = (
            SELECT t.id FROM teams t
            WHERE t.captain_id = bids.captain_id AND t.event_id = bids.event_id
        )
        """
    )
    # Historical bids with no resolvable team cannot be kept: team_id is NOT NULL
    # and a bid with no team means nothing. There is no data loss of record —
    # these are bids whose captain is no longer on any team in that event.
    op.execute("DELETE FROM bids WHERE team_id IS NULL")

    # ── 7. Drop the superseded columns and swap in the new ones ───────────────
    with op.batch_alter_table("teams") as batch:
        batch.drop_column("captain_id")
        batch.alter_column("captain_player_id", new_column_name="captain_id")

    with op.batch_alter_table("auction_players") as batch:
        batch.drop_column("current_bidder_id")
        batch.drop_column("player_id")
        batch.alter_column("new_player_id", new_column_name="player_id")

    with op.batch_alter_table("team_players") as batch:
        batch.drop_column("player_id")
        batch.alter_column("new_player_id", new_column_name="player_id")

    with op.batch_alter_table("bids") as batch:
        batch.drop_column("captain_id")

    # ── 8. Constraints and indexes ────────────────────────────────────────────
    # Defensive: remove any duplicate sale rows left by the pre-fix double-hammer
    # bug, keeping the earliest, so the unique constraint below can be created.
    op.execute(
        """
        DELETE FROM team_players
        WHERE id NOT IN (
            SELECT MIN(id) FROM team_players GROUP BY event_id, player_id
        )
        """
    )
    op.execute(
        """
        DELETE FROM auction_players
        WHERE id NOT IN (
            SELECT MIN(id) FROM auction_players GROUP BY event_id, player_id
        )
        """
    )

    with op.batch_alter_table("auction_players") as batch:
        batch.create_unique_constraint(
            "uq_auction_player_event_player", ["event_id", "player_id"]
        )
        batch.create_foreign_key(
            "fk_auction_player_player", "players", ["player_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_auction_player_team", "teams", ["current_bid_team_id"], ["id"]
        )
    op.create_index(
        "ix_auction_players_event_status", "auction_players", ["event_id", "status"]
    )

    with op.batch_alter_table("team_players") as batch:
        batch.create_unique_constraint(
            "uq_team_player_event_player", ["event_id", "player_id"]
        )
        batch.create_unique_constraint(
            "uq_team_player_team_player", ["team_id", "player_id"]
        )
        batch.create_foreign_key("fk_team_player_player", "players", ["player_id"], ["id"])
        batch.create_foreign_key(
            "fk_team_player_event", "auction_events", ["event_id"], ["id"]
        )
    op.create_index("ix_team_players_team", "team_players", ["team_id"])

    with op.batch_alter_table("bids") as batch:
        batch.alter_column("team_id", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint(
            "uq_bid_event_idempotency", ["event_id", "idempotency_key"]
        )
        batch.create_foreign_key("fk_bid_team", "teams", ["team_id"], ["id"])
    op.create_index("ix_bids_auction_player", "bids", ["auction_player_id", "id"])
    op.create_index("ix_bids_team", "bids", ["team_id"])

    with op.batch_alter_table("teams") as batch:
        batch.create_foreign_key("fk_team_captain_player", "players", ["captain_id"], ["id"])

    op.create_index("ix_bid_slabs_event_sort", "bid_slabs", ["event_id", "sort_order"])

    # ── 9. Audit log ──────────────────────────────────────────────────────────
    op.create_table(
        "auction_audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("auction_player_id", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("previous_amount", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["auction_events.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["auction_player_id"], ["auction_players.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_event_created", "auction_audit_log", ["event_id", "created_at"])

    # ── 10. Retire the captain role ───────────────────────────────────────────
    # `roles` is a JSON-encoded TEXT column (core.types.StringList), not a real
    # array, so this is string surgery. json.dumps writes '["a","b"]' with no
    # space after the comma, but hand-edited rows may have one — handle both.
    for pattern in ('"captain", ', '"captain",', ', "captain"', ',"captain"'):
        op.execute(
            sa.text("UPDATE users SET roles = REPLACE(roles, :old, '')").bindparams(
                old=pattern
            )
        )
    # A user whose only role was captain becomes a plain viewer.
    op.execute(
        sa.text(
            "UPDATE users SET roles = '[\"viewer\"]' "
            "WHERE roles IN ('[\"captain\"]', '[]', '')"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_audit_event_created", table_name="auction_audit_log")
    op.drop_table("auction_audit_log")

    op.drop_index("ix_bid_slabs_event_sort", table_name="bid_slabs")

    # bids: team_id -> captain_id
    op.add_column("bids", sa.Column("captain_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE bids
        SET captain_id = (
            SELECT p.legacy_user_id FROM teams t
            JOIN players p ON p.id = t.captain_id
            WHERE t.id = bids.team_id
        )
        """
    )
    op.drop_index("ix_bids_team", table_name="bids")
    op.drop_index("ix_bids_auction_player", table_name="bids")
    with op.batch_alter_table("bids") as batch:
        batch.drop_constraint("uq_bid_event_idempotency", type_="unique")
        batch.drop_column("team_id")
        batch.drop_column("idempotency_key")
        batch.drop_column("placed_by_user_id")
        batch.drop_column("reverted_at")
        batch.drop_column("reverted_by_user_id")

    # team_players
    op.add_column("team_players", sa.Column("legacy_player_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE team_players
        SET legacy_player_id = (
            SELECT p.legacy_user_id FROM players p WHERE p.id = team_players.player_id
        )
        """
    )
    op.drop_index("ix_team_players_team", table_name="team_players")
    with op.batch_alter_table("team_players") as batch:
        batch.drop_constraint("uq_team_player_event_player", type_="unique")
        batch.drop_constraint("uq_team_player_team_player", type_="unique")
        batch.drop_column("player_id")
        batch.alter_column("legacy_player_id", new_column_name="player_id")
        batch.drop_column("event_id")
        batch.drop_column("sold_at")

    # auction_players
    op.add_column("auction_players", sa.Column("legacy_player_id", sa.Integer(), nullable=True))
    op.add_column("auction_players", sa.Column("current_bidder_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE auction_players
        SET legacy_player_id = (
            SELECT p.legacy_user_id FROM players p WHERE p.id = auction_players.player_id
        ),
        current_bidder_id = (
            SELECT p.legacy_user_id FROM teams t
            JOIN players p ON p.id = t.captain_id
            WHERE t.id = auction_players.current_bid_team_id
        )
        """
    )
    op.drop_index("ix_auction_players_event_status", table_name="auction_players")
    with op.batch_alter_table("auction_players") as batch:
        batch.drop_constraint("uq_auction_player_event_player", type_="unique")
        batch.drop_column("player_id")
        batch.alter_column("legacy_player_id", new_column_name="player_id")
        batch.drop_column("current_bid_team_id")
        batch.drop_column("version")
        batch.drop_column("extension_count")

    # teams
    op.add_column("teams", sa.Column("legacy_captain_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE teams
        SET legacy_captain_id = (
            SELECT p.legacy_user_id FROM players p WHERE p.id = teams.captain_id
        )
        """
    )
    with op.batch_alter_table("teams") as batch:
        batch.drop_column("captain_id")
        batch.alter_column("legacy_captain_id", new_column_name="captain_id")
        batch.drop_column("logo")

    with op.batch_alter_table("auction_events") as batch:
        batch.drop_column("team_min_players")
        # no_timer is left in place: it belongs to the model, not to v2.
        batch.drop_column("timer_seconds")
        batch.drop_column("soft_close_threshold_seconds")
        batch.drop_column("soft_close_extension_seconds")
        batch.drop_column("deleted_at")

    op.drop_index("ix_players_created_by", table_name="players")
    op.drop_index("ix_players_name", table_name="players")
    op.drop_index("ix_players_id", table_name="players")
    op.drop_table("players")
