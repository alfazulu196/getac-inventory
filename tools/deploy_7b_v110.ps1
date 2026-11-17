# deploy_7b_v110.ps1
# Deploys the InventorySurvey field vision model (qwen2.5vl:7b) to this PC
# via the internet: downloads the GGUF weights from HuggingFace, verifies
# SHA-256, builds the model with "ollama create".
#
# Run this ON the Getac V110 while it has internet access.
# Wrapper: deploy_7b_v110.bat. Guide: CHEK_LIST_VYEZD.md, section 1 (variant A).
#
# ASCII-English only on purpose (PowerShell 5.1 + non-UTF console).
#
# Re-runs are safe: finished downloads are resumed, and if the model is
# already installed the script exits with "Nothing to do".
#
# Options:
#   -DownloadDir <path>  where to put downloaded files
#                        (default: %USERPROFILE%\Downloads\ollama_qwen25vl7b)
#   -KeepFiles           keep the downloaded .gguf files after success

param(
    [string]$DownloadDir = "$env:USERPROFILE\Downloads\ollama_qwen25vl7b",
    [switch]$KeepFiles
)

$ErrorActionPreference = "Stop"

$Model      = "qwen2.5vl:7b"
$WeightsUrl = "https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf"
$ProjUrl    = "https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/mmproj-F16.gguf"
$WeightsSha  = "d16776dcd9a28d42758c2958ed3a752aabf20a305252cd64ff2be72b4a78c503"
$ProjSha     = "987dd0733033fb5dd9b124d1ca926ae865572e432384eee7618b2eec3e735e17"
$WeightsSize = 4683072384
$ProjSize    = 1354163040
$SetupUrl   = "https://ollama.com/download/OllamaSetup.exe"
$MinFreeGb  = 6.5

function Info([string]$m) { Write-Host "[deploy] $m" }
function Die([string]$m) {
    Write-Host "[deploy] ERROR: $m" -ForegroundColor Red
    exit 1
}

function Find-Ollama {
    $cands = @()
    $cmd = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($cmd) { $cands += $cmd.Source }
    $cands += "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    $cands += "C:\Program Files\Ollama\ollama.exe"
    foreach ($c in $cands) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    return $null
}

function Test-Server {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/version" `
            -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

function Ensure-Server([string]$oll) {
    if (Test-Server) { return }
    Info "Ollama service is not running - starting it..."
    Start-Process -FilePath $oll -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Server) { Info "Ollama service is up."; return }
    }
    Die "Could not start the Ollama service. Start Ollama manually and re-run."
}

function Get-Sha256([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()
}

function Fetch([string]$url, [string]$outFile, [string]$sha = "", [long]$size = 0) {
    $name = Split-Path $outFile -Leaf
    if ($sha -and (Test-Path -LiteralPath $outFile)) {
        if ((Get-Sha256 $outFile) -eq $sha) {
            Info "Already downloaded and verified: $name"
            return
        }
        $len = (Get-Item -LiteralPath $outFile).Length
        if ($len -eq $size) {
            Info "$name is complete but corrupted - re-downloading from scratch..."
            Remove-Item -LiteralPath $outFile -Force
        } else {
            Info ("Partial download found ({0:N0} of {1:N0} bytes) - resuming..." -f $len, $size)
        }
    }
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) {
        Die "curl.exe not found (it is built into Windows 10 since 1803)."
    }
    $curlArgs = @("-L", "-C", "-",
                  "--retry", "8", "--retry-delay", "20",
                  "--speed-limit", "20000", "--speed-time", "90")
    $ver = ((& $curl.Source --version) -split " ")[1]
    if ($ver -ge "7.71") { $curlArgs += "--retry-all-errors" }
    $curlArgs += @("-o", $outFile, $url)
    Info "Downloading $name (10-40 min depending on the link)..."
    & $curl.Source @curlArgs
    if ($LASTEXITCODE -ne 0) {
        Die ("Download failed (curl exit $LASTEXITCODE). " +
             "Just re-run this script - it will resume where it stopped.")
    }
}

Info "=== InventorySurvey: deploy $Model via the internet ==="

New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
$DownloadDir = (Resolve-Path -LiteralPath $DownloadDir).Path

$oll = Find-Ollama
if (-not $oll) {
    Info "Ollama is not installed. Downloading the installer..."
    $setup = Join-Path $DownloadDir "OllamaSetup.exe"
    Fetch $SetupUrl $setup
    Info "Installer saved: $setup"
    Info "Launching it now. Finish the installation, THEN RE-RUN this script."
    Start-Process -FilePath $setup
    exit 2
}
Info "Ollama found: $oll"

Ensure-Server $oll

Info "Checking installed models..."
$list = (& $oll list) -join "`n"
if ($list -match [regex]::Escape($Model)) {
    Info "Model $Model is already installed. Nothing to do."
    exit 0
}

$driveLetter = $DownloadDir.Substring(0, 1)
$freeGb = [math]::Round((Get-PSDrive -Name $driveLetter).Free / 1GB, 1)
if ($freeGb -lt $MinFreeGb) {
    Die "Only $freeGb GB free on ${driveLetter}: - need about $MinFreeGb GB."
}
Info "Free space on ${driveLetter}: $freeGb GB - OK."

$weights = Join-Path $DownloadDir "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf"
$proj    = Join-Path $DownloadDir "mmproj-F16.gguf"

Fetch $WeightsUrl $weights $WeightsSha $WeightsSize
$sha = Get-Sha256 $weights
if ($sha -ne $WeightsSha) {
    Die ("Checksum mismatch for " + (Split-Path $weights -Leaf) + "." +
         "`n  expected: $WeightsSha" +
         "`n  got:      $sha" +
         "`nDelete the file and re-run this script.")
}
Info "Checksum OK: $(Split-Path $weights -Leaf)"

Fetch $ProjUrl $proj $ProjSha $ProjSize
$sha = Get-Sha256 $proj
if ($sha -ne $ProjSha) {
    Die ("Checksum mismatch for " + (Split-Path $proj -Leaf) + "." +
         "`n  expected: $ProjSha" +
         "`n  got:      $sha" +
         "`nDelete the file and re-run this script.")
}
Info "Checksum OK: $(Split-Path $proj -Leaf)"

$mf = Join-Path $DownloadDir "Modelfile"
@"
FROM $weights
FROM $proj
"@ | Set-Content -LiteralPath $mf -Encoding ASCII

Info "Building the model (hashes ~5.6 GB of files, may take a few minutes)..."
& $oll create $Model -f "$mf"
if ($LASTEXITCODE -ne 0) {
    Die "ollama create failed (exit $LASTEXITCODE). Fix the problem and re-run."
}

$list2 = (& $oll list) -join "`n"
if ($list2 -notmatch [regex]::Escape($Model)) {
    Die "Model $Model is not in 'ollama list' after create."
}

if (-not $KeepFiles) {
    Info "Cleaning up downloaded files (-KeepFiles keeps them)."
    Remove-Item -LiteralPath $weights -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $proj -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $mf -ErrorAction SilentlyContinue
}

Info "DONE. Model $Model is ready."
Info "Verify: tools\check_ollama.bat (must list qwen2.5vl:7b)."
Info "The InventorySurvey app picks the model up automatically on next start."
exit 0
