# The ultimate fix for Windows Django runserver issues

Write-Host "Cleaning up dangling background processes..." -ForegroundColor Yellow

# Kill ALL old Python processes that could be ghost servers
$pythonProcesses = Get-Process -Name python -ErrorAction SilentlyContinue
if ($pythonProcesses) {
    $pythonProcesses | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "=> Killed $($pythonProcesses.Count) ghost Python processes." -ForegroundColor Green
    Start-Sleep -Seconds 1
} else {
    Write-Host "=> No ghost Python processes found." -ForegroundColor Green
}

# Also free port 8000 explicitly
$portProcesses = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
if ($portProcesses) {
    Stop-Process -Id $portProcesses -Force -ErrorAction SilentlyContinue 
    Write-Host "=> Freed port 8000." -ForegroundColor Green
}

# Fix Windows Reloader freeze
Write-Host "Ensuring 'watchdog' is installed to fix Windows Reloader freeze..." -ForegroundColor Yellow
pip install watchdog > $null 2>&1

# Launch browser when the server boots
Start-Job -ScriptBlock {
    $retryCount = 0
    $maxRetries = 30
    while ($retryCount -lt $maxRetries) {
        $connection = Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -InformationLevel Quiet -WarningAction SilentlyContinue
        if ($connection) {
            Start-Process "http://127.0.0.1:8000/"
            break
        }
        Start-Sleep -Seconds 1
        $retryCount++
    }
} | Out-Null

Write-Host "Starting Primary Web Server..." -ForegroundColor Green
python manage.py runserver 127.0.0.1:8000