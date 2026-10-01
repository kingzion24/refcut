# RefCut setup for Windows. Run from the refcut folder:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "`n== RefCut setup ==" -ForegroundColor Cyan

# 1. Python 3.12 (torch/demucs don't support 3.13+ everywhere yet; Resolve also needs a system Python for scripts)
$py = $null
try { & py -3.12 --version *> $null; if ($LASTEXITCODE -eq 0) { $py = "py -3.12" } } catch {}
if (-not $py) {
  Write-Host "Installing Python 3.12 (winget)..." -ForegroundColor Yellow
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  Refresh-Path
  $py = "py -3.12"
}
Write-Host "Python: $(& py -3.12 --version)"

# 2. ffmpeg
if (-not (Have ffmpeg)) {
  Write-Host "Installing ffmpeg (winget)..." -ForegroundColor Yellow
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  Refresh-Path
}
if (Have ffmpeg) { Write-Host "ffmpeg: OK" } else { Write-Host "ffmpeg installed - open a NEW terminal after setup so it's on PATH." -ForegroundColor Yellow }

# 3. Claude Code
if (Have claude) { Write-Host "Claude Code: OK" }
else {
  Write-Host "Claude Code not found. Install it, then run 'claude' once to log in:" -ForegroundColor Yellow
  Write-Host "    irm https://claude.ai/install.ps1 | iex"
}

# 4. venv + packages
if (-not (Test-Path venv)) { & py -3.12 -m venv venv }
$vpy = ".\venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip
Write-Host "Installing CPU PyTorch (skips multi-GB CUDA download)..." -ForegroundColor Yellow
& $vpy -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
& $vpy -m pip install -r requirements.txt

# 5. Resolve bridge script
$scripts = Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
New-Item -ItemType Directory -Force -Path $scripts | Out-Null
Copy-Item "vendor\davinci-resolve-mcp\src\CursorBridge.py" $scripts -Force
Write-Host "Bridge script installed to: $scripts"

# 6. Also register the MCP with Claude Code so you can drive Resolve from the terminal too
if (Have claude) {
  $server = (Resolve-Path "vendor\davinci-resolve-mcp\src\resolve_mcp_bridge.py").Path
  $pyAbs = (Resolve-Path $vpy).Path
  try { claude mcp remove davinci-resolve -s user *> $null } catch {}
  claude mcp add --scope user davinci-resolve -- "$pyAbs" "$server"
}

# 7. Skills that ship with RefCut -> this machine's Claude (%USERPROFILE%\.claude\skills)
& $vpy skillset.py

# 8. Node.js + the motion B-roll renderer (Playwright + Chromium). Only "Add motion B-roll" needs these.
if (-not (Have node)) {
  Write-Host "Installing Node.js LTS (winget)..." -ForegroundColor Yellow
  winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
  Refresh-Path
}
if (Have node) {
  Write-Host "Installing the motion B-roll renderer (first time: ~150 MB download)..." -ForegroundColor Yellow
  & $vpy -c "import broll; [print('  ' + t) for _, t in broll.ensure_runtime()]; print('Motion B-roll renderer:', broll.runtime_status()['state'])"
  if ($LASTEXITCODE -ne 0) { Write-Host "Renderer install failed - RefCut will retry the first time you click 'Add motion B-roll'." -ForegroundColor Yellow }
} else {
  Write-Host "Node.js installed - open a NEW terminal and run setup again to finish the motion B-roll renderer (everything else is ready)." -ForegroundColor Yellow
}

# 9. Voice model for mascot videos (3.3 GB, once). Skip with:  setup.ps1 -NoVoiceModel
if ($args -notcontains "-NoVoiceModel") {
  Write-Host "Downloading the voice model for mascot videos (3.3 GB, one time)..." -ForegroundColor Yellow
  & $vpy -c "from huggingface_hub import snapshot_download; snapshot_download('k2-fsa/OmniVoice'); print('Voice model ready')"
  if ($LASTEXITCODE -ne 0) { Write-Host "Voice model download failed - RefCut will download it the first time you design a voice." -ForegroundColor Yellow }
}

Write-Host "`nDone. Run start.bat, then in Resolve: Workspace > Scripts > CursorBridge." -ForegroundColor Green
