$ErrorActionPreference = "Stop"

$APP = $PSScriptRoot
$PROJECT_ROOT = Split-Path -Parent $APP
$NSSM = Join-Path $PROJECT_ROOT "backend\nssm\nssm-2.24\win64\nssm.exe"
$NODE = (Get-Command node.exe -ErrorAction Stop).Source
$NEXT = Join-Path $APP "node_modules\next\dist\bin\next"

if (-not (Test-Path $NSSM)) {
    throw "NSSM executable not found at '$NSSM'. Check the project folder structure."
}
if (-not (Test-Path $NEXT)) {
    throw "Next.js executable not found at '$NEXT'. Run npm install in the frontend folder first."
}

Write-Host "==> Checking existing auction-frontend service" -ForegroundColor Cyan
$status = & $NSSM status auction-frontend 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   Service already exists - removing it first" -ForegroundColor Yellow
    & $NSSM stop auction-frontend
    & $NSSM remove auction-frontend confirm
}

Write-Host "==> Installing auction-frontend service" -ForegroundColor Cyan
& $NSSM install auction-frontend "$NODE"
& $NSSM set auction-frontend AppDirectory "$APP"
& $NSSM set auction-frontend AppParameters "$NEXT start"
& $NSSM set auction-frontend Description "Cricket Auction Platform - Next.js Frontend"
& $NSSM set auction-frontend AppEnvironmentExtra "NEXT_PUBLIC_API_URL=http://localhost:8000/api NEXT_PUBLIC_WS_URL=ws://localhost:8000/api/auction/ws"
& $NSSM set auction-frontend AppStdout (Join-Path $APP "frontend.log")
& $NSSM set auction-frontend AppStderr (Join-Path $APP "frontend.log")
& $NSSM set auction-frontend AppRotateFiles 1
& $NSSM set auction-frontend AppRotateSeconds 86400
& $NSSM set auction-frontend AppRotateBytes 10485760
& $NSSM set auction-frontend AppRestartDelay 3000

Write-Host "==> Starting service" -ForegroundColor Cyan
& $NSSM start auction-frontend

Write-Host "==> Service status" -ForegroundColor Cyan
& $NSSM status auction-frontend

Write-Host "Done. Open http://localhost:3000 (or your configured port) to verify." -ForegroundColor Green
