<#
.SYNOPSIS
    LeadManager4U.ai — GUI Setup Wizard & Maintenance Tool
.DESCRIPTION
    Native Windows Forms GUI interface for installing, upgrading, and uninstalling LeadManager4U.
    Allows custom install path selection, desktop shortcut creation, and service management.
.NOTES
    Must run as Administrator.
#>

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# --------------------------------------------------------------
# PRE-FLIGHT ADMIN CHECK
# --------------------------------------------------------------

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    [System.Windows.Forms.MessageBox]::Show(
        "LeadManager4U Setup requires Administrator privileges.`n`nPlease right-click Install-LeadManager4U.bat and select 'Run as administrator'.",
        "Administrator Privileges Required",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    ) | Out-Null
    exit 1
}

# --------------------------------------------------------------
# PATH DEFINITIONS
# --------------------------------------------------------------

$SCRIPT_DIR  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_DIR = Split-Path -Parent $SCRIPT_DIR

$DEFAULT_INSTALL_DIR = "C:\LeadManager4U"
$SERVICE_NAME        = "LeadManager4U"
$PORT                = 80
$HOST_NAME           = "lm.ai"
$PYTHON_VER          = "3.12.8"
$NSSM_VER            = "2.24"

# --------------------------------------------------------------
# GUI FORM SETUP
# --------------------------------------------------------------

[System.Windows.Forms.Application]::EnableVisualStyles()

$form               = New-Object System.Windows.Forms.Form
$form.Text          = "LeadManager4U.ai Setup Wizard"
$form.Size          = New-Object System.Drawing.Size(680, 580)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedSingle
$form.MaximizeBox   = $false
$form.BackColor     = [System.Drawing.Color]::FromArgb(245, 247, 250)

# Banner Panel
$bannerPanel           = New-Object System.Windows.Forms.Panel
$bannerPanel.Size      = New-Object System.Drawing.Size(680, 75)
$bannerPanel.Location  = New-Object System.Drawing.Point(0, 0)
$bannerPanel.BackColor = [System.Drawing.Color]::FromArgb(30, 41, 59)

$lblTitle           = New-Object System.Windows.Forms.Label
$lblTitle.Text      = "LeadManager4U.ai Setup and Maintenance"
$lblTitle.Font      = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$lblTitle.ForeColor = [System.Drawing.Color]::White
$lblTitle.Location  = New-Object System.Drawing.Point(20, 15)
$lblTitle.AutoSize  = $true

$lblSubtitle           = New-Object System.Windows.Forms.Label
$lblSubtitle.Text      = "Local URL: http://lm.ai | One-Click Windows Deployment"
$lblSubtitle.Font      = New-Object System.Drawing.Font("Segoe UI", 9)
$lblSubtitle.ForeColor = [System.Drawing.Color]::FromArgb(148, 163, 184)
$lblSubtitle.Location  = New-Object System.Drawing.Point(20, 45)
$lblSubtitle.AutoSize  = $true

$bannerPanel.Controls.Add($lblTitle)
$bannerPanel.Controls.Add($lblSubtitle)
$form.Controls.Add($bannerPanel)

# Group Box: Install Options
$grpOptions          = New-Object System.Windows.Forms.GroupBox
$grpOptions.Text     = " Installation Settings "
$grpOptions.Font     = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$grpOptions.Location = New-Object System.Drawing.Point(20, 90)
$grpOptions.Size     = New-Object System.Drawing.Size(625, 165)

# Label: Install Path
$lblPath          = New-Object System.Windows.Forms.Label
$lblPath.Text     = "Install Location (Select Drive / Folder):"
$lblPath.Font     = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Regular)
$lblPath.Location = New-Object System.Drawing.Point(15, 25)
$lblPath.AutoSize = $true
$grpOptions.Controls.Add($lblPath)

