# Build DiscordAlert with settings that reduce Windows Defender / browser
# false positives:
#   - NO UPX packing (UPX is a major AV trigger)
#   - Windows version resource (Company / Product / FileDescription)
#   - onedir folder + ZIP for distribution (far fewer false positives than onefile)
#   - optional onefile also built without UPX for convenience

$ErrorActionPreference = "Stop"
$Python = "C:\Users\Byteboom\AppData\Local\Programs\Python\Python312\python.exe"
$Root = "c:\Users\Byteboom\Desktop\discord alert"
$Src = Join-Path $Root "auto.py"
$Version = Join-Path $Root "version_info.txt"
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"

# Stop running copy so files aren't locked
Get-Process DiscordAlert -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

Write-Host "== Building onedir (recommended download) ==" -ForegroundColor Cyan
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --noupx `
    --name "DiscordAlert" `
    --version-file $Version `
    --distpath $Dist `
    --workpath $Build `
    --specpath $Root `
    --hidden-import winsdk `
    --hidden-import winsdk.windows.ui.notifications `
    --hidden-import winsdk.windows.ui.notifications.management `
    --hidden-import winsdk.windows.applicationmodel `
    --hidden-import winsdk.windows.foundation `
    --hidden-import winsdk.windows.foundation.collections `
    --collect-all winsdk `
    $Src

# Zip the folder for GitHub / friends (browsers flag raw .exe downloads harder)
$ZipPath = Join-Path $Dist "DiscordAlert-Windows.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $Dist "DiscordAlert\*") -DestinationPath $ZipPath -Force
Write-Host "Created $ZipPath" -ForegroundColor Green

Write-Host "== Building onefile (no UPX) ==" -ForegroundColor Cyan
& $Python -m PyInstaller `
    --noconfirm `
    --onefile `
    --noconsole `
    --noupx `
    --name "DiscordAlert" `
    --version-file $Version `
    --distpath $Dist `
    --workpath $Build `
    --specpath $Root `
    --hidden-import winsdk `
    --hidden-import winsdk.windows.ui.notifications `
    --hidden-import winsdk.windows.ui.notifications.management `
    --hidden-import winsdk.windows.applicationmodel `
    --hidden-import winsdk.windows.foundation `
    --hidden-import winsdk.windows.foundation.collections `
    --collect-all winsdk `
    $Src

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  ZIP (share this):  $ZipPath"
Write-Host "  EXE (local use):   $(Join-Path $Dist 'DiscordAlert.exe')"
