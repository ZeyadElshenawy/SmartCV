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

# Also free port 8000 explicitly. Using netstat instead of
# Get-NetTCPConnection because the latter goes through WMI, which hangs
# indefinitely on this machine (same root cause as the Py3.13 platform.machine
# hang patched in manage.py). netstat speaks straight to the TCP stack.
$portOwners = @((netstat -ano | Select-String ':8000\s+.*LISTENING').Line |
    Where-Object { $_ } |
    ForEach-Object { ($_.Trim() -split '\s+')[-1] } |
    Select-Object -Unique)
if ($portOwners) {
    $portOwners | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Write-Host "=> Freed port 8000." -ForegroundColor Green
}

# Fix Windows Reloader freeze. Only run pip if watchdog isn't already
# importable — `pip install` calls platform.machine() to pick a wheel tag,
# which hangs on this machine's broken WMI (same root cause as the
# manage.py patch). Plain `python -c "import watchdog"` doesn't touch WMI.
Write-Host "Ensuring 'watchdog' is installed to fix Windows Reloader freeze..." -ForegroundColor Yellow
python -c "import watchdog" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "=> Installing watchdog..." -ForegroundColor Yellow
    pip install watchdog > $null 2>&1
} else {
    Write-Host "=> watchdog already installed." -ForegroundColor Green
}

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