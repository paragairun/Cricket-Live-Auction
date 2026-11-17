"""Tests for the Cricket Auction Platform backend."""

import pytest
from sqlalchemy import select
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ── Auth ───────────────────────────────────────────────────────────────────

async def test_register_and_login(client: AsyncClient):
    resp = await client.post(
        "/api/auth/register",
        json={"name": "Alice", "email": "alice@example.com", "password": "secret123"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["user"]["email"] == "alice@example.com"
    assert "player" in data["user"]["roles"]
    assert data["access_token"]

    # Duplicate registration
    resp2 = await client.post(
        "/api/auth/register",
        json={"name": "Alice2", "email": "alice@example.com", "password": "secret123"},
    )
    assert resp2.status_code == 400

    # Login
    resp3 = await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "secret123"}
    )
    assert resp3.status_code == 200
    assert resp3.json()["user"]["email"] == "alice@example.com"

    # Wrong password
    resp4 = await client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "wrong"}
    )
    assert resp4.status_code == 401


async def test_me_endpoint(client: AsyncClient, make_user):
    from app.models.user import ROLE_PLAYER

    await make_user(email="me@example.com", name="Me", roles=[ROLE_PLAYER])

    # Get token via login
    login = await client.post(
        "/api/auth/login", json={"email": "me@example.com", "password": "password123"}
    )
    token = login.json()["access_token"]

    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


# ── Organizer events ──────────────────────────────────────────────────────

