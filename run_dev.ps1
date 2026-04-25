# The ultimate fix for Windows Django runserver issues

# Activate the project-local venv so `python` and `pip` resolve to .venv\Scripts\
# regardless of the user's PATH. The venv was created with --system-site-packages,
# so it inherits Django and other globally installed deps without re-installing.
# To run other commands inside the same venv (tests, migrate, shell):
#   . .\.venv\Scripts\Activate.ps1 ; python manage.py test
$venvActivate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
    Write-Host "=> Activated .venv ($((Get-Command python).Source))." -ForegroundColor Green
} else {
    Write-Host "=> No .venv found at $venvActivate; using system python." -ForegroundColor Yellow
}

Write-Host "Cleaning up dangling background processes..." -ForegroundColor Yellow

# Kill ALL old Python processes that could be ghost servers
$pythonProcesses = Get-Process -Name python -ErrorAction SilentlyContinue
if ($pythonProcesses) {
    $pythonProcesses | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "=> Killed $($pythonProcesses.Count) ghost Python processes." -ForegroundColor Green
    # Force-killed Python won't cleanly close its Supabase PgBouncer sockets;
    # the pooler needs a few seconds to reap those client slots before a fresh
    # connection on this run can succeed. Otherwise runserver hangs on the
    # first DB connect after "System check identified no issues".
    Write-Host "=> Waiting 5s for Supabase PgBouncer to reap orphaned client slots..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 5
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

# Launch browser when the server boots. Use netstat instead of Test-NetConnection
# because Test-NetConnection goes through WMI, which hangs indefinitely on this
# machine — same root cause as the runserver-port-free block above. The hung
# poller would either never open the browser, or open it too early (resulting
# in a connection-refused / blank page before Django was ready).
Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 30; $i++) {
        $listening = (netstat -ano | Select-String ':8000\s+.*LISTENING')
        if ($listening) {
            Start-Sleep -Milliseconds 400  # tiny grace period for the worker to accept
            Start-Process "http://127.0.0.1:8000/"
            break
        }
        Start-Sleep -Seconds 1
    }
} | Out-Null

Write-Host "Starting Primary Web Server..." -ForegroundColor Green
python manage.py runserver 127.0.0.1:8000