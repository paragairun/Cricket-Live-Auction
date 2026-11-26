"""API-level tests: auth, authorization, CRUD.

Version: 2.0.0 (2026-11-20)

The authorization tests here are the regression guard for the second P0: 1.x
used `require_role(ROLE_AUCTIONEER)`, which checked only the global roles array,
so any auctioneer on the platform could hammer, pause, finish or rewrite bids on
any event. Every `test_outsider_*` test below fails against 1.x.
"""
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.auction import (
    AuctionEvent,
    AuctionPlayer,
    AuctionStatus,
    Bid,
    BidSlab,
    Player,
    PlayerAuctionStatus,
    PlayerCategory,
    Team,
    TeamPlayer,
)
from app.models.user import ROLE_ADMIN, ROLE_AUCTIONEER, ROLE_ORGANIZER


# ── Auth ──────────────────────────────────────────────────────────────────────


async def test_register_and_login(client):
    resp = await client.post(
        "/api/auth/register",
        json={"name": "Alice", "email": "alice@example.com", "password": "secret12345"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["email"] == "alice@example.com"
    assert resp.json()["access_token"]

    dupe = await client.post(
        "/api/auth/register",
        json={"name": "Alice2", "email": "alice@example.com", "password": "secret12345"},
    )
    assert dupe.status_code == 400

    ok = await client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "secret12345"},
    )
    assert ok.status_code == 200

    bad = await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "wrong"}
    )
    assert bad.status_code == 401


async def test_short_password_is_rejected(client):
    """1.x accepted a one-character password."""
    resp = await client.post(
        "/api/auth/register",
        json={"name": "Bob", "email": "bob@example.com", "password": "short"},
    )
    assert resp.status_code == 422


async def test_me_endpoint(client, make_user, auth_headers):
    user = await make_user(email="me@example.com", name="Me")
    resp = await client.get("/api/auth/me", headers=auth_headers(user))
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


async def test_invalid_token_is_rejected(client):
    resp = await client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


# ── Per-event authorization (the P0 regression guards) ────────────────────────


@pytest.fixture
async def live_event(make_event, make_team, register_player):
    event, admin, organizer, auctioneer = await make_event(status=AuctionStatus.active)
    await make_team(event, name="Team A")
    await make_team(event, name="Team B")
    await register_player(event, name="Someone")
    return event, admin, organizer, auctioneer


async def test_outsider_auctioneer_cannot_control_another_event(
    client, live_event, make_user, auth_headers
):
    """An auctioneer for their own league must not touch someone else's."""
    event, *_ = live_event
    outsider = await make_user(
        email="outsider@example.com", name="Other Auctioneer", roles=[ROLE_AUCTIONEER]
    )
    headers = auth_headers(outsider)

    for method, path in [
        ("post", f"/api/auction/events/{event.id}/pause"),
        ("post", f"/api/auction/events/{event.id}/finish"),
        ("post", f"/api/auction/events/{event.id}/hammer"),
        ("post", f"/api/auction/events/{event.id}/bid-down"),
        ("post", f"/api/auction/events/{event.id}/next-player"),
        ("post", f"/api/auction/events/{event.id}/reset"),
    ]:
        resp = await getattr(client, method)(path, json={}, headers=headers)
        assert resp.status_code == 403, f"{path} returned {resp.status_code}, expected 403"


async def test_outsider_organizer_cannot_reset_another_event(
    client, live_event, make_user, auth_headers
):
    """1.x accepted any user holding the global organizer role here."""
    event, *_ = live_event
    outsider = await make_user(
        email="other-org@example.com", name="Other Organizer", roles=[ROLE_ORGANIZER]
    )
    resp = await client.post(
        f"/api/auction/events/{event.id}/reset", json={}, headers=auth_headers(outsider)
    )
    assert resp.status_code == 403


async def test_outsider_cannot_bid_on_another_event(
    client, live_event, make_user, auth_headers, db
):
    event, *_ = live_event
    outsider = await make_user(
        email="stranger@example.com", name="Stranger", roles=[ROLE_AUCTIONEER]
    )
    resp = await client.post(
        f"/api/auction/events/{event.id}/bid",
        json={"team_id": 1, "amount": 5000},
        headers=auth_headers(outsider),
    )
    assert resp.status_code == 403


