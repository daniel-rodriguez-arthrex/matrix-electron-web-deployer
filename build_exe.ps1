# Build the Matrix Electron Web Deployer as a one-file executable
# Run from the project directory

$ErrorActionPreference = "Stop"

# Ensure .env exists (copy example if missing)
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Write-Host "Creating .env from .env.example"
    Copy-Item ".env.example" ".env"
}

$ExeName = "Matrix Electron Web Deployer.exe"
$DistDir = "$PSScriptRoot"

Write-Host "Building executable..."

# Clean up any previous build artifacts
if (Test-Path "$PSScriptRoot\dist") {
    Remove-Item "$PSScriptRoot\dist" -Recurse -Force
}
if (Test-Path "$PSScriptRoot\build") {
    Remove-Item "$PSScriptRoot\build" -Recurse -Force
}

python -m PyInstaller `
    --onefile `
    --noconfirm `
    --clean `
    --windowed `
    --name "Matrix Electron Web Deployer" `
    --distpath "$DistDir" `
    --workpath "$PSScriptRoot\build" `
    --specpath "$PSScriptRoot" `
    --hidden-import paramiko `
    --hidden-import cryptography `
    --hidden-import bcrypt `
    --hidden-import nacl `
    --hidden-import nacl.bindings `
    --collect-all paramiko `
    "$PSScriptRoot\upgrade_or_gui.py"

$ExePath = "$DistDir\$ExeName"
if (Test-Path $ExePath) {
    Write-Host "Executable built: $ExePath"
} else {
    throw "Build failed: $ExePath not found"
}

# Create desktop shortcut
$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [Environment]::GetFolderPath('Desktop')
$Shortcut = $WshShell.CreateShortcut("$DesktopPath\Matrix Electron Web Deployer.lnk")
$Shortcut.TargetPath = $ExePath
$Shortcut.WorkingDirectory = $PSScriptRoot
$Shortcut.IconLocation = $ExePath
$Shortcut.Save()

Write-Host "Desktop shortcut created: $DesktopPath\Matrix Electron Web Deployer.lnk"
