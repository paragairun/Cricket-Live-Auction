$ErrorActionPreference = "Stop"

$NSSM = "C:\Users\Admin\Documents\Parag_Airun\Techiebutlers Cricket Auction\Cricket-Auction-Platform\backend\nssm\nssm-2.24\win64\nssm.exe"
$APP  = "C:\Users\Admin\Documents\Parag_Airun\Techiebutlers Cricket Auction\Cricket-Auction-Platform\frontend"
$NPM  = "C:\Program Files\nodejs\npm.cmd"
$SVC  = "auction-frontend"

Write-Host "==> Stopping auction-frontend service" -ForegroundColor Cyan
$ErrorActionPreference = "Continue"
& $NSSM stop $SVC 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   Stopped" -ForegroundColor Green
} else {
    Write-Host "   Already stopped or not running" -ForegroundColor Yellow
}

Write-Host "==> Deleting auction-frontend service" -ForegroundColor Cyan
& $NSSM remove $SVC confirm 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   Deleted" -ForegroundColor Green
} else {
    sc.exe delete $SVC 2>&1 | Out-Null
    Start-Sleep -Seconds 3
}

Write-Host "==> Verifying service is gone" -ForegroundColor Cyan
& $NSSM status $SVC 2>&1 | Out-Null
$statusExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($statusExit -ne 0) {
    Write-Host "   Service is fully removed - good" -ForegroundColor Green
} else {
    Write-Host "   Service still exists (close Services MMC if open and retry)" -ForegroundColor Red
    exit 1
}

Write-Host "==> Installing auction-frontend service (fresh)" -ForegroundColor Cyan
& $NSSM install $SVC "$NPM"
& $NSSM set $SVC AppDirectory "$APP"
& $NSSM set $SVC AppParameters "run start"
& $NSSM set $SVC Description "Cricket Auction Platform - Next.js Frontend"
& $NSSM set $SVC AppEnvironmentExtra "NEXT_PUBLIC_API_URL=http://localhost:8000/api" "NEXT_PUBLIC_WS_URL=ws://localhost:8000/api/auction/ws"
& $NSSM set $SVC AppStdout "$APP\frontend.log"
& $NSSM set $SVC AppStderr "$APP\frontend.log"
& $NSSM set $SVC AppRotateFiles 1
& $NSSM set $SVC AppRotateSeconds 86400
& $NSSM set $SVC AppRotateBytes 10485760
& $NSSM set $SVC AppRestartDelay 3000

Write-Host "==> Starting service" -ForegroundColor Cyan
& $NSSM start $SVC

Write-Host "==> Service status" -ForegroundColor Cyan
& $NSSM status $SVC

Write-Host "Done. Open http://localhost:3000 to verify." -ForegroundColor Green
