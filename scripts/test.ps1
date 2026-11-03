<#
.SYNOPSIS
  Every check for this cinema: shared-file drift, Python tests, lint, types, unit tests, build.
  The user's own database must come through untouched, so its modification time is compared.

  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test.ps1 [-E2E] [-NoBuild]
#>
[CmdletBinding()]
param(
    [switch]$E2E,
    [switch]$NoBuild
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$python = if (Test-Path -LiteralPath '.venv/Scripts/python.exe') { '.venv/Scripts/python.exe' } else { 'python' }
$app = if ((Get-Content backend/settings.py -Raw) -match '(?m)^PORT\s*=\s*6969') { 'MidnightCinema' } else { 'OnlineCinema' }
$db = Join-Path $env:APPDATA "$app/app.sqlite"
$before = if (Test-Path -LiteralPath $db) { (Get-Item -LiteralPath $db).LastWriteTimeUtc } else { $null }
$failed = @()

function Run([string]$Name, [scriptblock]$Block) {
    Write-Host ("`n== {0}" -f $Name) -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE) { $script:failed += $Name; Write-Host "FAILED: $Name" -ForegroundColor Red }
}

Run 'shared files' { & $python tools/check-shared.py }
Run 'python tests' { & $python -B -m unittest discover -s tests }
Push-Location web
try {
    Run 'lint' { npm run --silent lint }
    Run 'types' { npm run --silent typecheck }
    Run 'unit tests' { npm run --silent test }
    if (-not $NoBuild) { Run 'build' { npm run --silent build } }
} finally { Pop-Location }
if ($E2E) {
    $env:CINEMA_E2E = '1'
    Run 'headless browser' { & $python -B -m unittest discover -s e2e -t . -v }
}

if ($before) {
    $after = (Get-Item -LiteralPath $db).LastWriteTimeUtc
    if ($after -ne $before) { $failed += 'database untouched'; Write-Host "FAILED: a test wrote to $db" -ForegroundColor Red }
}
if ($failed) { Write-Host ("`nfailed: {0}" -f ($failed -join ', ')) -ForegroundColor Red; exit 1 }
Write-Host "`nall checks passed" -ForegroundColor Green
