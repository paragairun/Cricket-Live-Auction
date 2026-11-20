"""
Real-browser verification of the auctioneer console.

Loads the actual built Next.js page in Chromium with the backend mocked at the
network layer, then asserts the document does not scroll at any of the viewport
sizes this console is used at. Layout cannot be verified by typecheck, lint or
`next build` -- only by rendering it.
"""
import json, sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3000"
EID = 1

TEAMS = [
    {"id": i + 1, "name": n, "color": c, "logo": None, "captain_id": None,
     "captain_name": None, "budget": 2000000, "spent": s,
     "remaining": 2000000 - s, "max_players": 11, "player_count": pc}
    for i, (n, c, s, pc) in enumerate([
        ("Bajrangbali Gladiators", "#dc2626", 200000, 1),
        ("Pawanputra Chargers", "#2563eb", 200000, 1),
        ("Jain's Maruti Riders", "#16a34a", 630000, 3),
        ("Anjani Veer Warriors", "#9333ea", 300000, 2),
        ("Chiranjeevi Challengers", "#ea580c", 410000, 2),
        ("Kesarinandan Thunders", "#0d9488", 600000, 3),
        ("Shaktipeeth Spartans", "#be123c", 150000, 1),
        ("Ramdoot Royals", "#4f46e5", 0, 0),
    ])
]

PLAYERS = []
for i in range(1, 67):
    status = "sold" if i <= 11 else "unsold" if i <= 16 else "pending"
    PLAYERS.append({
        "id": i, "player_id": 100 + i,
        "name": ["Shaurya Mehta", "Purab Kela", "Kavir Shah", "Arnav Deshpande",
                 "Vivaan Kulkarni", "Reyansh Joshi"][i % 6],
        "profile_photo": None, "base_price": 200000, "category_id": 1,
        "current_bid": 200000, "current_bid_team_id": None,
        "status": status, "version": 1,
    })
# One player on the block, with a leading bid.
PLAYERS[16].update({"status": "active", "current_bid": 450000,
                    "current_bid_team_id": 3, "name": "Shaurya Mehta"})

STATE = {
    "event_id": EID, "status": "active", "active_auction_player_id": 17,
    "no_timer": False, "deadline_epoch": None, "server_time": 0,
    "teams": TEAMS, "players": PLAYERS,
    "bid_slabs": [
        {"id": 1, "min_value": 0, "max_value": 500000, "increment": 25000, "sort_order": 1},
        {"id": 2, "min_value": 500000, "max_value": 1500000, "increment": 50000, "sort_order": 2},
        {"id": 3, "min_value": 1500000, "max_value": 99999999, "increment": 100000, "sort_order": 3},
    ],
    "categories": [{"id": 1, "name": "Marquee Kids", "base_price": 200000,
                    "sort_order": 1, "auction_first": True}],
    "timing": {"base_seconds": 30, "soft_close_threshold_seconds": 10,
               "soft_close_extension_seconds": 10},
    "rules": {"team_budget": 2000000, "team_max_players": 11, "team_min_players": 7,
              "player_base_price": 200000, "auction_mode": "random",
              "rtm_enabled": True, "rtm_cards_per_team": 1},
    "rtm": None, "nominations": [], "seq": 1,
}

EVENT = {"id": EID, "auctioneer_id": 1, "organizer_id": 1, "admin_id": 1,
         "name": "Sanjeevni Cup Season 11 — Kids", "logo": None}

ROUTES = {
    "/auth/me": {"id": 1, "email": "op@example.com", "roles": ["admin"]},
    f"/auction/events/{EID}": EVENT,
    f"/auction/events/{EID}/state": STATE,
    f"/auction/events/{EID}/teams": [{"id": t["id"], "players": []} for t in TEAMS],
    f"/auction/events/{EID}/viewer-stats": {"live_viewers": 214, "total_unique_viewers": 980},
    f"/auction/events/{EID}/players-info": [
        {"id": p["player_id"], "player_id": p["player_id"], "name": p["name"], "role": "All-Rounder", "age": 12,
         "batting_rating": 8, "bowling_rating": 7, "profile_photo": None}
        for p in PLAYERS
    ],
}

VIEWPORTS = [
    ("laptop-1280x720", 1280, 720),
    ("laptop-1366x768", 1366, 768),
    ("laptop-1440x900", 1440, 900),
    ("macbook-1512x982", 1512, 982),
    ("projector-1920x1080", 1920, 1080),
    ("tv-2560x1440", 2560, 1440),
    ("tv-4k-3840x2160", 3840, 2160),
    ("tablet-1024x768", 1024, 768),
]


def handle(route):
    url = route.request.url
    path = url.split("/api", 1)[1].split("?")[0] if "/api" in url else ""
    for key, body in ROUTES.items():
        if path == key:
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(body))
    return route.fulfill(status=200, content_type="application/json", body="{}")


def main():
    failures, results = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-device-scale-factor=1"])
        for name, w, h in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            ctx.route("**/localhost:8000/api/**", handle)
            ctx.add_init_script("localStorage.setItem('token','test-token');")
            page = ctx.new_page()
            page.goto(f"{BASE}/auction/{EID}/auctioneer", wait_until="networkidle")
            page.wait_for_timeout(1200)

            errors = []
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
            page.wait_for_timeout(300)

            m = page.evaluate("""() => {
                const d = document.documentElement, b = document.body;
                return {
                  scrollH: Math.max(d.scrollHeight, b.scrollHeight),
                  clientH: d.clientHeight,
                  scrollW: Math.max(d.scrollWidth, b.scrollWidth),
                  clientW: d.clientWidth,
                  paddles: document.querySelectorAll('[data-paddle]').length,
                  text: document.body.innerText,
                };
            }""")
            vscroll = m["scrollH"] - m["clientH"]
            hscroll = m["scrollW"] - m["clientW"]

            # Content assertions -- the scroll check alone would pass a blank page.
            must_contain = ["Sanjeevni Cup", "TEAM PADDLES", "SOLD", "UNSOLD",
                            "PICK MANUALLY", "Shaurya Mehta", "Maruti Riders",
                            "PURSE", "MAX BID", "CURRENT BID", "LOT"]
            body = (m["text"] or "").upper()
            missing = [t for t in must_contain if t.upper() not in body]

            ok = vscroll <= 1 and hscroll <= 1 and not missing and not [
                e for e in errors if "WebSocket" not in e and "ws://" not in e]
            results.append((name, w, h, vscroll, hscroll, ok))
            if vscroll > 1 or hscroll > 1:
                failures.append(f"{name}: overflow v={vscroll}px h={hscroll}px")
            if missing:
                failures.append(f"{name}: page did not render {missing}")
            real_errors = [e for e in errors if "WebSocket" not in e and "ws://" not in e]
            if real_errors:
                failures.append(f"{name}: console errors {real_errors[:3]}")
            page.screenshot(path=f"/home/claude/verify/shot-{name}.png")
            ctx.close()
        browser.close()

    print(f"{'viewport':<24}{'size':<14}{'v-overflow':<13}{'h-overflow':<13}result")
    print("-" * 74)
    for name, w, h, v, hh, ok in results:
        print(f"{name:<24}{f'{w}x{h}':<14}{f'{v}px':<13}{f'{hh}px':<13}{'PASS' if ok else 'FAIL'}")
    print()
    if failures:
        print("FAILURES:"); [print("  " + f) for f in failures]; sys.exit(1)
    print(f"All {len(results)} viewports render with zero document scroll.")


if __name__ == "__main__":
    main()
