<#
.SYNOPSIS
    LeadManager4U.ai — One-Click Windows Installer / Upgrader
.DESCRIPTION
    Installs Python (if missing), creates a virtualenv, deploys the Django project,
    maps lm.ai in the Windows hosts file, and registers a Windows service via NSSM.
    On re-run it upgrades in place, preserving the SQLite database.
.NOTES
    Must run as Administrator. The companion .bat launcher handles UAC elevation.
#>

# --------------------------------------------------------------
# CONFIGURATION
# --------------------------------------------------------------

$INSTALL_DIR   = "C:\LeadManager4U"
$SERVICE_NAME  = "LeadManager4U"
$PORT          = 80
$HOST_NAME     = "lm.ai"
$PYTHON_VER    = "3.12.8"
$NSSM_VER      = "2.24"

# Derived paths
$SCRIPT_DIR    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_DIR   = Split-Path -Parent $SCRIPT_DIR
$VENV_DIR      = Join-Path $INSTALL_DIR ".venv"
$TOOLS_DIR     = Join-Path $INSTALL_DIR "tools"
$NSSM_EXE      = Join-Path $TOOLS_DIR  "nssm.exe"
$LOGS_DIR      = Join-Path $INSTALL_DIR "logs"
$BACKUP_DIR    = Join-Path $INSTALL_DIR "backups"
$LOGO_ICO      = Join-Path $SCRIPT_DIR "logo.ico"
$INSTALLED_LOGO= Join-Path $INSTALL_DIR "logo.ico"

$ErrorActionPreference  = "Continue"
$ProgressPreference     = "SilentlyContinue"

# --------------------------------------------------------------
# UTILITY FUNCTIONS
# --------------------------------------------------------------

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "=================================================" -ForegroundColor DarkCyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "=================================================" -ForegroundColor DarkCyan
}

function Write-OK([string]$msg)   { Write-Host "    [OK]   $msg" -ForegroundColor Green  }
function Write-Info([string]$msg) { Write-Host "    [INFO] $msg" -ForegroundColor Gray   }
function Write-Warn([string]$msg) { Write-Host "    [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red    }

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --------------------------------------------------------------
# PRE-FLIGHT
# --------------------------------------------------------------

Clear-Host
Write-Host ""
Write-Host "  +---------------------------------------------------+" -ForegroundColor Magenta
Write-Host "  |          LeadManager4U.ai   Installer             |" -ForegroundColor Magenta
Write-Host "  |               Windows Edition                     |" -ForegroundColor Magenta
Write-Host "  +---------------------------------------------------+" -ForegroundColor Magenta
Write-Host ""

if (-not (Test-IsAdmin)) {
    Write-Fail "This installer must run as Administrator."
    Write-Host "    Right-click Install-LeadManager4U.bat -> Run as administrator." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter to exit"
    exit 1
}

$IS_UPGRADE = Test-Path (Join-Path $INSTALL_DIR "manage.py")
if ($IS_UPGRADE) {
    Write-Host "  Mode: " -NoNewline
    Write-Host "UPGRADE" -ForegroundColor Yellow -NoNewline
    Write-Host " (existing installation detected)" -ForegroundColor Gray
} else {
    Write-Host "  Mode: " -NoNewline
    Write-Host "FRESH INSTALL" -ForegroundColor Green
}
Write-Host "  Install path : $INSTALL_DIR" -ForegroundColor Gray
Write-Host "  URL          : http://$HOST_NAME" -ForegroundColor Gray
Write-Host "  Source       : $PROJECT_DIR" -ForegroundColor Gray
Write-Host ""

# --------------------------------------------------------------
# PHASE 1 / 7 -- PYTHON RUNTIME
# --------------------------------------------------------------

Write-Step "Phase 1/7 - Python Runtime"

$pythonCmd = $null

foreach ($cmd in @("python", "python3")) {
    try {
        $verOutput = & $cmd --version 2>&1
        if ($verOutput -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 10) {
                $pythonCmd = (Get-Command $cmd -ErrorAction Stop).Source
                Write-OK "Found $cmd ($verOutput)"
                break
            }
        }
    } catch {}
}

