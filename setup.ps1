# One-time setup of the Smart Traffic Challan System on a new Windows machine.
# Run from this folder:   powershell -ExecutionPolicy Bypass -File setup.ps1
# Options:  -Cpu          force the CPU build of PyTorch even if an NVIDIA GPU is present
#           -SkipOllama   do not download the Mistral model (system still works, see README.md)
param([switch]$Cpu, [switch]$SkipOllama)

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

# ---- 1. Python 3.11 / 3.12 ----
Step "1/6  Looking for Python 3.11 or 3.12"
$py = $null
foreach ($cand in @(@("py", "-3.11"), @("py", "-3.12"), @("python"))) {
    if (-not (Get-Command $cand[0] -ErrorAction SilentlyContinue)) { continue }
    $extra = @($cand | Select-Object -Skip 1)
    try { $v = & $cand[0] @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { $v = $null }
    if ($LASTEXITCODE -eq 0 -and ($v -eq "3.11" -or $v -eq "3.12")) { $py = $cand; break }
}
if (-not $py) {
    throw ("Python 3.11 (or 3.12) not found. Newer versions (3.13+) are not supported by PyTorch 2.5.1 but can stay installed. " +
           "Install 3.11 next to them with:  py install 3.11   or from https://www.python.org/downloads/release/python-3119/ , then open a new PowerShell and run this script again.")
}
Write-Host "Using: $($py -join ' ')  (Python $v)"

# ---- 2. Virtual environment ----
Step "2/6  Creating virtual environment in .venv"
if (Test-Path $vpy) {
    try { $vv = & $vpy -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { $vv = $null }
    if ($vv -ne "3.11" -and $vv -ne "3.12") {
        Write-Host "Existing .venv uses Python $vv (unsupported) - recreating it with $v" -ForegroundColor Yellow
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
$index = if ($gpu) { "https://download.pytorch.org/whl/cu121" } else { "https://download.pytorch.org/whl/cpu" }
Step ("3/6  Installing PyTorch 2.5.1 (" + $(if ($gpu) { "NVIDIA CUDA 12.1" } else { "CPU only" }) + ") - large download, please wait")
Run $vpy @("-m", "pip", "install", "torch==2.5.1", "torchvision==0.20.1", "--index-url", $index)

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
