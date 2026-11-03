<#
.SYNOPSIS
  Build, test and restart this cinema's background server without serving a half-built page.

.DESCRIPTION
  1. The working tree must be clean (or -AllowDirty).
  2. The front-end is built into web\dist.next; the live web\dist keeps serving meanwhile.
  3. The Python tests run; a failure stops here.
  4. Waits until /api/health reports no cast and no offline job (or -Force), then swaps dist.next
     into place and restarts the scheduled task ("Midnight Cinema Server" / "Online Cinema Server").
  5. Waits for /api/health to answer with the new version and fetches the home page once.

  Run from the repository root: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy.ps1
#>
[CmdletBinding()]
param(
    [switch]$AllowDirty,
    [switch]$Force,
    [switch]$SkipTests,
    [int]$BusyWaitMinutes = 30
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$settings = Get-Content -LiteralPath (Join-Path $root 'backend/settings.py') -Raw -Encoding UTF8
if ($settings -notmatch '(?m)^PORT\s*=\s*(\d+)') { throw 'PORT not found in backend/settings.py' }
$port = [int]$Matches[1]
$task = switch ($port) { 6969 { 'Midnight Cinema Server' } 6970 { 'Online Cinema Server' } default { throw "No scheduled task is known for port $port" } }
$health = "http://127.0.0.1:$port/api/health"
$python = if (Test-Path -LiteralPath '.venv/Scripts/python.exe') { '.venv/Scripts/python.exe' } else { 'python' }

function Step([string]$Text) { Write-Host ("`n== {0}" -f $Text) -ForegroundColor Cyan }
function Get-Health {
    try { return Invoke-RestMethod -Uri $health -TimeoutSec 5 } catch { return $null }
}

Step 'Working tree'
$dirty = git status --porcelain --untracked-files=no
if ($dirty -and -not $AllowDirty) { throw "Uncommitted changes:`n$($dirty -join "`n")`nCommit them, or pass -AllowDirty." }
$version = (git rev-parse --short HEAD).Trim()
Write-Host "deploying $version to $task (port $port)"

Step 'Front-end build (web\dist.next)'
Push-Location web
try {
    if (!(Test-Path node_modules)) { npm install; if ($LASTEXITCODE) { throw 'npm install failed' } }
    npx vite build --outDir dist.next --emptyOutDir
    if ($LASTEXITCODE) { throw 'vite build failed' }
    npx tsc -p tsconfig.app.json --noEmit
    if ($LASTEXITCODE) { throw 'type check failed' }
} finally { Pop-Location }

if (-not $SkipTests) {
    Step 'Python tests'
    & $python -B -m unittest discover -s tests
    if ($LASTEXITCODE) { throw 'tests failed; nothing was deployed' }
}

Step 'Waiting for the server to be idle'
$deadline = (Get-Date).AddMinutes($BusyWaitMinutes)
while ($true) {
    $state = Get-Health
    if ($null -eq $state) { Write-Host 'server not answering; deploying anyway'; break }
    $casts = [int]$state.busy.casts; $jobs = [int]$state.busy.offline_jobs
    if (($casts -eq 0 -and $jobs -eq 0) -or $Force) { break }
    if ((Get-Date) -gt $deadline) { throw "still busy after $BusyWaitMinutes minutes (casts=$casts, offline_jobs=$jobs); pass -Force to restart anyway" }
    Write-Host ("busy: casts={0} offline_jobs={1}; checking again in 30 s" -f $casts, $jobs)
    Start-Sleep -Seconds 30
}

Step 'Swapping web\dist and restarting the task'
if (Test-Path web/dist.previous) { Remove-Item -Recurse -Force web/dist.previous }
if (Test-Path web/dist) { Rename-Item web/dist dist.previous }
Rename-Item web/dist.next dist
Disable-ScheduledTask -TaskName $task | Out-Null
Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($p in $listener) { $proc = Get-Process -Id $p -ErrorAction SilentlyContinue; if ($proc -and $proc.ProcessName -like 'python*') { Stop-Process -Id $p -Force } }
Enable-ScheduledTask -TaskName $task | Out-Null
Start-ScheduledTask -TaskName $task

Step 'Waiting for the new server'
$deadline = (Get-Date).AddSeconds(120)
do {
    Start-Sleep -Seconds 2
    $state = Get-Health
} while ((Get-Date) -lt $deadline -and ($null -eq $state -or $state.version -ne $version))
if ($null -eq $state) { throw "the server did not answer within 120 s; see $env:APPDATA\<app>\server-logs" }
if ($state.version -ne $version) { Write-Warning "server reports version $($state.version), expected $version" }
$home = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/" -TimeoutSec 10
if ($home.StatusCode -ne 200 -or $home.Content -notmatch '<div id="root">') { throw 'the home page did not come back' }
if (Test-Path web/dist.previous) { Remove-Item -Recurse -Force web/dist.previous }
Write-Host ("`ndeployed {0}: {1}" -f $version, ($state | ConvertTo-Json -Compress)) -ForegroundColor Green
