<#
Windows equivalent of the Makefile.  Usage:
  .\demo.ps1 up                 # build + start everything
  .\demo.ps1 smoke              # end-to-end check
  .\demo.ps1 scenario loop      # healthy | tool_error | slow_tool | loop | bad_answer | pii
  .\demo.ps1 traffic-on | traffic-off
  .\demo.ps1 backend-jaeger | backend-vendor | backend-default
  .\demo.ps1 logs | down | reset
If scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\demo.ps1 up
#>
param(
    [Parameter(Position = 0)][string]$Command = "help",
    [Parameter(Position = 1)][string]$Arg = "healthy"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Python {
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
    throw "Python 3 not found. Install it from python.org (tick 'Add to PATH')."
}

function With-Overlay([string]$overlay, [scriptblock]$block) {
    $env:COLLECTOR_OVERLAY = $overlay
    try { & $block } finally { Remove-Item Env:COLLECTOR_OVERLAY -ErrorAction SilentlyContinue }
}

switch ($Command) {
    "up" {
        if (-not (Test-Path .env)) { Copy-Item .env.example .env }
        docker compose up -d --build
        Write-Host "Control panel: http://localhost:8000   Grafana: http://localhost:3001/d/agent-observability"
    }
    "down"  { docker compose --profile jaeger down }
    "reset" { docker compose --profile jaeger down -v }
    "logs"  { docker compose logs -f agent otel-collector }
    "smoke" { & (Python) scripts\smoke_test.py }
    "scenario" {
        $body = @{ scenario = $Arg } | ConvertTo-Json
        Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/run -ContentType "application/json" -Body $body |
            ConvertTo-Json -Depth 6
    }
    "traffic-on"  { Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/traffic/start }
    "traffic-off" { Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/traffic/stop }
    "backend-default" {
        With-Overlay "none" { docker compose up -d otel-collector }
        docker compose --profile jaeger stop jaeger
    }
    "backend-jaeger" {
        With-Overlay "jaeger" { docker compose --profile jaeger up -d jaeger otel-collector }
        Write-Host "Jaeger UI: http://localhost:16686  (agent container was not touched)"
    }
    "backend-vendor" {
        With-Overlay "vendor" { docker compose up -d otel-collector }
    }
    "dashboard" { & (Python) scripts\build_dashboard.py }
    default {
        Get-Content $PSCommandPath -TotalCount 10 | Select-Object -Skip 1 | Write-Host
    }
}
