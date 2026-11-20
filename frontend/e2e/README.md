# Browser checks for the auctioneer console

Layout cannot be verified by `tsc`, ESLint or `next build`. These render the
real page in Chromium with the backend mocked at the network layer.

They exist because three production bugs in a row — a React render loop, a
white screen, and a name cropped to "P…" — all passed typecheck, unit tests and
a clean build. Nothing in that chain ever rendered a component.

## Running

```bash
npm run build
npx next start -p 3000 &
python3 -m playwright install --with-deps chromium
python3 e2e/no_scroll_and_render.py     # 8 viewports, zero document scroll + content present
python3 e2e/name_never_cropped.py       # long and short names, never clipped
```

`no_scroll_and_render.py` asserts content as well as scroll, deliberately: a
blank page does not scroll either, and an earlier version of this check passed a
white screen.