if (-not $pythonCmd) {
    $knownPaths = @(
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    foreach ($p in $knownPaths) {
        if (Test-Path $p) {
            $pythonCmd = $p
            Write-OK "Found Python at $p"
            break
        }
    }
}

if (-not $pythonCmd) {
    Write-Warn "Python not found - installing Python $PYTHON_VER automatically"

    $pyUrl       = "https://www.python.org/ftp/python/$PYTHON_VER/python-$PYTHON_VER-amd64.exe"
    $pyInstaller = Join-Path $env:TEMP "python-$PYTHON_VER-installer.exe"
    $pyTarget    = "C:\Python312"

    Write-Info "Downloading Python $PYTHON_VER..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
        Write-OK "Download complete"
    } catch {
        Write-Fail "Download failed: $_"
        Write-Host "    Install Python 3.10+ manually from https://www.python.org" -ForegroundColor Yellow
        Write-Host ""
        Read-Host "  Press Enter to exit"
        exit 1
    }

    Write-Info "Installing Python silently..."
    $proc = Start-Process -FilePath $pyInstaller -ArgumentList @(
        "/quiet", "InstallAllUsers=1", "PrependPath=1",
        "TargetDir=$pyTarget", "Include_pip=1",
        "Include_launcher=1", "Include_tcltk=0"
    ) -Wait -PassThru -NoNewWindow

    Remove-Item $pyInstaller -Force -ErrorAction SilentlyContinue

    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        Write-Fail "Python installer returned code $($proc.ExitCode)"
        Write-Host ""
        Read-Host "  Press Enter to exit"
        exit 1
    }

    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path","User")

    $pythonCmd = Join-Path $pyTarget "python.exe"
    if (Test-Path $pythonCmd) {
        Write-OK "Python $PYTHON_VER installed to $pyTarget"
    } else {
        Write-Fail "Python verification failed after install"
        Write-Host ""
        Read-Host "  Press Enter to exit"
        exit 1
    }
}

Write-Info "Verifying pip..."
$pipCheck = & $pythonCmd -m pip --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Info "Bootstrapping pip..."
    & $pythonCmd -m ensurepip --upgrade 2>&1 | Out-Null
}
Write-OK "pip ready"

# --------------------------------------------------------------
# PHASE 2 / 7 -- PROJECT FILES
# --------------------------------------------------------------

Write-Step "Phase 2/7 - Deploy Project Files"

if (-not (Test-Path $INSTALL_DIR)) {
    New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null
    Write-OK "Created $INSTALL_DIR"
}

if ($IS_UPGRADE) {
    $svc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-Info "Stopping existing service for upgrade..."
        try {
            if (Test-Path $NSSM_EXE) {
                & $NSSM_EXE stop $SERVICE_NAME 2>&1 | Out-Null
            } else {
                Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Seconds 3
            Write-OK "Service stopped"
        } catch {
            Write-Warn "Could not stop service gracefully - continuing anyway"
        }
    }
}

if (-not (Test-Path $BACKUP_DIR)) {
    New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null
}

# Relocate any existing loose database backups from root to backups directory
Get-ChildItem -Path $INSTALL_DIR -Filter "db.sqlite3.backup-*" -File -ErrorAction SilentlyContinue | ForEach-Object {
    $dest = Join-Path $BACKUP_DIR $_.Name
    if (-not (Test-Path $dest)) {
        Move-Item $_.FullName $dest -Force -ErrorAction SilentlyContinue
    } else {
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
    }
    Write-Info "Relocated loose backup $($_.Name) -> backups/"
}

if ($IS_UPGRADE) {
    $dbFile = Join-Path $INSTALL_DIR "db.sqlite3"
    if (Test-Path $dbFile) {
        $ts       = Get-Date -Format "yyyyMMdd-HHmmss"
        $dbBackup = Join-Path $BACKUP_DIR "db.sqlite3.backup-$ts"
        Copy-Item $dbFile $dbBackup -Force
        foreach ($ext in @("-wal","-shm")) {
            $src = "$dbFile$ext"
            if (Test-Path $src) { Copy-Item $src "$dbBackup$ext" -Force }
        }
        Write-OK "Database backed up -> backups/db.sqlite3.backup-$ts"

        # Prune old backups, keeping only the 5 most recent
        $backupsList = Get-ChildItem -Path $BACKUP_DIR -Filter "db.sqlite3.backup-*" |
                       Where-Object { $_.Name -notmatch "\-(wal|shm)$" } |
                       Sort-Object LastWriteTime -Descending
        if ($backupsList.Count -gt 5) {
            $oldBackups = $backupsList | Select-Object -Skip 5
            foreach ($old in $oldBackups) {
                Remove-Item $old.FullName -Force -ErrorAction SilentlyContinue
                foreach ($ext in @("-wal","-shm")) {
                    Remove-Item "$($old.FullName)$ext" -Force -ErrorAction SilentlyContinue
                }
                Write-Info "Cleaned up old backup: $($old.Name)"
            }
        }
    }
}