async def test_list_my_events(client: AsyncClient, make_event, auth_token):
    event, org = await make_event(name="My League")
    token = await auth_token(email="org@example.com")

    resp = await client.get(
        "/api/organizer/events", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["name"] == "My League"


async def test_organizer_cannot_access_others_event(client: AsyncClient, make_event, auth_token, make_user):
    event, org = await make_event()
    token = await auth_token(email="org@example.com")

    # Create another organizer, log in as them, try to access event 1
    await make_user(email="other@example.com", name="Other Org", roles=["player", "organizer"])
    other_token = await auth_token(email="other@example.com")

    resp = await client.get(
        f"/api/organizer/events/{event.id}/players",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 403


# ── Bid slabs ──────────────────────────────────────────────────────────────

async def test_create_and_list_bid_slabs(client: AsyncClient, make_event, auth_token):
    event, org = await make_event()
    token = await auth_token(email="org@example.com")

    # Create slabs
    resp = await client.post(
        f"/api/organizer/events/{event.id}/bid-slabs",
        json={"min_value": 10000, "max_value": 100000, "increment": 2000, "sort_order": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["increment"] == 2000

    resp2 = await client.post(
        f"/api/organizer/events/{event.id}/bid-slabs",
        json={"min_value": 100000, "max_value": 500000, "increment": 7000, "sort_order": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 201

    # List
    resp3 = await client.get(
        f"/api/organizer/events/{event.id}/bid-slabs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp3.status_code == 200
    slabs = resp3.json()
    assert len(slabs) == 2
    assert slabs[0]["sort_order"] == 1
    assert slabs[1]["sort_order"] == 2


# ── Player categories ─────────────────────────────────────────────────────

async def test_create_and_list_categories(client: AsyncClient, make_event, auth_token):
    event, org = await make_event()
    token = await auth_token(email="org@example.com")

    resp = await client.post(
        f"/api/organizer/events/{event.id}/categories",
        json={"name": "Captain", "base_price": 50000, "sort_order": 1, "auction_first": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["auction_first"] is True

    resp2 = await client.get(
        f"/api/organizer/events/{event.id}/categories",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 200
    cats = resp2.json()
    assert len(cats) == 1
    assert cats[0]["name"] == "Captain"


# ── Players ────────────────────────────────────────────────────────────────

async def test_add_player_to_event(client: AsyncClient, make_event, make_user, auth_token):
    event, org = await make_event()
    token = await auth_token(email="org@example.com")

    # Player not yet in event
    player = await make_user(email="p1@example.com", name="P1")

    resp = await client.post(
        f"/api/organizer/events/{event.id}/players",
        json={"player_id": player.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text

    # List players
    resp2 = await client.get(
        f"/api/organizer/events/{event.id}/players",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 200
    players = resp2.json()
    assert len(players) == 1


# ── Edit player profile ───────────────────────────────────────────────────

async def test_edit_player_profile(client: AsyncClient, make_event, make_player, auth_token):
    event, org = await make_event()
    token = await auth_token(email="org@example.com")

    player, ap = await make_player(event.id, email="p1@example.com", name="P1")

    resp = await client.patch(
        f"/api/organizer/events/{event.id}/players/{player.id}/profile",
        json={
            "name": "P1 Updated",
            "age": 28,
            "batting_rating": 8.5,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "P1 Updated"
    assert data["age"] == 28
    assert data["batting_rating"] == 8.5


async def test_edit_player_profile_with_category(client: AsyncClient, make_event, make_player, auth_token):
    event, org = await make_event()
    token = await auth_token(email="org@example.com")

    # Create a category
    cat_resp = await client.post(
        f"/api/organizer/events/{event.id}/categories",
        json={"name": "Captain", "base_price": 50000, "sort_order": 1, "auction_first": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    cat_id = cat_resp.json()["id"]

    player, ap = await make_player(event.id, email="p1@example.com", name="P1")

    resp = await client.patch(
        f"/api/organizer/events/{event.id}/players/{player.id}/profile",
        json={"category_id": cat_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    # Verify category was set on AuctionPlayer
    list_resp = await client.get(
        f"/api/organizer/events/{event.id}/players",
        headers={"Authorization": f"Bearer {token}"},
    )
    players = list_resp.json()
    assert players[0]["category_id"] == cat_id


# ── No timer setting ──────────────────────────────────────────────────────

async def test_no_timer_setting(client: AsyncClient, make_event, auth_token):
    event, org = await make_event(no_timer=True)
    token = await auth_token(email="org@example.com")

    resp = await client.get(f"/api/auction/events/{event.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["no_timer"] is True

    # Toggle via settings
    resp2 = await client.patch(
        f"/api/organizer/events/{event.id}/settings",
        json={"no_timer": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["no_timer"] is False


# ── Re-auction all unsold ─────────────────────────────────────────────────

async def test_re_auction_all_unsold(client: AsyncClient, make_event, make_player, make_user, auth_token):
    event, org = await make_event(status="active")
    # The re-auction endpoint requires the auctioneer role.
    await make_user(email="ae@example.com", name="Auctioneer", roles=["player", "auctioneer"])
    token = await auth_token(email="ae@example.com")

    # Create 3 unsold players
    players = []
    for i in range(3):
        p, ap = await make_player(
            event.id,
            email=f"unsold{i}@example.com",
            name=f"Unsold {i}",
            status="unsold",
        )
        players.append(p)

    resp = await client.post(
        f"/api/auction/events/{event.id}/re-auction-all-unsold",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 3

    # Verify they are back to pending (list as the event owner/organizer)
    org_token = await auth_token(email="org@example.com")
    list_resp = await client.get(
        f"/api/organizer/events/{event.id}/players",
        headers={"Authorization": f"Bearer {org_token}"},
    )
    players_list = list_resp.json()
    pending = [p for p in players_list if p["status"] == "pending"]
    assert len(pending) == 3


async def test_re_auction_all_unsold_blocks_when_active_player(client: AsyncClient, make_event, make_player, make_user, auth_token):
    event, org = await make_event(status="active")
    await make_user(email="ae@example.com", name="Auctioneer", roles=["player", "auctioneer"])
    token = await auth_token(email="ae@example.com")

    player, ap = await make_player(event.id, email="active@example.com", name="Active Player", status="active")

    resp = await client.post(
        f"/api/auction/events/{event.id}/re-auction-all-unsold",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400

