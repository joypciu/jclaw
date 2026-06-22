#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Test all container skills against the local model stack.

.DESCRIPTION
    Requires the local model stack to already be running:
        .\scripts\run-local-e2e.ps1 -ModelPath ... -WorkerModelPath ...
    (Those llama-server + LiteLLM processes stay alive between runs.)

    This script:
    1. Starts the credential proxy (port 3001) pointing to LiteLLM
    2. Sets up a disposable test group directory
    3. For each container skill, runs the agent container with a minimal
       probe prompt and checks the output is non-empty / non-error
    4. Prints a PASS/FAIL matrix
    5. Stops the credential proxy

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\test-container-skills.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\test-container-skills.ps1 -Verbose
#>
param (
    [string]$LiteLLMProxyUrl = "http://127.0.0.1:4000",
    [string]$LiteLLMApiKey   = "dummy-key",
    [string]$MainModel        = "jclaw-main",
    [string]$WorkerModel      = "jclaw-worker",
    [string]$ContainerImage   = "nanoclaw-agent:latest",
    [int]$CredProxyPort       = 3001,
    [int]$ContainerTimeoutSec = 120,
    [string]$JoyEnv           = "joy",
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$repoRoot  = Split-Path -Parent $PSScriptRoot
$logsDir   = Join-Path $repoRoot "logs\e2e-local"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# ── helpers ─────────────────────────────────────────────────────────────────

function Stop-ByPort {
    param([int]$Port)
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if (-not $listeners) { return }
    $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        try { Stop-Process -Id $procId -Force -ErrorAction Stop; Write-Host "Stopped PID $procId on :$Port" }
        catch { $msg = $_.Exception.Message; Write-Warning "Could not stop PID ${procId}: $msg" }
    }
}

function Wait-HttpReady {
    param([string]$Url, [hashtable]$Headers = @{}, [int]$TimeoutSec = 30, [string]$Name)
    for ($i = 0; $i -lt $TimeoutSec; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers $Headers -TimeoutSec 2
            if ($r.StatusCode -lt 300) { return $true }
        } catch {}
        Start-Sleep -Seconds 1
    }
    return $false
}

if ($Stop) {
    Stop-ByPort -Port $CredProxyPort
    Write-Host "Credential proxy stopped."
    exit 0
}

# ── verify litellm reachable ─────────────────────────────────────────────────
Write-Host "`n[pre-check] Verifying LiteLLM proxy at $LiteLLMProxyUrl"
$proxReady = Wait-HttpReady -Url "$LiteLLMProxyUrl/v1/models" `
    -Headers @{ "Authorization" = "Bearer $LiteLLMApiKey" } `
    -TimeoutSec 10 -Name "LiteLLM"
if (-not $proxReady) {
    throw "LiteLLM proxy not reachable at $LiteLLMProxyUrl. Run run-local-e2e.ps1 first."
}
Write-Host "[pre-check] LiteLLM proxy OK"

# ── write .env for credential proxy ──────────────────────────────────────────
$envFile = Join-Path $repoRoot ".env"
$needsCleanup = $false
if (-not (Test-Path $envFile)) {
    @"
JCLAW_GATEWAY_BASE_URL=$LiteLLMProxyUrl/v1
JCLAW_GATEWAY_API_KEY=$LiteLLMApiKey
JCLAW_MODEL=$MainModel
JCLAW_WORKER_MODEL=$WorkerModel
"@ | Set-Content -Path $envFile -Encoding UTF8
    $needsCleanup = $true
    Write-Host "[env] Created temporary .env"
} else {
    Write-Host "[env] Using existing .env ($(Split-Path $envFile -Leaf))"
}

# ── start credential proxy ────────────────────────────────────────────────────
Write-Host "[1/3] Starting credential proxy on :$CredProxyPort"
Stop-ByPort -Port $CredProxyPort

$credProxyLog = Join-Path $logsDir "cred-proxy.log"
$credProxyErr = Join-Path $logsDir "cred-proxy.err.log"

# Prefer the joy env Python directly (avoids mamba PS-alias limitation in Start-Process)
$joyPython = "C:\Users\User\Desktop\thesis\data\joy\python.exe"
if (-not (Test-Path $joyPython)) {
    if (-not $Env:MAMBA_EXE -or -not (Test-Path $Env:MAMBA_EXE)) {
        throw "joy Python not found at $joyPython and MAMBA_EXE not set."
    }
    $credExe  = $Env:MAMBA_EXE
    $credArgs = @("run", "-n", $JoyEnv, "python", "-m", "src.main", "start-proxy", "--port", "$CredProxyPort", "--host", "0.0.0.0")
} else {
    $credExe  = $joyPython
    $credArgs = @("-m", "src.main", "start-proxy", "--port", "$CredProxyPort", "--host", "0.0.0.0")
}

$credProxyProc = Start-Process -FilePath $credExe `
    -ArgumentList $credArgs `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $credProxyLog `
    -RedirectStandardError  $credProxyErr `
    -WindowStyle Hidden -PassThru