Write-Info "Copying project files..."

$roboSrc  = "`"$PROJECT_DIR`""
$roboDst  = "`"$INSTALL_DIR`""
$roboDirs = ".git __pycache__ installer .venv node_modules tools logs attached_assets backups"
$roboFiles = ".replit replit.nix replit.md"

$roboArgs = "$roboSrc $roboDst /E /XD $roboDirs /XF $roboFiles"

if ($IS_UPGRADE) {
    $roboArgs += " /XF db.sqlite3 db.sqlite3-wal db.sqlite3-shm"
}

$roboArgs += " /NFL /NDL /NJH /NJS /NC /NS /NP"

cmd /c "robocopy $roboArgs" 2>&1 | Out-Null
$roboExit = $LASTEXITCODE

if ($roboExit -ge 8) {
    Write-Warn "Some files may not have copied (robocopy exit code: $roboExit)"
} else {
    Write-OK "Project files deployed to $INSTALL_DIR"
}

# --------------------------------------------------------------
# PHASE 3 / 7 -- VIRTUAL ENVIRONMENT & DEPENDENCIES
# --------------------------------------------------------------

Write-Step "Phase 3/7 - Python Dependencies"

$venvPython = Join-Path $VENV_DIR "Scripts\python.exe"
$venvPip    = Join-Path $VENV_DIR "Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    Write-Info "Creating virtual environment..."
    & $pythonCmd -m venv $VENV_DIR 2>&1 | Out-Null
    if (-not (Test-Path $venvPython)) {
        Write-Fail "Virtual environment creation failed - retrying..."
        & $pythonCmd -m venv $VENV_DIR --clear 2>&1 | Out-Null
        if (-not (Test-Path $venvPython)) {
            Write-Fail "Cannot create virtual environment"
            Write-Host ""
            Read-Host "  Press Enter to exit"
            exit 1
        }
    }
    Write-OK "Virtual environment created"
} else {
    Write-OK "Virtual environment already exists"
}

Write-Info "Upgrading pip..."
& $venvPython -m pip install --upgrade pip --quiet 2>&1 | Out-Null

Write-Info "Installing project dependencies..."
$reqFile = Join-Path $INSTALL_DIR "requirements.txt"

& $venvPip install -r $reqFile waitress --quiet 2>&1 | Out-Null
$pipExit = $LASTEXITCODE

if ($pipExit -ne 0) {
    Write-Warn "First pip attempt had issues - retrying with verbose output..."
    & $venvPip install -r $reqFile waitress 2>&1 | Out-Null
}

Write-OK "All dependencies installed"

# --------------------------------------------------------------
# PHASE 4 / 7 -- DJANGO CONFIGURATION
# --------------------------------------------------------------

Write-Step "Phase 4/7 - Django Setup"

Push-Location $INSTALL_DIR
try {
    $env:DJANGO_SETTINGS_MODULE = "maps_scraper.settings"
    $env:SITE_URL = "http://$HOST_NAME"

    Write-Info "Running database migrations..."
    & $venvPython manage.py migrate --run-syncdb 2>&1 | Out-Null
    Write-OK "Migrations complete"

    Write-Info "Collecting static files..."
    & $venvPython manage.py collectstatic --noinput 2>&1 | Out-Null
    Write-OK "Static files ready"

    Write-Info "Verifying admin account..."
    $userScript = 'import os, django; os.environ.setdefault("DJANGO_SETTINGS_MODULE","maps_scraper.settings"); django.setup(); from django.contrib.auth.models import User; print("CREATED" if not User.objects.filter(username="SA").exists() and User.objects.create_superuser("SA","","admin123") else "EXISTS")'
    $userResult = & $venvPython -c $userScript 2>&1
    if ("$userResult" -match "CREATED") {
        Write-OK "Admin user created  (username: SA  /  password: admin123)"
    } else {
        Write-OK "Admin user verified (SA)"
    }
} finally {
    Pop-Location
}

