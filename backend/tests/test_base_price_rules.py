"""Base price rules: a category's price governs.

New in 2.1.2 (2026-11-20).

When a player is in a category, that category's price IS their base price. The
per-player figure and the event default are out of the picture entirely — the
point of a category is that everyone in it starts at the same number.

The engine already honoured this at auction time, but the stored
AuctionPlayer.base_price was not kept in step: assigning "Diamond (1000000)" to
a player left them stored at 1000, the edit form showed 1000, and saving that
form wrote the stale figure straight back.
"""
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.auction import AuctionPlayer, PlayerAuctionStatus, PlayerCategory
from app.services import auction_service


async def _category(client, event, organizer, auth_headers, name, base_price):
    resp = await client.post(
        f"/api/organizer/events/{event.id}/categories",
        json={"name": name, "base_price": base_price},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_player(client, event, organizer, auth_headers, name, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"/api/organizer/events/{event.id}/players"
    if query:
        url += f"?{query}"
    resp = await client.post(url, json={"name": name}, headers=auth_headers(organizer))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _stored_base(auction_player_id: int) -> int:
    async with AsyncSessionLocal() as session:
        ap = await session.get(AuctionPlayer, auction_player_id)
        return ap.base_price


async def test_category_price_wins_when_adding_a_player(
    client, make_event, auth_headers
):
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    diamond = await _category(client, event, organizer, auth_headers, "Diamond", 1000000)

    # A base price is supplied AND a category — the category must win.
    player = await _add_player(
        client, event, organizer, auth_headers, "Ajay",
        category_id=diamond["id"], base_price=1000,
    )
    assert player["base_price"] == 1000000
    assert await _stored_base(player["auction_player_id"]) == 1000000


async def test_moving_a_player_into_a_category_reprices_them(
    client, make_event, auth_headers
):
    """The reported bug: category Diamond, base price still showing 1000."""
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    diamond = await _category(client, event, organizer, auth_headers, "Diamond", 1000000)

    player = await _add_player(client, event, organizer, auth_headers, "Ajay")
    ap_id = player["auction_player_id"]
    assert await _stored_base(ap_id) == 1000

    resp = await client.patch(
        f"/api/organizer/events/{event.id}/players/{ap_id}/auction-settings",
        json={"category_id": diamond["id"]},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["base_price"] == 1000000
    assert await _stored_base(ap_id) == 1000000


async def test_a_stale_base_price_cannot_overwrite_the_category(
    client, make_event, auth_headers
):
    """Saving the edit form, which carries the old figure, must not win."""
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    diamond = await _category(client, event, organizer, auth_headers, "Diamond", 1000000)
    player = await _add_player(
        client, event, organizer, auth_headers, "Ajay", category_id=diamond["id"]
    )
    ap_id = player["auction_player_id"]

    resp = await client.patch(
        f"/api/organizer/events/{event.id}/players/{ap_id}/auction-settings",
        json={"category_id": diamond["id"], "base_price": 1000},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["base_price"] == 1000000, "The category must outrank the form"


async def test_per_player_price_applies_only_without_a_category(
    client, make_event, auth_headers
):
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    player = await _add_player(
        client, event, organizer, auth_headers, "Uncategorised", base_price=7500
    )
    assert player["base_price"] == 7500


async def test_a_category_with_no_price_falls_back(client, make_event, auth_headers):
    """A category that sets no price must not force everyone to zero."""
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    plain = await _category(client, event, organizer, auth_headers, "Unpriced", 0)

    default_player = await _add_player(
        client, event, organizer, auth_headers, "A", category_id=plain["id"]
    )
    assert default_player["base_price"] == 1000

    explicit = await _add_player(
        client, event, organizer, auth_headers, "B",
        category_id=plain["id"], base_price=4000,
    )
    assert explicit["base_price"] == 4000


async def test_repricing_a_category_moves_its_players(client, make_event, auth_headers):
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    gold = await _category(client, event, organizer, auth_headers, "Gold", 50000)
    p1 = await _add_player(
        client, event, organizer, auth_headers, "One", category_id=gold["id"]
    )
    p2 = await _add_player(
        client, event, organizer, auth_headers, "Two", category_id=gold["id"]
    )
    outsider = await _add_player(client, event, organizer, auth_headers, "Outsider")

    resp = await client.patch(
        f"/api/organizer/events/{event.id}/categories/{gold['id']}",
        json={"base_price": 75000},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 200, resp.text

    assert await _stored_base(p1["auction_player_id"]) == 75000
    assert await _stored_base(p2["auction_player_id"]) == 75000
    # A player outside the category must be untouched.
    assert await _stored_base(outsider["auction_player_id"]) == 1000


async def test_deleting_a_category_returns_players_to_the_default(
    client, make_event, auth_headers
):
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    gold = await _category(client, event, organizer, auth_headers, "Gold", 50000)
    player = await _add_player(
        client, event, organizer, auth_headers, "One", category_id=gold["id"]
    )
    assert await _stored_base(player["auction_player_id"]) == 50000

    resp = await client.delete(
        f"/api/organizer/events/{event.id}/categories/{gold['id']}",
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 204

    assert await _stored_base(player["auction_player_id"]) == 1000


async def test_csv_import_lets_the_category_outrank_a_base_price_column(
    client, make_event, auth_headers
):
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    await _category(client, event, organizer, auth_headers, "Diamond", 1000000)

    csv_body = "name,category,base_price\nAjay,Diamond,1000\nRavi,,2500\n"
    resp = await client.post(
        f"/api/organizer/events/{event.id}/import-players",
        files={"file": ("players.csv", csv_body, "text/csv")},
        headers=auth_headers(organizer),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["imported"] == 2
    # The clash is reported rather than applied silently.
    assert any("base_price ignored" in e for e in resp.json()["errors"])

    listing = await client.get(
        f"/api/organizer/events/{event.id}/players", headers=auth_headers(organizer)
    )
    by_name = {p["name"]: p for p in listing.json()}
    assert by_name["Ajay"]["base_price"] == 1000000, "Category wins"
    assert by_name["Ravi"]["base_price"] == 2500, "No category, so the column applies"


async def test_the_auction_opens_at_the_category_price(
    client, make_event, make_team, auth_headers
):
    """End to end: the figure the player actually goes up at."""
    from app.models.auction import AuctionEvent, AuctionStatus

    # Set up while the event is still a draft — adding players to a running
    # auction is refused, correctly.
    event, _admin, organizer, _auctioneer = await make_event(player_base_price=1000)
    await make_team(event, name="A")
    diamond = await _category(client, event, organizer, auth_headers, "Diamond", 1000000)
    player = await _add_player(
        client, event, organizer, auth_headers, "Ajay", category_id=diamond["id"]
    )

    async with AsyncSessionLocal() as session:
        row = await session.get(AuctionEvent, event.id)
        row.status = AuctionStatus.active
        await session.commit()

    async with AsyncSessionLocal() as session:
        ap = await auction_service.set_next_player(
            event.id, player["auction_player_id"], session
        )

    assert ap.base_price == 1000000
    assert ap.current_bid == 1000000, "Bidding must open at the category price"



async def test_changing_common_base_price_reprices_unpriced_players_only(
    client, make_event, auth_headers
):
    """Changing the event default must update eligible stored player prices."""
    from app.models.auction import AuctionEvent

    event, _admin, organizer, _auctioneer = await make_event(player_base_price=500000)
    pending = await _add_player(client, event, organizer, auth_headers, "Pending")
    unsold = await _add_player(client, event, organizer, auth_headers, "Unsold")
    sold = await _add_player(client, event, organizer, auth_headers, "Sold")
    active = await _add_player(client, event, organizer, auth_headers, "On the block")
    priced_category = await _category(
        client, event, organizer, auth_headers, "Premium", 800000
    )
    categorized = await _add_player(
        client, event, organizer, auth_headers, "Categorized",
        category_id=priced_category["id"],
    )

    async with AsyncSessionLocal() as session:
        for player, status in (
            (unsold, PlayerAuctionStatus.unsold),
            (sold, PlayerAuctionStatus.sold),
            (active, PlayerAuctionStatus.active),
        ):
            row = await session.get(AuctionPlayer, player["auction_player_id"])
            row.status = status
        await session.commit()

    response = await client.patch(
        f"/api/organizer/events/{event.id}/settings",
        json={"player_base_price": 200000},
        headers=auth_headers(organizer),
    )
    assert response.status_code == 200, response.text
    assert response.json()["player_base_price"] == 200000

    assert await _stored_base(pending["auction_player_id"]) == 200000
    assert await _stored_base(unsold["auction_player_id"]) == 200000
    assert await _stored_base(sold["auction_player_id"]) == 500000
    assert await _stored_base(active["auction_player_id"]) == 500000
    assert await _stored_base(categorized["auction_player_id"]) == 800000
