param(
    [string]$LlamaServerPath = "P:\tools\llama-b8639-cuda131\llama-server.exe",
    # Main (general-purpose) model — used for jclaw-main alias
    [string]$ModelPath = "P:\Qwen_Qwen3.5-2B-bf16.gguf",
    # Worker (coding) model — used for jclaw-worker alias. Leave empty to share MainModel.
    [string]$WorkerModelPath = "P:\omnicoder-9b-q8_0.gguf",
    [string]$BindHost = "127.0.0.1",
    [int]$LlamaPort = 5002,
    # Second llama-server port for the worker/coding model (only used when WorkerModelPath is set)
    [int]$LlamaWorkerPort = 5003,
    [string]$MainAlias = "jclaw-main",
    [string]$WorkerAlias = "jclaw-worker",
    [switch]$DisableThinking = $true,
    [switch]$StrictAssert = $true,
    [string]$ExpectedResponse = "JCLAW CUDA 13.1 OK",
    [switch]$StartJClaw,
    [switch]$Stop,
    [int]$LlamaContext = 8192,
    [int]$LlamaGpuLayers = 999,
    [int]$LlamaWaitSeconds = 120
)

$ErrorActionPreference = 'Stop'

function Wait-HttpReady {
    param(
        [string]$Url,
        [hashtable]$Headers,
        [int]$TimeoutSeconds,
        [string]$Name
    )

    $max = [Math]::Max(1, $TimeoutSeconds)
    for ($i = 0; $i -lt $max; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers $Headers -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return $response
            }
        }
        catch {
            # retry until timeout
        }
        Start-Sleep -Seconds 1
    }

    throw "$Name did not become ready: $Url"
}

function Stop-ByPort {
    param([int]$Port)

    $listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if (-not $listeners) {
        return
    }

    $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "Stopped process on port ${Port}: PID ${procId}"
        }
        catch {
            Write-Warning "Failed to stop PID ${procId} on port ${Port}: $($_.Exception.Message)"
        }
    }
}

function Ensure-File {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path $Path)) {
        throw "$Label not found: $Path"
    }
}

