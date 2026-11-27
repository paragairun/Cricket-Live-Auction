$ErrorActionPreference = "Stop"

$APP = $PSScriptRoot
$PROJECT_ROOT = Split-Path -Parent $APP
$NSSM = Join-Path $PROJECT_ROOT "backend\nssm\nssm-2.24\win64\nssm.exe"

if (-not (Test-Path $NSSM)) {
    throw "NSSM executable not found at '$NSSM'. Check the project folder structure."
}

Push-Location $APP
try {
    Write-Host "Stopping auction-frontend service..." -ForegroundColor Yellow
    & $NSSM stop auction-frontend

    Write-Host "Cleaning .next cache..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force (Join-Path $APP ".next") -ErrorAction SilentlyContinue

    Write-Host "Building production bundle..." -ForegroundColor Cyan
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed with exit code $LASTEXITCODE."
    }

    Write-Host "Starting auction-frontend service..." -ForegroundColor Green
    & $NSSM start auction-frontend

    Write-Host "Restarting auction-backend service..." -ForegroundColor Green
    & $NSSM restart auction-backend

    Write-Host "Done! Hard-refresh your browser (Ctrl + Shift + R)." -ForegroundColor Green
}
finally {
    Pop-Location
}
