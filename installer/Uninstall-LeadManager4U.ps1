<#
.SYNOPSIS
    LeadManager4U.ai — Uninstaller
.DESCRIPTION
    Cleanly removes the LeadManager4U Windows service, hosts-file entry,
    desktop shortcut, and optionally the entire install directory.
.NOTES
    Must run as Administrator.
#>

$INSTALL_DIR  = "C:\LeadManager4U"
$SERVICE_NAME = "LeadManager4U"
$HOST_NAME    = "lm.ai"
$NSSM_EXE     = Join-Path $INSTALL_DIR "tools\nssm.exe"

$ErrorActionPreference = "Continue"

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
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Clear-Host
Write-Host ""
Write-Host "  +---------------------------------------------------+" -ForegroundColor Red
Write-Host "  |         LeadManager4U.ai   Uninstaller            |" -ForegroundColor Red
Write-Host "  +---------------------------------------------------+" -ForegroundColor Red
Write-Host ""

if (-not (Test-IsAdmin)) {
    Write-Fail "This script must run as Administrator."
    Write-Host ""; Read-Host "  Press Enter to exit"; exit 1
}

Write-Host "  This will remove the LeadManager4U service and host mapping." -ForegroundColor Yellow
Write-Host ""
$confirm = Read-Host "  Type 'yes' to continue"
if ($confirm -ne "yes") {
    Write-Host "  Cancelled." -ForegroundColor Gray
    Write-Host ""; Read-Host "  Press Enter to exit"; exit 0
}

Write-Step "Step 1/4 - Remove Windows Service"

$svc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -eq "Running") {
        Write-Info "Stopping service..."
        if (Test-Path $NSSM_EXE) {
            & $NSSM_EXE stop $SERVICE_NAME 2>&1 | Out-Null
        } else {
            Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 3
        Write-OK "Service stopped"
    }

    if (Test-Path $NSSM_EXE) {
        & $NSSM_EXE remove $SERVICE_NAME confirm 2>&1 | Out-Null
    } else {
        sc.exe delete $SERVICE_NAME 2>&1 | Out-Null
    }
    Start-Sleep -Seconds 1
    Write-OK "Service '$SERVICE_NAME' removed"
} else {
    $task = Get-ScheduledTask -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $SERVICE_NAME -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $SERVICE_NAME -Confirm:$false
        Write-OK "Scheduled task removed"
    } else {
        Write-Info "No service or scheduled task found"
    }
}

Write-Step "Step 2/4 - Remove Host Mapping"

$hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
if (Test-Path $hostsPath) {
    $lines = Get-Content $hostsPath
    $newLines = $lines | Where-Object {
        $_ -notmatch "^\s*127\.0\.0\.1\s+lm\.ai" -and
        $_ -notmatch "^\s*#\s*LeadManager4U"
    }
    $newLines | Set-Content $hostsPath -Encoding ASCII
    & ipconfig /flushdns 2>&1 | Out-Null
    Write-OK "Removed lm.ai from hosts file"
} else {
    Write-Warn "Hosts file not found"
}

Write-Step "Step 3/4 - Remove Shortcut"

$removed = $false
foreach ($folder in @(
    [Environment]::GetFolderPath("CommonDesktopDirectory"),
    [Environment]::GetFolderPath("Desktop")
)) {
    foreach ($scName in @("LeadManager4U.url", "LeadManager4U.lnk")) {
        $shortcut = Join-Path $folder $scName
        if (Test-Path $shortcut) {
            Remove-Item $shortcut -Force
            Write-OK "Removed $shortcut"
            $removed = $true
        }
    }
}
if (-not $removed) { Write-Info "No desktop shortcut found" }

Write-Step "Step 4/4 - Remove Files"

if (Test-Path $INSTALL_DIR) {
    Write-Host ""
    Write-Host "  The install directory is: $INSTALL_DIR" -ForegroundColor Yellow
    Write-Host "  This contains your database (db.sqlite3) with all lead data." -ForegroundColor Yellow
    Write-Host ""
    $delChoice = Read-Host "  Delete entire directory? (yes / no / keep-db)"

    if ($delChoice -eq "yes") {
        Remove-Item $INSTALL_DIR -Recurse -Force -ErrorAction SilentlyContinue
        Write-OK "Deleted $INSTALL_DIR"
    } elseif ($delChoice -eq "keep-db") {
        $tempDb = Join-Path $env:TEMP "LeadManager4U-db.sqlite3"
        $dbPath = Join-Path $INSTALL_DIR "db.sqlite3"
        if (Test-Path $dbPath) {
            Copy-Item $dbPath $tempDb -Force
            foreach ($ext in @("-wal","-shm")) {
                if (Test-Path "$dbPath$ext") { Copy-Item "$dbPath$ext" "$tempDb$ext" -Force }
            }
        }

        Remove-Item $INSTALL_DIR -Recurse -Force -ErrorAction SilentlyContinue

        $dbSaveDir = Join-Path ([Environment]::GetFolderPath("Desktop")) "LeadManager4U-Backup"
        New-Item -ItemType Directory -Path $dbSaveDir -Force | Out-Null
        if (Test-Path $tempDb) {
            Move-Item $tempDb (Join-Path $dbSaveDir "db.sqlite3") -Force
            foreach ($ext in @("-wal","-shm")) {
                if (Test-Path "$tempDb$ext") { Move-Item "$tempDb$ext" (Join-Path $dbSaveDir "db.sqlite3$ext") -Force }
            }
            Write-OK "Database saved to $dbSaveDir"
        }
        Write-OK "Install directory removed (database preserved)"
    } else {
        Write-Info "Keeping $INSTALL_DIR"
    }
} else {
    Write-Info "Install directory not found"
}

Write-Host ""
Write-Host "  +---------------------------------------------------+" -ForegroundColor Green
Write-Host "  |         Uninstall Complete                        |" -ForegroundColor Green
Write-Host "  +---------------------------------------------------+" -ForegroundColor Green
Write-Host ""

Read-Host "  Press Enter to close"
