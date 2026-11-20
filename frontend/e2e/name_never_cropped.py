"""Prove the player's name and details are never cropped, however long the name."""
import json, sys, importlib.util
spec = importlib.util.spec_from_file_location("base", "e2e/no_scroll_and_render.py")
base = importlib.util.module_from_spec(spec); spec.loader.exec_module_from = None
exec(open("e2e/no_scroll_and_render.py").read().split('def main()')[0], base.__dict__)
from playwright.sync_api import sync_playwright

LONG = "Venkataraghavan Parthasarathy Balasubramanian"
SHORT = "Kai Roy"

def run(name_value, viewport):
    base.PLAYERS[16]["name"] = name_value
    for row in base.ROUTES[f"/auction/events/{base.EID}/players-info"]:
        if row["player_id"] == base.PLAYERS[16]["player_id"]:
            row["name"] = name_value
    base.ROUTES[f"/auction/events/{base.EID}/state"] = base.STATE

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        ctx.route("**/localhost:8000/api/**", base.handle)
        ctx.add_init_script("localStorage.setItem('token','t');")
        page = ctx.new_page()
        page.goto(f"{base.BASE}/auction/{base.EID}/auctioneer", wait_until="networkidle")
        page.wait_for_timeout(1600)
        r = page.evaluate("""() => {
            const h = [...document.querySelectorAll('h2')].find(e => e.textContent.trim().length > 2);
            if (!h) return null;
            const cs = getComputedStyle(h);
            return {
              text: h.textContent.trim(),
              overflowY: h.scrollHeight - h.clientHeight,
              overflowX: h.scrollWidth - h.clientWidth,
              fontPx: parseFloat(cs.fontSize),
              boxOverflow: h.parentElement.scrollHeight - h.parentElement.clientHeight,
              docScroll: document.documentElement.scrollHeight - document.documentElement.clientHeight,
            };
        }""")
        page.screenshot(path=f"/home/claude/verify/shot-longname-{viewport[0]}x{viewport[1]}.png")
        b.close()
    return r

fails = []
print(f"{'case':<10}{'viewport':<12}{'font':<9}{'clipped?':<11}{'doc scroll':<12}result")
print("-" * 66)
for label, value in (("long", LONG), ("short", SHORT)):
    for vp in [(1280, 720), (1920, 1080), (3840, 2160)]:
        r = run(value, vp)
        if r is None:
            fails.append(f"{label}@{vp}: name element not found"); continue
        complete = r["text"] == value
        clipped = r["overflowY"] > 1 or r["overflowX"] > 1 or r["boxOverflow"] > 1
        ok = complete and not clipped and r["docScroll"] <= 1
        print(f"{label:<10}{f'{vp[0]}x{vp[1]}':<12}{r['fontPx']:<9.1f}"
              f"{('YES' if clipped else 'no'):<11}{str(r["docScroll"])+"px":<12}"
              f"{'PASS' if ok else 'FAIL'}")
        if not complete: fails.append(f"{label}@{vp}: text altered -> {r['text']!r}")
        if clipped: fails.append(f"{label}@{vp}: clipped y={r['overflowY']} x={r['overflowX']} box={r['boxOverflow']}")
        if r["docScroll"] > 1: fails.append(f"{label}@{vp}: document scrolls {r['docScroll']}px")

print()
if fails:
    print("FAILURES:"); [print("  " + f) for f in fails]; sys.exit(1)
print("Name renders in full, unclipped, at every size — long names shrink instead.")