# --------------------------------------------------------------
# PHASE 5 / 7 -- HOST-FILE MAPPING
# --------------------------------------------------------------

Write-Step "Phase 5/7 - Network: Map lm.ai -> localhost"

$hostsPath    = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$hostsContent = Get-Content $hostsPath -Raw -ErrorAction SilentlyContinue

if ($hostsContent -notmatch "(?m)^\s*127\.0\.0\.1\s+lm\.ai") {
    try {
        $entry = "`r`n# LeadManager4U.ai - added by installer`r`n127.0.0.1    lm.ai`r`n"
        [System.IO.File]::AppendAllText($hostsPath, $entry)
        Write-OK "Added  127.0.0.1  lm.ai  to hosts file"
    } catch {
        Write-Fail "Could not write hosts file: $_"
        Write-Warn "Manually add this line to $hostsPath :"
        Write-Host "      127.0.0.1    lm.ai" -ForegroundColor White
    }
} else {
    Write-OK "lm.ai already mapped in hosts file"
}

& ipconfig /flushdns 2>&1 | Out-Null
Write-OK "DNS cache flushed"

# --------------------------------------------------------------
# PHASE 6 / 7 -- PORT CONFLICT RESOLUTION
# --------------------------------------------------------------

Write-Step "Phase 6/7 - Port $PORT Availability"

$portListeners = Get-NetTCPConnection -LocalPort $PORT -ErrorAction SilentlyContinue |
    Where-Object { $_.State -eq "Listen" }

$conflict = $false
if ($portListeners) {
    foreach ($listener in $portListeners) {
        $proc = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -ne "idle") {
            Write-Warn "Port $PORT in use by  $($proc.ProcessName)  (PID $($listener.OwningProcess))"
            $conflict = $true
        }
    }
}

if ($conflict) {
    foreach ($svcName in @("W3SVC","Apache2.4","Apache","httpd","nginx")) {
        $cs = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if ($cs -and $cs.Status -eq "Running") {
            Write-Info "Stopping conflicting service: $svcName"
            Stop-Service -Name $svcName -Force -ErrorAction SilentlyContinue
            Set-Service  -Name $svcName -StartupType Manual -ErrorAction SilentlyContinue
            Write-OK "$svcName stopped (set to Manual start)"
        }
    }

    Start-Sleep -Seconds 2
    $portListeners = Get-NetTCPConnection -LocalPort $PORT -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq "Listen" }
    if ($portListeners) {
        Write-Warn "Port $PORT still occupied - service may fail to bind"
    } else {
        Write-OK "Port $PORT freed"
    }
} else {
    Write-OK "Port $PORT is available"
}

# --------------------------------------------------------------
# PHASE 7 / 7 -- WINDOWS SERVICE  (NSSM + Waitress)
# --------------------------------------------------------------

Write-Step "Phase 7/7 - Windows Service"

foreach ($dir in @($TOOLS_DIR, $LOGS_DIR)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

if (-not (Test-Path $NSSM_EXE)) {
    Write-Info "Downloading NSSM $NSSM_VER (service manager)..."

    $nssmUrl     = "https://nssm.cc/release/nssm-$NSSM_VER.zip"
    $nssmZip     = Join-Path $env:TEMP "nssm-$NSSM_VER.zip"
    $nssmExtract = Join-Path $env:TEMP "nssm-extract"

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing
        Write-OK "NSSM downloaded"

        if (Test-Path $nssmExtract) { Remove-Item $nssmExtract -Recurse -Force }
        Expand-Archive -Path $nssmZip -DestinationPath $nssmExtract -Force

        $nssmBin = Get-ChildItem -Path $nssmExtract -Filter "nssm.exe" -Recurse |
            Where-Object { $_.DirectoryName -like "*win64*" } |
            Select-Object -First 1
        if (-not $nssmBin) {
            $nssmBin = Get-ChildItem -Path $nssmExtract -Filter "nssm.exe" -Recurse |
                Select-Object -First 1
        }

        Copy-Item $nssmBin.FullName $NSSM_EXE -Force
        Remove-Item $nssmZip     -Force -ErrorAction SilentlyContinue
        Remove-Item $nssmExtract -Recurse -Force -ErrorAction SilentlyContinue
        Write-OK "NSSM installed to $TOOLS_DIR"
    } catch {
        Write-Fail "NSSM download failed: $_"
        Write-Warn "Will fall back to Scheduled Task"
    }
} else {
    Write-OK "NSSM already present"
}