async def test_assigned_auctioneer_can_control_their_event(
    client, live_event, auth_headers
):
    event, _admin, _org, auctioneer = live_event
    resp = await client.post(
        f"/api/auction/events/{event.id}/pause", json={}, headers=auth_headers(auctioneer)
    )
    assert resp.status_code == 200, resp.text


async def test_viewer_stats_requires_authentication(client, live_event):
    """1.x had no auth dependency on this endpoint at all."""
    event, *_ = live_event
    resp = await client.get(f"/api/auction/events/{event.id}/viewer-stats")
    assert resp.status_code == 401


# ── Tournament CRUD ───────────────────────────────────────────────────────────


async def test_admin_can_edit_and_delete_a_tournament(client, make_event, auth_headers):
    event, admin, *_ = await make_event(name="Season One")
    headers = auth_headers(admin)

    edit = await client.patch(
        f"/api/admin/events/{event.id}",
        json={"name": "Season One (revised)", "team_budget": 250000},
        headers=headers,
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["name"] == "Season One (revised)"
    # 1.x silently dropped team_budget from the patch.
    assert edit.json()["team_budget"] == 250000

    archived = await client.delete(f"/api/admin/events/{event.id}", headers=headers)
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    listed = await client.get("/api/admin/events", headers=headers)
    assert all(e["id"] != event.id for e in listed.json())

    restored = await client.post(f"/api/admin/events/{event.id}/restore", headers=headers)
    assert restored.status_code == 200

    hard = await client.delete(f"/api/admin/events/{event.id}?hard=true", headers=headers)
    assert hard.status_code == 200
    assert hard.json()["status"] == "deleted"

    async with AsyncSessionLocal() as session:
        gone = await session.get(AuctionEvent, event.id)
    assert gone is None


async def test_another_admin_cannot_delete_your_tournament(
    client, make_event, make_user, auth_headers
):
    event, *_ = await make_event()
    other_admin = await make_user(
        email="other-admin@example.com", name="Other Admin", roles=[ROLE_ADMIN]
    )
    resp = await client.delete(
        f"/api/admin/events/{event.id}", headers=auth_headers(other_admin)
    )
    assert resp.status_code == 403


async def test_cannot_delete_a_running_auction(client, live_event, auth_headers):
    event, admin, *_ = live_event
    resp = await client.delete(f"/api/admin/events/{event.id}", headers=auth_headers(admin))
    assert resp.status_code == 409


# ── Players need no email ─────────────────────────────────────────────────────


async def test_player_can_be_created_with_only_a_name(client, make_event, auth_headers):
    """The whole point of the new Player model.

    1.x required a unique email per player because players were User rows.
    """
    event, _admin, organizer, _auctioneer = await make_event()
    resp = await client.post(
        f"/api/organizer/events/{event.id}/players",
        json={"name": "Ramesh"},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Ramesh"
    assert body["email"] is None
    assert body["auction_player_id"] is not None


async def test_two_players_can_share_a_blank_email(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    headers = auth_headers(organizer)
    for name in ("Player One", "Player Two"):
        resp = await client.post(
            f"/api/organizer/events/{event.id}/players",
            json={"name": name},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    async with AsyncSessionLocal() as session:
        players = (await session.execute(select(Player))).scalars().all()
    assert len(players) == 2


async def test_organizer_can_edit_and_remove_a_player(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    headers = auth_headers(organizer)

    created = await client.post(
        f"/api/organizer/events/{event.id}/players",
        json={"name": "Typo Name", "batting_rating": 5.0},
        headers=headers,
    )
    ap_id = created.json()["auction_player_id"]

    edited = await client.patch(
        f"/api/organizer/events/{event.id}/players/{ap_id}",
        json={"name": "Correct Name", "batting_rating": 8.5},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Correct Name"
    assert edited.json()["batting_rating"] == 8.5

    removed = await client.delete(
        f"/api/organizer/events/{event.id}/players/{ap_id}", headers=headers
    )
    assert removed.status_code == 204


async def test_outsider_organizer_cannot_add_players(
    client, make_event, make_user, auth_headers
):
    event, *_ = await make_event()
    outsider = await make_user(
        email="nosy@example.com", name="Nosy", roles=[ROLE_ORGANIZER]
    )
    resp = await client.post(
        f"/api/organizer/events/{event.id}/players",
        json={"name": "Sneaky"},
        headers=auth_headers(outsider),
    )
    assert resp.status_code == 403


# ── Teams and captains ────────────────────────────────────────────────────────


async def test_captain_must_be_a_registered_player(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    headers = auth_headers(organizer)

    bad = await client.post(
        f"/api/organizer/events/{event.id}/teams",
        json={"name": "Team X", "captain_id": 9999},
        headers=headers,
    )
    assert bad.status_code == 400

    player = await client.post(
        f"/api/organizer/events/{event.id}/players",
        json={"name": "Skipper"},
        headers=headers,
    )
    good = await client.post(
        f"/api/organizer/events/{event.id}/teams",
        json={"name": "Team X", "captain_id": player.json()["id"]},
        headers=headers,
    )
    assert good.status_code == 201, good.text
    assert good.json()["captain_id"] == player.json()["id"]


async def test_one_player_cannot_captain_two_teams(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    headers = auth_headers(organizer)
    player = await client.post(
        f"/api/organizer/events/{event.id}/players",
        json={"name": "Busy Skipper"},
        headers=headers,
    )
    pid = player.json()["id"]

    first = await client.post(
        f"/api/organizer/events/{event.id}/teams",
        json={"name": "Team One", "captain_id": pid},
        headers=headers,
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/organizer/events/{event.id}/teams",
        json={"name": "Team Two", "captain_id": pid},
        headers=headers,
    )
    assert second.status_code == 409


async def test_duplicate_team_names_are_refused(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    headers = auth_headers(organizer)
    body = {"name": "Royals"}
    assert (
        await client.post(
            f"/api/organizer/events/{event.id}/teams", json=body, headers=headers
        )
    ).status_code == 201
    assert (
        await client.post(
            f"/api/organizer/events/{event.id}/teams", json=body, headers=headers
        )
    ).status_code == 409


# ── Bid slabs and categories ──────────────────────────────────────────────────


async def test_bid_slab_crud(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    headers = auth_headers(organizer)

    created = await client.post(
        f"/api/organizer/events/{event.id}/bid-slabs",
        json={"min_value": 0, "max_value": 50000, "increment": 500, "sort_order": 0},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    slab_id = created.json()["id"]

    updated = await client.patch(
        f"/api/organizer/events/{event.id}/bid-slabs/{slab_id}",
        json={"increment": 1000},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["increment"] == 1000

    deleted = await client.delete(
        f"/api/organizer/events/{event.id}/bid-slabs/{slab_id}", headers=headers
    )
    assert deleted.status_code == 204


async def test_invalid_slab_range_is_refused(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    resp = await client.post(
        f"/api/organizer/events/{event.id}/bid-slabs",
        json={"min_value": 50000, "max_value": 1000, "increment": 500},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 400


async def test_category_crud_and_duplicate_name(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    headers = auth_headers(organizer)

    created = await client.post(
        f"/api/organizer/events/{event.id}/categories",
        json={"name": "Marquee", "base_price": 20000, "auction_first": True},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    cat_id = created.json()["id"]

    dupe = await client.post(
        f"/api/organizer/events/{event.id}/categories",
        json={"name": "Marquee", "base_price": 5000},
        headers=headers,
    )
    assert dupe.status_code == 409

    updated = await client.patch(
        f"/api/organizer/events/{event.id}/categories/{cat_id}",
        json={"base_price": 25000},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["base_price"] == 25000

    deleted = await client.delete(
        f"/api/organizer/events/{event.id}/categories/{cat_id}", headers=headers
    )
    assert deleted.status_code == 204


# ── CSV import ────────────────────────────────────────────────────────────────


async def test_csv_import_needs_only_a_name_column(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    csv_body = "name\nRamesh\nSuresh\nMahesh\n"
    resp = await client.post(
        f"/api/organizer/events/{event.id}/import-players",
        files={"file": ("players.csv", csv_body, "text/csv")},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["imported"] == 3
    assert resp.json()["skipped"] == 0


async def test_csv_import_reports_rows_without_a_name(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event()
    csv_body = "name,phone\nRamesh,900000001\n,900000002\n"
    resp = await client.post(
        f"/api/organizer/events/{event.id}/import-players",
        files={"file": ("players.csv", csv_body, "text/csv")},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1
    assert resp.json()["skipped"] == 1



async def test_practice_copy_isolated_and_starts_clean(
    client, make_event, make_player, make_team, register_player, auth_headers, db
):
    event, _admin, organizer, _auctioneer = await make_event(
        name="Championship", status=AuctionStatus.completed, team_budget=250000
    )
    captain, captain_ap = await register_player(event, name="Captain", base_price=1000)
    player, ap = await register_player(event, name="Auction Player", base_price=5000)
    async with AsyncSessionLocal() as session:
        source_team = Team(
            event_id=event.id,
            name="Falcons",
            captain_id=captain.id,
            budget=200000,
            spent=5000,
            max_players=8,
            rtm_cards_used=1,
        )
        category = PlayerCategory(
            event_id=event.id, name="Premium", base_price=5000, sort_order=2,
            auction_first=True,
        )
        session.add_all([source_team, category])
        await session.flush()
        source_ap = await session.get(AuctionPlayer, ap.id)
        source_ap.category_id = category.id
        source_ap.status = PlayerAuctionStatus.sold
        source_ap.current_bid = 15000
        source_ap.current_bid_team_id = source_team.id
        session.add_all([
            BidSlab(
                event_id=event.id, min_value=0, max_value=50000, increment=1000,
                sort_order=0,
            ),
            TeamPlayer(
                event_id=event.id, team_id=source_team.id, player_id=player.id,
                sold_price=15000,
            ),
            Bid(
                event_id=event.id, auction_player_id=source_ap.id,
                team_id=source_team.id, amount=15000,
            ),
        ])
        await session.commit()

    response = await client.post(
        f"/api/organizer/events/{event.id}/practice-copy",
        headers=auth_headers(organizer),
    )
    assert response.status_code == 201, response.text
    practice_id = response.json()["id"]
    assert response.json()["status"] == "draft"
    assert response.json()["name"] == "Championship - Practice"

    async with AsyncSessionLocal() as session:
        original_ap = await session.get(AuctionPlayer, ap.id)
        copied_ap = (
            await session.execute(
                select(AuctionPlayer).where(
                    AuctionPlayer.event_id == practice_id,
                    AuctionPlayer.base_price == 5000,
                )
            )
        ).scalar_one()
        copied_team = (
            await session.execute(select(Team).where(Team.event_id == practice_id))
        ).scalar_one()
        copied_category = (
            await session.execute(
                select(PlayerCategory).where(PlayerCategory.event_id == practice_id)
            )
        ).scalar_one()
        copied_slab = (
            await session.execute(select(BidSlab).where(BidSlab.event_id == practice_id))
        ).scalar_one()

        assert original_ap.status == PlayerAuctionStatus.sold
        assert original_ap.current_bid == 15000
        assert copied_ap.player_id != original_ap.player_id
        assert copied_ap.status == PlayerAuctionStatus.pending
        assert copied_ap.current_bid == 0
        assert copied_ap.current_bid_team_id is None
        assert copied_ap.category_id == copied_category.id
        assert copied_team.captain_id is not None
        assert copied_team.captain_id != captain.id
        assert copied_team.budget == 200000
        assert copied_team.spent == 0
        assert copied_team.rtm_cards_used == 0
        assert copied_slab.increment == 1000
        assert copied_category.name == "Premium"
        assert (await session.execute(
            select(TeamPlayer).where(TeamPlayer.event_id == practice_id)
        )).scalars().all() == []
        assert (await session.execute(
            select(Bid).where(Bid.event_id == practice_id)
        )).scalars().all() == []
