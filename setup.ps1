# One-time setup of the Smart Traffic Challan System on a new Windows machine.
# Run from this folder:   powershell -ExecutionPolicy Bypass -File setup.ps1
# Options:  -Cpu          force the CPU build of PyTorch even if an NVIDIA GPU is present
#           -SkipOllama   do not download the Mistral model (system still works, see README.md)
#           -Python 3.14  use this Python version (otherwise the first of 3.11, 3.12, 3.14, 3.13 found)
param([switch]$Cpu, [switch]$SkipOllama, [string]$Python = "")

# "Continue": in Windows PowerShell 5.1 harmless stderr output of native programs (pip notices,
# warnings) would otherwise abort the script. Failures are detected through exit codes in Run.
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$app  = Join-Path $root "smart_traffic"
$venv = Join-Path $root ".venv"
$vpy  = Join-Path $venv "Scripts\python.exe"

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Run($exe, [string[]]$a) {
    & $exe @a
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $exe $($a -join ' ')" }
}

# ---- 1. Python 3.11 - 3.14 ----
# 3.11/3.12 use the originally tested PyTorch 2.5.1; 3.13/3.14 use PyTorch 2.14.1 (same results, tested).
# 3.15+ is not supported yet: scikit-image / pyclipper (needed by EasyOCR) have no Windows builds for it.
$supported = @("3.11", "3.12", "3.13", "3.14")
Step "1/6  Looking for Python 3.11 - 3.14"
$py = $null; $found = @()
$cands = @(@("py", "-3.11"), @("py", "-3.12"), @("py", "-3.14"), @("py", "-3.13"), @("python"), @("python3"))
if ($Python) { $cands = @(, @("py", "-$Python")) }   # assigned directly: an if-expression would flatten the list
foreach ($cand in $cands) {
    if (-not (Get-Command $cand[0] -ErrorAction SilentlyContinue)) { continue }
    $extra = @($cand | Select-Object -Skip 1)
    try { $v = & $cand[0] @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { $v = $null }
    if ($LASTEXITCODE -ne 0 -or -not $v) { continue }
    $found += $v
    if ($supported -contains $v) { $py = $cand; break }
}
if (-not $py) {
    $have = if ($found) { "Found Python " + (($found | Select-Object -Unique) -join ", ") + ", which is not supported. " } else { "No Python found. " }
    throw ($have + "Install Python 3.14 (it can stay next to 3.15): https://www.python.org/downloads/ -> latest 3.14.x 'Windows installer (64-bit)', " +
           "or run:  py install 3.14   Then open a NEW PowerShell window and run this script again.")
}
Write-Host "Using: $($py -join ' ')  (Python $v)"

# ---- 2. Virtual environment ----
Step "2/6  Creating virtual environment in .venv"
if (Test-Path $vpy) {
    try { $vv = & $vpy -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { $vv = $null }
    if ($vv -ne $v) {
        Write-Host "Existing .venv uses Python $vv - recreating it with Python $v" -ForegroundColor Yellow
        Remove-Item $venv -Recurse -Force
    }
}
if (-not (Test-Path $vpy)) {
    $extra = @($py | Select-Object -Skip 1)
    Run $py[0] ($extra + @("-m", "venv", $venv))
} else { Write-Host ".venv already exists - reusing it" }
Run $vpy @("-m", "pip", "install", "--upgrade", "pip", "--quiet")

# ---- 3. PyTorch (GPU or CPU) ----
$gpu = $false
if (-not $Cpu -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    try { & nvidia-smi -L 2>$null | Out-Null; $gpu = ($LASTEXITCODE -eq 0) } catch { $gpu = $false }
}
if ($v -eq "3.11" -or $v -eq "3.12") { $tv = "2.5.1"; $tvv = "0.20.1"; $cu = "cu121"; $cuName = "CUDA 12.1" }
else                                  { $tv = "2.14.1"; $tvv = "0.29.1"; $cu = "cu126"; $cuName = "CUDA 12.6" }
$index = if ($gpu) { "https://download.pytorch.org/whl/$cu" } else { "https://download.pytorch.org/whl/cpu" }
Step ("3/6  Installing PyTorch $tv (" + $(if ($gpu) { "NVIDIA $cuName" } else { "CPU only" }) + ") - large download (~2.5 GB with GPU), please wait")
Run $vpy @("-m", "pip", "install", "torch==$tv", "torchvision==$tvv", "--index-url", $index)

# ---- 4. Other packages ----
Step "4/6  Installing project packages (requirements.txt)"
Run $vpy @("-m", "pip", "install", "-r", (Join-Path $app "requirements.txt"))

# ---- 5. Config + model downloads ----
Step "5/6  Configuration and model files"
$envFile = Join-Path $app ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $app ".env.example") $envFile
    Write-Host "Created smart_traffic\.env from .env.example (SMS runs in DRY-RUN until you add Twilio keys)."
} else { Write-Host "smart_traffic\.env already present - kept as is." }
Write-Host "Downloading EasyOCR text models (about 100 MB, first time only)..."
Run $vpy @("-c", "import easyocr; easyocr.Reader(['en'], gpu=False, verbose=False); print('EasyOCR models ready')")

if ($SkipOllama) {
    Write-Host "Skipping Ollama / Mistral (-SkipOllama)."
} elseif (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Host "Pulling Mistral 7B for Ollama (about 4.4 GB, first time only)..."
    try { Invoke-WebRequest http://127.0.0.1:11434 -UseBasicParsing -TimeoutSec 3 | Out-Null }
    catch { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep 5 }
    Run "ollama" @("pull", "mistral")
} else {
    Write-Host "Ollama is not installed. Optional: install it from https://ollama.com/download, then run:  ollama pull mistral" -ForegroundColor Yellow
    Write-Host "Without it the system still works (rule-based plate correction + template SMS text)." -ForegroundColor Yellow
}
if (-not (Test-Path (Join-Path $app "models\best.pt"))) { throw "models\best.pt is missing - copy the trained weights into smart_traffic\models\" }

# ---- 6. Self-test ----
Step "6/6  Running unit tests"
Push-Location $app
try { Run $vpy @("-m", "pytest", "tests", "-q") } finally { Pop-Location }
$dev = & $vpy -c "import torch; print('GPU: ' + torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU mode (no CUDA GPU)')"
Write-Host "`nSetup complete. $dev" -ForegroundColor Green
Write-Host "Start the system with:  run_dashboard.bat   (then open http://localhost:8000)" -ForegroundColor Green
