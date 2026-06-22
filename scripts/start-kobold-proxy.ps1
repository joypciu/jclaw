param(
  [string]$MainUpstreamBaseUrl = "http://127.0.0.1:5001/v1",
  [string]$MainUpstreamModel = "Qwen_Qwen3.5-9B-Q8_0",
  [string]$WorkerUpstreamBaseUrl = "http://127.0.0.1:5002/v1",
  [string]$WorkerUpstreamModel = "Qwen_Qwen3.5-2B-bf16",
  [string]$MainAlias = "jclaw-main",
  [string]$WorkerAlias = "jclaw-worker",
    [int]$Port = 4000,
    [string]$ProxyKey = "dummy-key",
    [switch]$DetailedDebug
)

$litellm = Get-Command litellm -ErrorAction SilentlyContinue
if (-not $litellm) {
    Write-Error "litellm was not found in PATH. Install it with: pip install 'litellm[proxy]'"
    exit 1
}

$configPath = Join-Path $env:TEMP "jclaw-litellm-kobold.yaml"
$yaml = @"
model_list:
  - model_name: $MainAlias
    litellm_params:
      model: openai/$MainUpstreamModel
      api_base: $MainUpstreamBaseUrl
      api_key: $ProxyKey
      drop_params: true
  - model_name: $WorkerAlias
    litellm_params:
      model: openai/$WorkerUpstreamModel
      api_base: $WorkerUpstreamBaseUrl
      api_key: $ProxyKey
      drop_params: true
  - model_name: claude-3-5-sonnet-latest
    litellm_params:
      model: openai/$MainUpstreamModel
      api_base: $MainUpstreamBaseUrl
      api_key: $ProxyKey
      drop_params: true
  - model_name: claude-3-7-sonnet-latest
    litellm_params:
      model: openai/$MainUpstreamModel
      api_base: $MainUpstreamBaseUrl
      api_key: $ProxyKey
      drop_params: true
  - model_name: claude-sonnet-4-0
    litellm_params:
      model: openai/$MainUpstreamModel
      api_base: $MainUpstreamBaseUrl
      api_key: $ProxyKey
      drop_params: true

general_settings:
  master_key: $ProxyKey
"@

Set-Content -Path $configPath -Value $yaml -Encoding UTF8

$arguments = @('--config', $configPath, '--port', "$Port")
if ($DetailedDebug) {
    $arguments += '--detailed_debug'
}

Write-Host "Starting LiteLLM proxy on http://127.0.0.1:$Port"
Write-Host "Main upstream:   $MainUpstreamBaseUrl ($MainUpstreamModel -> $MainAlias)"
Write-Host "Worker upstream: $WorkerUpstreamBaseUrl ($WorkerUpstreamModel -> $WorkerAlias)"
Write-Host "Generated config: $configPath"

& $litellm.Source @arguments