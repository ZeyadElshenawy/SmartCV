# The ultimate fix for Windows Django runserver issues

Write-Host "Cleaning up dangling background processes..." -ForegroundColor Yellow

# Kill the old suspended python runserver processes hoarding port 8000
$portProcesses = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
if ($portProcesses) {
    Stop-Process -Id $portProcesses -Force -ErrorAction SilentlyContinue 
    Write-Host "=> Freeing up port 8000 from ghost Python servers." -ForegroundColor Green
} else {
    Write-Host "=> Port 8000 is free." -ForegroundColor Green
}

# The true cause of the "System Check" freeze:
# Django's default StatReloader takes several minutes to check the modification timestamps of every single file on Windows.
Write-Host "Ensuring 'watchdog' is installed to fix Windows Reloader freeze..." -ForegroundColor Yellow
pip install watchdog > $null 2>&1

Write-Host "Starting Django Server cleanly..." -ForegroundColor Cyan
python -X importtime manage.py runserver 127.0.0.1:8000 -v 3