function Read-Tail {
    param(
        [string]$Path,
        [int]$Lines = 40
    )
    if (-not (Test-Path $Path)) {
        return ""
    }
    return (Get-Content -Path $Path -Tail $Lines -ErrorAction SilentlyContinue | Out-String)
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$logsDir = Join-Path $repoRoot "logs\e2e-local"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$llamaLog = Join-Path $logsDir "llama-server.log"
$llamaErrLog = Join-Path $logsDir "llama-server.err.log"

if ($Stop) {
    Stop-ByPort -Port $LlamaPort
    if ($WorkerModelPath) { Stop-ByPort -Port $LlamaWorkerPort }
    Write-Host "Stopped local E2E services (ports ${LlamaPort}$(if ($WorkerModelPath) { ', ' + $LlamaWorkerPort }))."
    exit 0
}

Ensure-File -Path $LlamaServerPath -Label "llama-server.exe"
Ensure-File -Path $ModelPath -Label "Main model"
if ($WorkerModelPath) { Ensure-File -Path $WorkerModelPath -Label "Worker model" }

$dualModel = ($WorkerModelPath -and ($WorkerModelPath -ne $ModelPath))

Write-Host "[1/5] Cleaning stale listeners"
Stop-ByPort -Port $LlamaPort
if ($dualModel) { Stop-ByPort -Port $LlamaWorkerPort }

Write-Host "[2/5] Starting llama-server (main: $(Split-Path $ModelPath -Leaf))"
$llamaArgs = @(
    "--model", $ModelPath,
    "--host", $BindHost,
    "--port", "$LlamaPort",
    "--ctx-size", "$LlamaContext",
    "--n-gpu-layers", "$LlamaGpuLayers"
)
Start-Process -FilePath $LlamaServerPath -ArgumentList $llamaArgs -RedirectStandardOutput $llamaLog -RedirectStandardError $llamaErrLog -WindowStyle Hidden | Out-Null
$llamaModelsUrl = "http://${BindHost}:${LlamaPort}/v1/models"
$llamaReady = Wait-HttpReady -Url $llamaModelsUrl -Headers @{} -TimeoutSeconds $LlamaWaitSeconds -Name "llama-server"
Write-Host "llama-server (main) ready: $($llamaReady.StatusCode)"

$workerApiBase = $null
if ($dualModel) {
    $llamaWorkerLog    = Join-Path $logsDir "llama-server-worker.log"
    $llamaWorkerErrLog = Join-Path $logsDir "llama-server-worker.err.log"
    Write-Host "[2b] Starting llama-server (worker: $(Split-Path $WorkerModelPath -Leaf))"
    $llamaWorkerArgs = @(
        "--model", $WorkerModelPath,
        "--host", $BindHost,
        "--port", "$LlamaWorkerPort",
        "--ctx-size", "$LlamaContext",
        "--n-gpu-layers", "$LlamaGpuLayers"
    )
    Start-Process -FilePath $LlamaServerPath -ArgumentList $llamaWorkerArgs -RedirectStandardOutput $llamaWorkerLog -RedirectStandardError $llamaWorkerErrLog -WindowStyle Hidden | Out-Null
    $llamaWorkerModelsUrl = "http://${BindHost}:${LlamaWorkerPort}/v1/models"
    $llamaWorkerReady = Wait-HttpReady -Url $llamaWorkerModelsUrl -Headers @{} -TimeoutSeconds $LlamaWaitSeconds -Name "llama-server-worker"
    Write-Host "llama-server (worker) ready: $($llamaWorkerReady.StatusCode)"
    $workerApiBase = "http://${BindHost}:${LlamaWorkerPort}/v1"
}

Write-Host "[3/5] Writing model alias config (JCLAW_MODEL_ALIASES)"
$mainModelFile   = [System.IO.Path]::GetFileName($ModelPath)
$workerModelFile = if ($dualModel) { [System.IO.Path]::GetFileName($WorkerModelPath) } else { $mainModelFile }
$workerPort = if ($dualModel) { $LlamaWorkerPort } else { $LlamaPort }

# Build alias map as a hashtable then serialize to compact JSON
$mainEntry   = '{"url":"http://' + $BindHost + ':' + $LlamaPort + '/v1","model":"' + $mainModelFile   + '","key":"dummy-key"}'
$workerEntry = '{"url":"http://' + $BindHost + ':' + $workerPort + '/v1","model":"' + $workerModelFile + '","key":"dummy-key"}'
$aliasJson   = '{"' + $MainAlias + '":' + $mainEntry + ',"' + $WorkerAlias + '":' + $workerEntry + '}'

# Persist to .env so the credential proxy picks it up on next start
$envFile = Join-Path $repoRoot ".env"
if (Test-Path $envFile) {
    $envLines = Get-Content $envFile | Where-Object { $_ -notmatch '^JCLAW_MODEL_ALIASES=' }
    ($envLines + "JCLAW_MODEL_ALIASES=$aliasJson") | Set-Content $envFile -Encoding UTF8
}
# Also inject into current process so a running credential proxy inherits it
$Env:JCLAW_MODEL_ALIASES = $aliasJson
Write-Host "JCLAW_MODEL_ALIASES=$aliasJson"

Write-Host "[4/5] Running smoke test (direct to llama-server)"
if ($DisableThinking) {
    $prompt = "/no_think`nReply with exactly: $ExpectedResponse"
} else {
    $prompt = "Reply with exactly: $ExpectedResponse"
}

$body = @{
    model       = $mainModelFile
    prompt      = $prompt
    temperature = 0
    max_tokens  = 64
} | ConvertTo-Json -Depth 5

$completion = Invoke-RestMethod -Method Post -Uri "http://${BindHost}:${LlamaPort}/v1/completions" -Headers @{"Content-Type" = "application/json"} -Body $body -TimeoutSec 240
$text = $completion.choices[0].text
Write-Host "finish_reason=$($completion.choices[0].finish_reason)"
Write-Host "text_preview=$($text.Substring(0, [Math]::Min(240, $text.Length)))"

if ($StrictAssert) {
    $normalized = ($text -replace "\r", "")
    $ok = $false
    if ($normalized -match [Regex]::Escape($ExpectedResponse)) {
        $ok = $true
    }

    if (-not $ok) {
        throw "Smoke test assertion failed: expected phrase '$ExpectedResponse' was not found in model output."
    }

    Write-Host "smoke_assert=pass"
}

# Resolve python: prefer explicit env var, then `python` on PATH, then `python3`
$pythonBin = if ($env:JCLAW_PYTHON) { $env:JCLAW_PYTHON }
             elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" }
             elseif (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" }
             else { throw "Python not found. Set JCLAW_PYTHON env var or add python to PATH." }

if ($StartJClaw) {
    Write-Host "[5/5] Starting J Claw orchestrator (python=$pythonBin)"
    Start-Process -FilePath $pythonBin -ArgumentList @("-m", "src.main", "run", "--allow-no-channels") -WorkingDirectory $repoRoot | Out-Null
    Write-Host "J Claw started (allow-no-channels)."
}
else {
    Write-Host "[5/5] Done. J Claw runtime not started (use -StartJClaw to launch)."
}

Write-Host ""
Write-Host "Logs:"
Write-Host "  llama-server (main): $llamaLog"
Write-Host "  llama-server (main) err: $llamaErrLog"
if ($dualModel) {
    Write-Host "  llama-server (worker): $llamaWorkerLog"
    Write-Host "  llama-server (worker) err: $llamaWorkerErrLog"
}
Write-Host ""
Write-Host "Stop everything with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/run-local-e2e.ps1 -Stop"