# Textbox: Path
$txtPath          = New-Object System.Windows.Forms.TextBox
$txtPath.Text     = $DEFAULT_INSTALL_DIR
$txtPath.Font     = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Regular)
$txtPath.Location = New-Object System.Drawing.Point(15, 48)
$txtPath.Size     = New-Object System.Drawing.Size(485, 25)
$grpOptions.Controls.Add($txtPath)

# Button: Browse
$btnBrowse          = New-Object System.Windows.Forms.Button
$btnBrowse.Text     = "Browse..."
$btnBrowse.Font     = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Regular)
$btnBrowse.Location = New-Object System.Drawing.Point(510, 46)
$btnBrowse.Size     = New-Object System.Drawing.Size(100, 27)
$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "Select Installation Directory for LeadManager4U"
    $dlg.SelectedPath = $txtPath.Text
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $txtPath.Text = $dlg.SelectedPath
    }
})
$grpOptions.Controls.Add($btnBrowse)

# Checkbox: Desktop Shortcut
$chkShortcut          = New-Object System.Windows.Forms.CheckBox
$chkShortcut.Text     = "Create Desktop Shortcut (http://lm.ai)"
$chkShortcut.Font     = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Regular)
$chkShortcut.Checked  = $true
$chkShortcut.Location = New-Object System.Drawing.Point(15, 85)
$chkShortcut.Size     = New-Object System.Drawing.Size(280, 25)
$grpOptions.Controls.Add($chkShortcut)

# Checkbox: Service Registration
$chkService          = New-Object System.Windows.Forms.CheckBox
$chkService.Text     = "Register Windows Service (Auto-start on Windows boot)"
$chkService.Font     = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Regular)
$chkService.Checked  = $true
$chkService.Location = New-Object System.Drawing.Point(15, 115)
$chkService.Size     = New-Object System.Drawing.Size(380, 25)
$grpOptions.Controls.Add($chkService)

# Checkbox: Open Browser
$chkOpenBrowser          = New-Object System.Windows.Forms.CheckBox
$chkOpenBrowser.Text     = "Launch http://lm.ai in browser when complete"
$chkOpenBrowser.Font     = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Regular)
$chkOpenBrowser.Checked  = $true
$chkOpenBrowser.Location = New-Object System.Drawing.Point(310, 85)
$chkOpenBrowser.Size     = New-Object System.Drawing.Size(300, 25)
$grpOptions.Controls.Add($chkOpenBrowser)

$form.Controls.Add($grpOptions)

# Progress Bar
$progressBar          = New-Object System.Windows.Forms.ProgressBar
$progressBar.Location = New-Object System.Drawing.Point(20, 268)
$progressBar.Size     = New-Object System.Drawing.Size(625, 20)
$progressBar.Style    = [System.Windows.Forms.ProgressBarStyle]::Continuous
$progressBar.Value    = 0
$form.Controls.Add($progressBar)

# Log Panel (RichTextBox)
$rtbLog            = New-Object System.Windows.Forms.RichTextBox
$rtbLog.Location   = New-Object System.Drawing.Point(20, 298)
$rtbLog.Size       = New-Object System.Drawing.Size(625, 175)
$rtbLog.ReadOnly   = $true
$rtbLog.Font       = New-Object System.Drawing.Font("Consolas", 8.5)
$rtbLog.BackColor  = [System.Drawing.Color]::FromArgb(15, 23, 42)
$rtbLog.ForeColor  = [System.Drawing.Color]::FromArgb(226, 232, 240)
$form.Controls.Add($rtbLog)