$runServerPath = Join-Path $INSTALL_DIR "run-server.py"

$runServerPy = @'
import os
import sys

SERVICE_DIR = r"C:\LeadManager4U"
os.chdir(SERVICE_DIR)
sys.path.insert(0, SERVICE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "maps_scraper.settings")
os.environ["SITE_URL"] = "http://lm.ai"

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()

from django.conf import settings as django_settings
if "http://lm.ai" not in django_settings.CSRF_TRUSTED_ORIGINS:
    django_settings.CSRF_TRUSTED_ORIGINS.append("http://lm.ai")

from waitress import serve

print("=" * 60)
print("  LeadManager4U.ai")
print("  Listening on http://lm.ai:80")
print("=" * 60)

serve(application, host="0.0.0.0", port=80, threads=4, url_scheme="http")
'@

Set-Content -Path $runServerPath -Value $runServerPy -Encoding UTF8 -Force
Write-OK "Service runner script created"

$useNssm = Test-Path $NSSM_EXE

if ($useNssm) {
    $existingSvc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
    if ($existingSvc) {
        if ($existingSvc.Status -eq "Running") {
            & $NSSM_EXE stop $SERVICE_NAME 2>&1 | Out-Null
            Start-Sleep -Seconds 3
        }
        & $NSSM_EXE remove $SERVICE_NAME confirm 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        Write-Info "Removed previous service registration"
    }

    & $NSSM_EXE install $SERVICE_NAME $venvPython $runServerPath 2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME AppDirectory        $INSTALL_DIR                              2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME DisplayName          "LeadManager4U Web Application"           2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME Description           "LeadManager4U.ai - Lead generation and email campaign platform" 2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME Start                 SERVICE_AUTO_START                       2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME AppStdout             (Join-Path $LOGS_DIR "service-stdout.log") 2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME AppStderr             (Join-Path $LOGS_DIR "service-stderr.log") 2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME AppStdoutCreationDisposition 4  2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME AppStderrCreationDisposition 4  2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME AppRotateFiles       1                                       2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME AppRotateBytes       5242880                                  2>&1 | Out-Null
    & $NSSM_EXE set $SERVICE_NAME AppEnvironmentExtra   "DJANGO_SETTINGS_MODULE=maps_scraper.settings" 2>&1 | Out-Null

    Write-OK "Service '$SERVICE_NAME' registered (auto-start on boot)"

    Write-Info "Starting service..."
    & $NSSM_EXE start $SERVICE_NAME 2>&1 | Out-Null

    $retries = 0
    while ($retries -lt 5) {
        Start-Sleep -Seconds 2
        $svc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq "Running") { break }
        $retries++
    }

    $svc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-OK "Service is RUNNING"
    } else {
        Write-Warn "Service not yet running - check logs at $LOGS_DIR"
    }
} else {
    Write-Warn "NSSM unavailable - creating a Scheduled Task instead"

    $existingTask = Get-ScheduledTask -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
    if ($existingTask) {
        Unregister-ScheduledTask -TaskName $SERVICE_NAME -Confirm:$false 2>&1 | Out-Null
    }

    $action   = New-ScheduledTaskAction -Execute $venvPython -Argument "`"$runServerPath`"" -WorkingDirectory $INSTALL_DIR
    $trigger  = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest -LogonType ServiceAccount

    Register-ScheduledTask -TaskName $SERVICE_NAME -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description "LeadManager4U.ai web application" | Out-Null

    Start-ScheduledTask -TaskName $SERVICE_NAME
    Write-OK "Scheduled task created and started"
}

# --------------------------------------------------------------
# DESKTOP SHORTCUT
# --------------------------------------------------------------

Write-Info "Creating desktop shortcut with LM logo..."
try {
    if (Test-Path $LOGO_ICO) {
        Copy-Item $LOGO_ICO $INSTALLED_LOGO -Force -ErrorAction SilentlyContinue
    }

    $desktopPath  = [Environment]::GetFolderPath("CommonDesktopDirectory")
    if (-not (Test-Path $desktopPath)) {
        $desktopPath = [Environment]::GetFolderPath("Desktop")
    }

    $iconPath = if (Test-Path $INSTALLED_LOGO) { $INSTALLED_LOGO } else { "C:\Windows\System32\shell32.dll" }

    # Internet shortcut (.url)
    $urlShortcut = Join-Path $desktopPath "LeadManager4U.url"
    "[InternetShortcut]`r`nURL=http://$HOST_NAME`r`nIconIndex=0`r`nIconFile=$iconPath" | Set-Content -Path $urlShortcut -Encoding ASCII -Force

    # Windows shell shortcut (.lnk)
    try {
        $wsh = New-Object -ComObject WScript.Shell
        $lnkShortcut = Join-Path $desktopPath "LeadManager4U.lnk"
        $shortcut = $wsh.CreateShortcut($lnkShortcut)
        $shortcut.TargetPath = "http://$HOST_NAME"
        if (Test-Path $INSTALLED_LOGO) {
            $shortcut.IconLocation = "$INSTALLED_LOGO,0"
        }
        $shortcut.Description = "LeadManager4U.ai Web Application"
        $shortcut.Save()
    } catch {}

    Write-OK "Desktop shortcut created -> LeadManager4U (LM Logo)"
} catch {
    Write-Warn "Could not create desktop shortcut: $_"
}

# --------------------------------------------------------------
# VERIFICATION
# --------------------------------------------------------------

Write-Step "Verifying Installation"

Write-Info "Waiting for server to respond at http://$HOST_NAME ..."
$serverReady = $false
$maxAttempts = 20

for ($i = 0; $i -lt $maxAttempts; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://$HOST_NAME" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        $serverReady = $true
        break
    } catch {
        if ($_.Exception -and $_.Exception.Response) {
            $code = 0
            try { $code = [int]$_.Exception.Response.StatusCode } catch {}
            if ($code -in @(200, 301, 302)) {
                $serverReady = $true
                break
            }
        }
    }
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline -ForegroundColor DarkGray
}
Write-Host ""

if ($serverReady) {
    Write-OK "Server is responding at http://$HOST_NAME"
} else {
    Write-Warn "Server not responding yet - it may still be starting"
    Write-Info "Try opening http://$HOST_NAME in a few seconds"
    Write-Info "Check logs: $LOGS_DIR"
}

Start-Process "http://$HOST_NAME"

# --------------------------------------------------------------
# DONE
# --------------------------------------------------------------

Write-Host ""
Write-Host "  +---------------------------------------------------+" -ForegroundColor Green
Write-Host "  |    Installation Complete!                         |" -ForegroundColor Green
Write-Host "  |                                                   |" -ForegroundColor Green
Write-Host "  |    URL      :  http://lm.ai                       |" -ForegroundColor Green
Write-Host "  |    Username :  SA                                 |" -ForegroundColor Green
Write-Host "  |    Password :  admin123                           |" -ForegroundColor Green
Write-Host "  |                                                   |" -ForegroundColor Green
if ($IS_UPGRADE) {
Write-Host "  |    Mode     :  Upgraded (database preserved)      |" -ForegroundColor Green
} else {
Write-Host "  |    Mode     :  Fresh install                      |" -ForegroundColor Green
}
Write-Host "  |    Service  :  Auto-starts on every boot          |" -ForegroundColor Green
Write-Host "  +---------------------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Host "  The application runs as a Windows service and will" -ForegroundColor Gray
Write-Host "  start automatically whenever your computer boots."   -ForegroundColor Gray
Write-Host ""
Write-Host "  To uninstall, run Uninstall-LeadManager4U.bat"       -ForegroundColor Gray
Write-Host ""

Read-Host "  Press Enter to close"
