# Complete Drop-In Auctioneer Console Package (v3)

All 5 files in this archive are **complete, ready-to-overwrite files** (no manual merging required).

## Where to Copy Each File
1. `backend/app/schemas/auction.py` -> Overwrite `backend/app/schemas/auction.py`
2. `backend/app/api/auction.py` -> Overwrite `backend/app/api/auction.py`
3. `backend/app/services/auction_service.py` -> Overwrite `backend/app/services/auction_service.py`
4. `frontend/store/auction.ts` -> Overwrite `frontend/store/auction.ts`
5. `frontend/app/auction/[eventId]/auctioneer/page.tsx` -> Overwrite `frontend/app/auction/[eventId]/auctioneer/page.tsx`
6. `frontend/rebuild-all.ps1` -> Overwrite `frontend/rebuild-all.ps1`

## Build & Restart
In Administrator PowerShell inside `frontend/`:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\rebuild-all.ps1
```
Then hard-refresh your browser (`Ctrl + Shift + R`) and click **↻ Reset** in the top-right bar to clear any stale active state.