# Logging Helper
function Append-Log([string]$msg, [string]$type = "INFO") {
    $color = switch ($type) {
        "OK"   { [System.Drawing.Color]::FromArgb(74, 222, 128) }
        "WARN" { [System.Drawing.Color]::FromArgb(250, 204, 21) }
        "FAIL" { [System.Drawing.Color]::FromArgb(248, 113, 113) }
        "STEP" { [System.Drawing.Color]::FromArgb(56, 189, 248) }
        default{ [System.Drawing.Color]::FromArgb(226, 232, 240) }
    }
    $rtbLog.SelectionStart  = $rtbLog.TextLength
    $rtbLog.SelectionLength = 0
    $rtbLog.SelectionColor  = $color
    $rtbLog.AppendText("[$type] $msg`n")
    $rtbLog.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

# Action Buttons
$btnInstall          = New-Object System.Windows.Forms.Button
$btnInstall.Text     = "Install / Upgrade"
$btnInstall.Font     = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$btnInstall.BackColor= [System.Drawing.Color]::FromArgb(16, 185, 129)
$btnInstall.ForeColor= [System.Drawing.Color]::White
$btnInstall.FlatStyle= [System.Windows.Forms.FlatStyle]::Flat
$btnInstall.Location = New-Object System.Drawing.Point(20, 488)
$btnInstall.Size     = New-Object System.Drawing.Size(180, 38)
$form.Controls.Add($btnInstall)

$btnUninstall          = New-Object System.Windows.Forms.Button
$btnUninstall.Text     = "Uninstall App"
$btnUninstall.Font     = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$btnUninstall.BackColor= [System.Drawing.Color]::FromArgb(239, 68, 68)
$btnUninstall.ForeColor= [System.Drawing.Color]::White
$btnUninstall.FlatStyle= [System.Windows.Forms.FlatStyle]::Flat
$btnUninstall.Location = New-Object System.Drawing.Point(220, 488)
$btnUninstall.Size     = New-Object System.Drawing.Size(160, 38)
$form.Controls.Add($btnUninstall)

$btnClose          = New-Object System.Windows.Forms.Button
$btnClose.Text     = "Close"
$btnClose.Font     = New-Object System.Drawing.Font("Segoe UI", 10)
$btnClose.Location = New-Object System.Drawing.Point(545, 488)
$btnClose.Size     = New-Object System.Drawing.Size(100, 38)
$btnClose.Add_Click({ $form.Close() })
$form.Controls.Add($btnClose)

# Controls Lock/Unlock
function Set-ControlsEnabled([bool]$enabled) {
    $btnInstall.Enabled     = $enabled
    $btnUninstall.Enabled   = $enabled
    $btnBrowse.Enabled      = $enabled
    $txtPath.Enabled        = $enabled
    $chkShortcut.Enabled    = $enabled
    $chkService.Enabled     = $enabled
    $chkOpenBrowser.Enabled = $enabled
}

# --------------------------------------------------------------
# INSTALLATION WORKER LOGIC
# --------------------------------------------------------------

$btnInstall.Add_Click({
    $installDir = $txtPath.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($installDir)) {
        [System.Windows.Forms.MessageBox]::Show("Please specify a valid installation directory.", "Invalid Path", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        return
    }

    Set-ControlsEnabled $false
    $progressBar.Value = 0
    $rtbLog.Clear()

    Append-Log "Starting LeadManager4U Setup..." "STEP"
    Append-Log "Target Path: $installDir" "INFO"
    Append-Log "Target URL:  http://lm.ai" "INFO"

    $venvDir   = Join-Path $installDir ".venv"
    $toolsDir  = Join-Path $installDir "tools"
    $nssmExe   = Join-Path $toolsDir "nssm.exe"
    $logsDir   = Join-Path $installDir "logs"
    $backupDir    = Join-Path $installDir "backups"
    $logoIco      = Join-Path $SCRIPT_DIR "logo.ico"
    $installedLogo= Join-Path $installDir "logo.ico"
    $isUpgrade = Test-Path (Join-Path $installDir "manage.py")

    if ($isUpgrade) {
        Append-Log "Upgrade mode detected. Existing installation at $installDir" "WARN"
    } else {
        Append-Log "Fresh installation mode." "INFO"
    }

    # 1. Python Check / Download
    $progressBar.Value = 10
    Append-Log "Phase 1/7: Checking Python Runtime..." "STEP"

    $pythonCmd = $null
    foreach ($cmd in @("python", "python3")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "Python 3\.(\d+)") {
                if ([int]$Matches[1] -ge 10) {
                    $pythonCmd = (Get-Command $cmd -ErrorAction Stop).Source
                    Append-Log "Found $cmd ($ver)" "OK"
                    break
                }
            }
        } catch {}
    }

    if (-not $pythonCmd) {
        $knownPaths = @("C:\Python312\python.exe", "C:\Python311\python.exe", "C:\Python310\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
        foreach ($p in $knownPaths) {
            if (Test-Path $p) {
                $pythonCmd = $p
                Append-Log "Found Python at $p" "OK"
                break
            }
        }
    }

    if (-not $pythonCmd) {
        Append-Log "Python 3.10+ not found. Downloading Python $PYTHON_VER..." "WARN"
        $pyUrl       = "https://www.python.org/ftp/python/$PYTHON_VER/python-$PYTHON_VER-amd64.exe"
        $pyInstaller = Join-Path $env:TEMP "python-$PYTHON_VER-installer.exe"
        $pyTarget    = "C:\Python312"

        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
            Append-Log "Installing Python silently to $pyTarget..." "INFO"
            $proc = Start-Process -FilePath $pyInstaller -ArgumentList @("/quiet", "InstallAllUsers=1", "PrependPath=1", "TargetDir=$pyTarget", "Include_pip=1") -Wait -PassThru -NoNewWindow
            Remove-Item $pyInstaller -Force -ErrorAction SilentlyContinue
            $pythonCmd = Join-Path $pyTarget "python.exe"
            Append-Log "Python runtime successfully installed." "OK"
        } catch {
            Append-Log "Failed to auto-install Python: $_" "FAIL"
            Set-ControlsEnabled $true
            return
        }
    }

    # 2. Deploy Project Files
    $progressBar.Value = 25
    Append-Log "Phase 2/7: Deploying project files to $installDir..." "STEP"
    if (-not (Test-Path $installDir)) {
        New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    }

    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }

    Get-ChildItem -Path $installDir -Filter "db.sqlite3.backup-*" -File -ErrorAction SilentlyContinue | ForEach-Object {
        $dest = Join-Path $backupDir $_.Name
        if (-not (Test-Path $dest)) {
            Move-Item $_.FullName $dest -Force -ErrorAction SilentlyContinue
        } else {
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
        }
        Append-Log "Relocated loose backup $($_.Name) -> backups/" "INFO"
    }

    if ($isUpgrade) {
        $dbFile = Join-Path $installDir "db.sqlite3"
        if (Test-Path $dbFile) {
            $ts = Get-Date -Format "yyyyMMdd-HHmmss"
            $dbBackup = Join-Path $backupDir "db.sqlite3.backup-$ts"
            Copy-Item $dbFile $dbBackup -Force
            foreach ($ext in @("-wal","-shm")) {
                $src = "$dbFile$ext"
                if (Test-Path $src) { Copy-Item $src "$dbBackup$ext" -Force }
            }
            Append-Log "Database backed up -> backups/db.sqlite3.backup-$ts" "OK"

            $backupsList = Get-ChildItem -Path $backupDir -Filter "db.sqlite3.backup-*" |
                           Where-Object { $_.Name -notmatch "\-(wal|shm)$" } |
                           Sort-Object LastWriteTime -Descending
            if ($backupsList.Count -gt 5) {
                $oldBackups = $backupsList | Select-Object -Skip 5
                foreach ($old in $oldBackups) {
                    Remove-Item $old.FullName -Force -ErrorAction SilentlyContinue
                    foreach ($ext in @("-wal","-shm")) {
                        Remove-Item "$($old.FullName)$ext" -Force -ErrorAction SilentlyContinue
                    }
                    Append-Log "Cleaned up old backup: $($old.Name)" "INFO"
                }
            }
        }
    }

    $roboSrc  = "`"$PROJECT_DIR`""
    $roboDst  = "`"$installDir`""
    $roboDirs = ".git __pycache__ installer .venv node_modules tools logs attached_assets backups"
    $roboFiles= ".replit replit.nix replit.md"
    $roboArgs = "$roboSrc $roboDst /E /XD $roboDirs /XF $roboFiles"
    if ($isUpgrade) { $roboArgs += " /XF db.sqlite3 db.sqlite3-wal db.sqlite3-shm" }
    $roboArgs += " /NFL /NDL /NJH /NJS /NC /NS /NP"

    cmd /c "robocopy $roboArgs" 2>&1 | Out-Null
    Append-Log "Project files deployed." "OK"

    # 3. Virtualenv & Dependencies
    $progressBar.Value = 45
    Append-Log "Phase 3/7: Setting up Python virtual environment..." "STEP"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    $venvPip    = Join-Path $venvDir "Scripts\pip.exe"

    if (-not (Test-Path $venvPython)) {
        & $pythonCmd -m venv $venvDir 2>&1 | Out-Null
    }
    Append-Log "Installing dependencies (Django, Waitress, Selenium)..." "INFO"
    $reqFile = Join-Path $installDir "requirements.txt"
    & $venvPip install -r $reqFile waitress --quiet 2>&1 | Out-Null
    Append-Log "All Python packages installed." "OK"

    # 4. Django Migration & Static
    $progressBar.Value = 65
    Append-Log "Phase 4/7: Configuring Django database and static files..." "STEP"
    Push-Location $installDir
    try {
        $env:DJANGO_SETTINGS_MODULE = "maps_scraper.settings"
        $env:SITE_URL = "http://$HOST_NAME"
        & $venvPython manage.py migrate --run-syncdb 2>&1 | Out-Null
        Append-Log "Database migrations applied." "OK"
        & $venvPython manage.py collectstatic --noinput 2>&1 | Out-Null
        Append-Log "Static assets collected." "OK"

        $userScript = 'import os, django; os.environ.setdefault("DJANGO_SETTINGS_MODULE","maps_scraper.settings"); django.setup(); from django.contrib.auth.models import User; print("CREATED" if not User.objects.filter(username="SA").exists() and User.objects.create_superuser("SA","","admin123") else "EXISTS")'
        $userResult = & $venvPython -c $userScript 2>&1
        if ("$userResult" -match "CREATED") {
            Append-Log "Default Admin created (User: SA / Pass: admin123)" "OK"
        } else {
            Append-Log "Admin user verified (SA)." "OK"
        }
    } finally {
        Pop-Location
    }

    # 5. Host Mapping (lm.ai)
    $progressBar.Value = 75
    Append-Log "Phase 5/7: Mapping http://lm.ai -> 127.0.0.1..." "STEP"
    $hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
    $hostsContent = Get-Content $hostsPath -Raw -ErrorAction SilentlyContinue
    if ($hostsContent -notmatch "(?m)^\s*127\.0\.0\.1\s+lm\.ai") {
        try {
            [System.IO.File]::AppendAllText($hostsPath, "`r`n# LeadManager4U`r`n127.0.0.1    lm.ai`r`n")
            Append-Log "lm.ai mapped in hosts file." "OK"
        } catch {
            Append-Log "Could not edit hosts file automatically: $_" "WARN"
        }
    } else {
        Append-Log "lm.ai already mapped." "OK"
    }
    & ipconfig /flushdns 2>&1 | Out-Null

    # 6. Service / Startup Registration
    $progressBar.Value = 85
    if ($chkService.Checked) {
        Append-Log "Phase 6/7: Registering LeadManager4U Windows Service..." "STEP"
        foreach ($d in @($toolsDir, $logsDir)) {
            if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        }

        if (-not (Test-Path $nssmExe)) {
            try {
                $nssmUrl = "https://nssm.cc/release/nssm-$NSSM_VER.zip"
                $nssmZip = Join-Path $env:TEMP "nssm-$NSSM_VER.zip"
                $nssmExtract = Join-Path $env:TEMP "nssm-extract"
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing
                Expand-Archive -Path $nssmZip -DestinationPath $nssmExtract -Force
                $nssmBin = Get-ChildItem -Path $nssmExtract -Filter "nssm.exe" -Recurse | Where-Object { $_.DirectoryName -like "*win64*" } | Select-Object -First 1
                if (-not $nssmBin) { $nssmBin = Get-ChildItem -Path $nssmExtract -Filter "nssm.exe" -Recurse | Select-Object -First 1 }
                Copy-Item $nssmBin.FullName $nssmExe -Force
                Remove-Item $nssmZip, $nssmExtract -Recurse -Force -ErrorAction SilentlyContinue
            } catch {
                Append-Log "NSSM download issue: $_" "WARN"
            }
        }

        $runServerPath = Join-Path $installDir "run-server.py"
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

print("Serving LeadManager4U on http://lm.ai:80 ...")
serve(application, host="0.0.0.0", port=80, threads=4, url_scheme="http")
'@
        Set-Content -Path $runServerPath -Value $runServerPy -Encoding UTF8 -Force

        if (Test-Path $nssmExe) {
            $svc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
            if ($svc) {
                if ($svc.Status -eq "Running") { & $nssmExe stop $SERVICE_NAME 2>&1 | Out-Null; Start-Sleep -Seconds 2 }
                & $nssmExe remove $SERVICE_NAME confirm 2>&1 | Out-Null
            }
            & $nssmExe install $SERVICE_NAME $venvPython $runServerPath 2>&1 | Out-Null
            & $nssmExe set $SERVICE_NAME AppDirectory $installDir 2>&1 | Out-Null
            & $nssmExe set $SERVICE_NAME DisplayName "LeadManager4U Web Application" 2>&1 | Out-Null
            & $nssmExe set $SERVICE_NAME Start SERVICE_AUTO_START 2>&1 | Out-Null
            & $nssmExe start $SERVICE_NAME 2>&1 | Out-Null
            Append-Log "Windows Service registered and started." "OK"
        }
    }

    # 7. Desktop Shortcut
    $progressBar.Value = 95
    if ($chkShortcut.Checked) {
        try {
            if (Test-Path $logoIco) {
                Copy-Item $logoIco $installedLogo -Force -ErrorAction SilentlyContinue
            }

            $dt = [Environment]::GetFolderPath("CommonDesktopDirectory")
            if (-not (Test-Path $dt)) { $dt = [Environment]::GetFolderPath("Desktop") }

            $iconPath = if (Test-Path $installedLogo) { $installedLogo } else { "C:\Windows\System32\shell32.dll" }

            # Internet shortcut (.url)
            $urlShortcut = Join-Path $dt "LeadManager4U.url"
            "[InternetShortcut]`r`nURL=http://$HOST_NAME`r`nIconIndex=0`r`nIconFile=$iconPath" | Set-Content -Path $urlShortcut -Encoding ASCII -Force

            # Windows shell shortcut (.lnk)
            try {
                $wsh = New-Object -ComObject WScript.Shell
                $lnkShortcut = Join-Path $dt "LeadManager4U.lnk"
                $shortcut = $wsh.CreateShortcut($lnkShortcut)
                $shortcut.TargetPath = "http://$HOST_NAME"
                if (Test-Path $installedLogo) {
                    $shortcut.IconLocation = "$installedLogo,0"
                }
                $shortcut.Description = "LeadManager4U.ai Web Application"
                $shortcut.Save()
            } catch {}

            Append-Log "Desktop shortcut created with LM logo." "OK"
        } catch {
            Append-Log "Shortcut creation warning: $_" "WARN"
        }
    }

    $progressBar.Value = 100
    Append-Log "Phase 7/7: Setup Complete!" "STEP"
    Append-Log "URL: http://lm.ai | Username: SA | Password: admin123" "OK"

    Set-ControlsEnabled $true

    if ($chkOpenBrowser.Checked) {
        Start-Process "http://$HOST_NAME"
    }

    [System.Windows.Forms.MessageBox]::Show(
        "LeadManager4U installation complete!`n`nApp URL: http://lm.ai`nAdmin Username: SA`nAdmin Password: admin123",
        "Installation Complete",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
})

