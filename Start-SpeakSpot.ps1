param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$repoRoot = $PSScriptRoot
$backendRoot = Join-Path $repoRoot 'backend'
$frontendRoot = Join-Path $repoRoot 'frontend'
$pythonPath = Join-Path $backendRoot '.venv\Scripts\python.exe'
$logRoot = Join-Path $repoRoot '.cache\launcher'
$started = @()

function Read-Page([string]$Url) {
    try { return (Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).Content }
    catch { return $null }
}
function Test-Backend {
    $body = Read-Page 'http://127.0.0.1:8000/api/health'
    if (-not $body) { return $false }
    try { $health = $body | ConvertFrom-Json; return $health.status -eq 'ok' -and $health.service -eq 'SpeakSpot' }
    catch { return $false }
}
function Test-Frontend {
    $body = Read-Page 'http://127.0.0.1:5173/'
    return $body -and $body.Contains('<title>SpeakSpot</title>') -and $body.Contains('/@vite/client')
}
function Test-Port([int]$Port) {
    return [bool]([System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq $Port })
}
function Wait-Ready([scriptblock]$Check, $Process, [string]$Name) {
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        if (& $Check) { return }
        if ($Process -and $Process.HasExited) { throw "$Name stopped during startup. See $logRoot" }
        Start-Sleep -Milliseconds 400
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Name did not become ready. See $logRoot"
}

try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Missing backend\.venv\Scripts\python.exe. Follow the setup steps in README.md first.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules\vite'))) {
        throw 'Missing frontend dependencies. Run pnpm install in frontend first.'
    }
    $pnpmPath = (Get-Command pnpm.cmd -ErrorAction Stop).Source
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    if (-not (Test-Backend)) {
        if (Test-Port 8000) { throw 'Port 8000 is occupied by another service. It was left running.' }
        Write-Host '[1/2] Starting backend with backend\.venv...'
        $backendProcess = Start-Process -FilePath $pythonPath -ArgumentList @(
            '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload'
        ) -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logRoot 'backend.out.log') `
            -RedirectStandardError (Join-Path $logRoot 'backend.err.log')
        $started += $backendProcess
        Wait-Ready { Test-Backend } $backendProcess 'Backend'
    } else { Write-Host '[1/2] Backend is already running.' }
    if (-not (Test-Frontend)) {
        if (Test-Port 5173) { throw 'Port 5173 is occupied by another service. It was left running.' }
        Write-Host '[2/2] Starting frontend with pnpm dev...'
        # Encode literal paths so spaces and apostrophes do not become shell syntax.
        $workingPath = $frontendRoot.Replace("'", "''")
        $commandPath = $pnpmPath.Replace("'", "''")
        $command = "Set-Location -LiteralPath '$workingPath'`n& '$commandPath' dev --host 127.0.0.1 --port 5173 --strictPort`n" + 'exit $LASTEXITCODE'
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $frontendProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded
        ) -WorkingDirectory $frontendRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logRoot 'frontend.out.log') `
            -RedirectStandardError (Join-Path $logRoot 'frontend.err.log')
        $started += $frontendProcess
        Wait-Ready { Test-Frontend } $frontendProcess 'Frontend'
    } else { Write-Host '[2/2] Frontend is already running.' }
    Write-Host 'SpeakSpot is ready: http://127.0.0.1:5173'
    Write-Host "Logs: $logRoot"
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5173' }
} catch {
    # Only undo services started by this launch, never an existing server.
    foreach ($process in $started) {
        if (-not $process.HasExited) { & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null }
    }
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