# Wait for port to be listening (credential proxy doesn't expose /v1/models)
$credListening = $false
for ($i = 0; $i -lt 20; $i++) {
    if (Get-NetTCPConnection -State Listen -LocalPort $CredProxyPort -ErrorAction SilentlyContinue) {
        $credListening = $true; break
    }
    Start-Sleep -Seconds 1
}
if (-not $credListening) {
    $errTail = Get-Content $credProxyErr -Tail 20 -ErrorAction SilentlyContinue | Out-String
    throw "Credential proxy did not start on :$CredProxyPort.`n$errTail"
}
Write-Host "[1/3] Credential proxy running on :$CredProxyPort"

# ── prepare test group ────────────────────────────────────────────────────────
Write-Host "`n[2/3] Preparing test group structure"
$testGroupFolder = "skill_test"
$groupDir        = Join-Path $repoRoot "groups\$testGroupFolder"
$dataSessionDir  = Join-Path $repoRoot "data\sessions\$testGroupFolder"
$claudeDir       = Join-Path $dataSessionDir ".claude"
$ipcDir          = Join-Path $dataSessionDir "ipc"
$agentRunnerDir  = Join-Path $dataSessionDir "agent-runner-src"

foreach ($d in @($groupDir, $claudeDir, $ipcDir,
                  (Join-Path $ipcDir "messages"),
                  (Join-Path $ipcDir "tasks"),
                  (Join-Path $ipcDir "input"))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# Copy container skills into .claude/skills
$skillsSrc = Join-Path $repoRoot "container\skills"
$skillsDst = Join-Path $claudeDir "skills"
if (Test-Path $skillsSrc) {
    Copy-Item -Recurse -Force -Path "$skillsSrc\*" -Destination $skillsDst
    Write-Host "[2/3] Copied container skills to $skillsDst"
}

# Write .claude/settings.json
$settingsFile = Join-Path $claudeDir "settings.json"
@{
    env = @{
        CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1"
        CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD = "1"
        CLAUDE_CODE_DISABLE_AUTO_MEMORY = "0"
    }
} | ConvertTo-Json -Depth 5 | Set-Content -Path $settingsFile -Encoding UTF8

# Copy agent-runner src
$agentRunnerSrc = Join-Path $repoRoot "container\agent-runner\src"
if ((Test-Path $agentRunnerSrc) -and -not (Test-Path $agentRunnerDir)) {
    Copy-Item -Recurse -Path $agentRunnerSrc -Destination $agentRunnerDir
}

Write-Host "[2/3] Test group ready"

# ── skill test matrix ─────────────────────────────────────────────────────────
# Each entry: skill name -> minimal probe prompt
# The agent should respond with something (non-error) to show the skill is accessible.
$skillTests = [ordered]@{
    "status"          = "/no_think`nRun the status skill. Reply with STATUS_OK if the skill loaded."
    "capabilities"    = "/no_think`nList available capabilities. Reply with CAPABILITIES_OK if the skill loaded."
    "web-search"      = "/no_think`nYou have a web-search skill. Just reply WEB_SEARCH_OK to confirm the skill is loaded."
    "source-verify"   = "/no_think`nYou have a source-verify skill. Reply SOURCE_VERIFY_OK to confirm."
    "host-browser"    = "/no_think`nYou have a host-browser skill. Reply HOST_BROWSER_OK to confirm."
    "agent-browser"   = "/no_think`nYou have an agent-browser skill. Reply AGENT_BROWSER_OK to confirm."
    "repo-radar"      = "/no_think`nYou have a repo-radar skill. Reply REPO_RADAR_OK to confirm."
    "slack-formatting"= "/no_think`nYou have a slack-formatting skill. Reply SLACK_FORMAT_OK to confirm."
}

# Build base docker args (mounts + env)
function Get-DockerArgs {
    param([string]$ContainerName)

    $hostGateway = "host.docker.internal"
    $args = @(
        "run", "-i", "--rm", "--name", $ContainerName,
        "-e", "TZ=UTC",
        "-e", "ANTHROPIC_BASE_URL=http://${hostGateway}:${CredProxyPort}",
        "-e", "ANTHROPIC_API_KEY=placeholder",
        "-e", "JCLAW_MODEL=$MainModel",
        "-e", "JCLAW_WORKER_MODEL=$WorkerModel",
        # project root (read-only) — gives agent access to CLAUDE.md etc.
        "-v", "${repoRoot}:/workspace/project:ro",
        # group dir
        "-v", "${groupDir}:/workspace/group",
        # .claude dir (skills, settings)
        "-v", "${claudeDir}:/home/node/.claude",
        # ipc
        "-v", "${ipcDir}:/workspace/ipc",
        # agent-runner src
        "-v", "${agentRunnerDir}:/app/src",
        $ContainerImage
    )
    return $args
}

function Invoke-ContainerSkill {
    param([string]$SkillName, [string]$Prompt)

    $containerName = "jclaw-skill-test-$SkillName-$(Get-Random -Maximum 9999)"
    $payload = @{
        prompt      = $Prompt
        groupFolder = $testGroupFolder
        chatJid     = "test@skill.test"
        isMain      = $true
    } | ConvertTo-Json -Compress

    $dockerArgs = Get-DockerArgs -ContainerName $containerName

    # Write payload to temp file WITHOUT BOM so docker stdin gets clean UTF-8
    $tmpPayload = Join-Path $env:TEMP "jclaw-skill-payload-$SkillName.json"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tmpPayload, $payload, $utf8NoBom)

    $tmpStdout = Join-Path $env:TEMP "jclaw-skill-out-$SkillName.txt"
    $tmpStderr = Join-Path $env:TEMP "jclaw-skill-err-$SkillName.txt"

    $startTime = Get-Date
    try {
        $proc = Start-Process -FilePath "docker" `
            -ArgumentList $dockerArgs `
            -RedirectStandardInput  $tmpPayload `
            -RedirectStandardOutput $tmpStdout `
            -RedirectStandardError  $tmpStderr `
            -WindowStyle Hidden -PassThru
        $finished = $proc.WaitForExit($ContainerTimeoutSec * 1000)
        if (-not $finished) {
            $proc.Kill()
            return @{ Status = "FAIL"; Error = "Container timed out after ${ContainerTimeoutSec}s"; Duration = $ContainerTimeoutSec }
        }
        $exitCode = $proc.ExitCode
    } catch {
        return @{ Status = "FAIL"; Error = $_.Exception.Message; Duration = 0 }
    }
    $duration = [int]((Get-Date) - $startTime).TotalSeconds

    $outputStr = Get-Content $tmpStdout -Raw -ErrorAction SilentlyContinue
    if (-not $outputStr) { $outputStr = "" }
    $startMarker = "---JCLAW_OUTPUT_START---"
    $endMarker   = "---JCLAW_OUTPUT_END---"
    if ($outputStr -match "$startMarker`r?`n(.*?)`r?`n$endMarker") {
        try {
            $parsed = $Matches[1] | ConvertFrom-Json
            if ($parsed.status -eq "error") {
                return @{ Status = "FAIL"; Error = $parsed.error; Duration = $duration }
            }
            $result = $parsed.result
            return @{ Status = "PASS"; Result = $result; Duration = $duration }
        } catch {
            return @{ Status = "FAIL"; Error = "JSON parse error: $($_.Exception.Message)"; Duration = $duration }
        }
    } elseif ($exitCode -ne 0) {
        $tail = ($outputStr -split "`n" | Select-Object -Last 8) -join "`n"
        return @{ Status = "FAIL"; Error = "exit=$exitCode`n$tail"; Duration = $duration }
    } else {
        return @{ Status = "FAIL"; Error = "No output markers found"; Duration = $duration }
    }
}

