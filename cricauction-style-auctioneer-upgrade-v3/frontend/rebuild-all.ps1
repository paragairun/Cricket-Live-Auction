Write-Host "Stopping auction-frontend service..." -ForegroundColor Yellow
& "C:\Users\Admin\Documents\Parag_Airun\Techiebutlers Cricket Auction\Cricket-Auction-Platform\backend\nssm\nssm-2.24\win64\nssm.exe" stop auction-frontend

Write-Host "Cleaning .next cache..." -ForegroundColor Yellow
Remove-Item -Recurse -Force .next -ErrorAction SilentlyContinue

Write-Host "Building production bundle..." -ForegroundColor Cyan
npm run build

Write-Host "Starting auction-frontend service..." -ForegroundColor Green
& "C:\Users\Admin\Documents\Parag_Airun\Techiebutlers Cricket Auction\Cricket-Auction-Platform\backend\nssm\nssm-2.24\win64\nssm.exe" start auction-frontend

Write-Host "Restarting auction-backend service..." -ForegroundColor Green
& "C:\Users\Admin\Documents\Parag_Airun\Techiebutlers Cricket Auction\Cricket-Auction-Platform\backend\nssm\nssm-2.24\win64\nssm.exe" restart auction-backend

Write-Host "Done! Hard-refresh your browser (Ctrl + Shift + R)." -ForegroundColor Green