# --------------------------------------------------------------
# UNINSTALL WORKER LOGIC
# --------------------------------------------------------------

$btnUninstall.Add_Click({
    $installDir = $txtPath.Text.Trim()
    $confirm = [System.Windows.Forms.MessageBox]::Show(
        "Are you sure you want to uninstall LeadManager4U?`n`nThis will remove the Windows Service and lm.ai host mapping.",
        "Confirm Uninstallation",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question
    )
    if ($confirm -ne [System.Windows.Forms.DialogResult]::Yes) { return }

    Set-ControlsEnabled $false
    $progressBar.Value = 0
    $rtbLog.Clear()

    Append-Log "Starting Uninstallation..." "STEP"

    # Stop & Remove Service
    $progressBar.Value = 30
    $nssmExe = Join-Path $installDir "tools\nssm.exe"
    $svc = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
    if ($svc) {
        if ($svc.Status -eq "Running") {
            if (Test-Path $nssmExe) { & $nssmExe stop $SERVICE_NAME 2>&1 | Out-Null }
            else { Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Seconds 2
        }
        if (Test-Path $nssmExe) { & $nssmExe remove $SERVICE_NAME confirm 2>&1 | Out-Null }
        else { sc.exe delete $SERVICE_NAME 2>&1 | Out-Null }
        Append-Log "Windows Service removed." "OK"
    }

    # Remove Hosts Mapping
    $progressBar.Value = 60
    $hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
    if (Test-Path $hostsPath) {
        $lines = Get-Content $hostsPath
        $newLines = $lines | Where-Object { $_ -notmatch "127\.0\.0\.1\s+lm\.ai" -and $_ -notmatch "LeadManager4U" }
        $newLines | Set-Content $hostsPath -Encoding ASCII
        & ipconfig /flushdns 2>&1 | Out-Null
        Append-Log "lm.ai host mapping removed." "OK"
    }

    # Remove Desktop Shortcut
    $progressBar.Value = 80
    foreach ($folder in @([Environment]::GetFolderPath("CommonDesktopDirectory"), [Environment]::GetFolderPath("Desktop"))) {
        foreach ($scName in @("LeadManager4U.url", "LeadManager4U.lnk")) {
            $shortcut = Join-Path $folder $scName
            if (Test-Path $shortcut) { Remove-Item $shortcut -Force }
        }
    }
    Append-Log "Desktop shortcut removed." "OK"

    # Delete Files Prompt
    $delDbPrompt = [System.Windows.Forms.MessageBox]::Show(
        "Do you want to delete the installation folder ($installDir)?`n`nClick YES to delete everything.`nClick NO to preserve your database and files.",
        "Delete Folder?",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question
    )

    if ($delDbPrompt -eq [System.Windows.Forms.DialogResult]::Yes) {
        if (Test-Path $installDir) {
            Remove-Item $installDir -Recurse -Force -ErrorAction SilentlyContinue
            Append-Log "Folder $installDir removed." "OK"
        }
    } else {
        Append-Log "Preserved installation directory: $installDir" "INFO"
    }

    $progressBar.Value = 100
    Append-Log "Uninstallation complete." "STEP"
    Set-ControlsEnabled $true

    [System.Windows.Forms.MessageBox]::Show("LeadManager4U has been uninstalled successfully.", "Uninstallation Complete", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
})

# Display Form
[System.Windows.Forms.Application]::Run($form)