# ── run tests ─────────────────────────────────────────────────────────────────
Write-Host "`n[3/3] Running container skill tests`n"
Write-Host ("{0,-20} {1,-6} {2,-8} {3}" -f "SKILL", "STATUS", "TIME(s)", "NOTES")
Write-Host ("-" * 80)

$results = [ordered]@{}
foreach ($skill in $skillTests.Keys) {
    Write-Host ("[{0}] Testing..." -f $skill) -NoNewline
    $r = Invoke-ContainerSkill -SkillName $skill -Prompt $skillTests[$skill]
    $results[$skill] = $r

    $statusColor = if ($r.Status -eq "PASS") { "Green" } else { "Red" }
    if ($r.Status -eq "PASS") {
        $srcStr = if ($r.Result) { $r.Result } else { "" }
        $notes = ($srcStr -replace "`r|`n", " ").Substring(0, [Math]::Min(60, $srcStr.Length))
    } else {
        $srcStr = if ($r.Error) { $r.Error } else { "" }
        $notes = ($srcStr -replace "`r|`n", " ").Substring(0, [Math]::Min(80, $srcStr.Length))
    }
    Write-Host "`r" -NoNewline
    Write-Host ("{0,-20} " -f $skill) -NoNewline
    Write-Host ("{0,-6} " -f $r.Status) -ForegroundColor $statusColor -NoNewline
    Write-Host ("{0,-8} {1}" -f $r.Duration, $notes)
}

Write-Host ("-" * 80)
$passed = ($results.Values | Where-Object { $_.Status -eq "PASS" }).Count
$failed = ($results.Values | Where-Object { $_.Status -eq "FAIL" }).Count
Write-Host "Results: $passed PASS, $failed FAIL out of $($results.Count) container skills`n"

# ── cleanup ───────────────────────────────────────────────────────────────────
Write-Host "[cleanup] Stopping credential proxy"
Stop-ByPort -Port $CredProxyPort
if ($needsCleanup -and (Test-Path $envFile)) {
    Remove-Item $envFile -Force
    Write-Host "[cleanup] Removed temporary .env"
}

Write-Host "`nCredential proxy log: $credProxyErr"
if ($failed -gt 0) { exit 1 } else { exit 0 }
