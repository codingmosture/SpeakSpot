#requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$InnoCompiler,
    [string]$OutputDirectory,
    [string]$PythonArchive,
    [string]$DependencyDirectory,
    [string]$BuildRoot = [IO.Path]::GetTempPath(),
    [switch]$UseInstalledFrontendDependencies
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $project 'frontend'
$lock = Join-Path $PSScriptRoot 'python-win64.lock'
$version = (Get-Content -Raw -LiteralPath (Join-Path $frontend 'package.json') | ConvertFrom-Json).version
$inno = (Get-Item -LiteralPath $InnoCompiler).FullName
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$stage = Join-Path $BuildRoot ('ss-' + [guid]::NewGuid().ToString('N').Substring(0, 12))
if (!$OutputDirectory) { $OutputDirectory = Join-Path $project 'dist' }
$output = (New-Item -ItemType Directory -Force -Path $OutputDirectory).FullName

function Run([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program exited with code $LASTEXITCODE"
    }
}

New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
    if (!$UseInstalledFrontendDependencies) {
        Run 'pnpm' @('--dir', $frontend, 'install', '--frozen-lockfile')
    }
    Run 'pnpm' @('--dir', $frontend, 'build')
    Copy-Item -LiteralPath (Join-Path $frontend 'dist') -Destination (Join-Path $stage 'web') -Recurse

    $backend = New-Item -ItemType Directory -Path (Join-Path $stage 'backend')
    Copy-Item -Path (Join-Path $project 'backend\*.py') -Destination $backend.FullName

    if (!$PythonArchive) {
        $PythonArchive = Join-Path $stage 'python.zip'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-embeddable-amd64.zip' -OutFile $PythonArchive
    }
    $hash = '0BA1EE4BFCBDC3A559D27E2E9CE76D04A7839947A23F46375872C755C0D463B8'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $PythonArchive).Hash -ne $hash) {
        throw 'The Python embeddable archive SHA-256 did not match.'
    }

    $runtime = Join-Path $stage 'python'
    Expand-Archive -LiteralPath $PythonArchive -DestinationPath $runtime
    $sitePackages = (New-Item -ItemType Directory -Path (Join-Path $runtime 'Lib\site-packages') -Force).FullName
    Set-Content -Encoding ascii -LiteralPath (Join-Path $runtime 'python313._pth') -Value @(
        'python313.zip', '.', 'Lib\site-packages', '..\backend', 'import site'
    )
    if ($DependencyDirectory) {
        # A short staging path keeps wheel modules with long names importable on Windows.
        & robocopy $DependencyDirectory $sitePackages /E /XJ /MT:16 /XD Scripts __pycache__ /NFL /NDL /NJH /NJS /NP
        if ($LASTEXITCODE -ge 8) {
            throw "Copying packaged dependencies failed with code $LASTEXITCODE"
        }
    } else {
        Run 'python' @('-c', "import struct,sys; assert sys.version_info[:2] == (3,13) and struct.calcsize('P') == 8")
        Run 'python' @('-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
            '--only-binary=:all:', '--no-binary=kiwipiepy-model', '--no-compile',
            '--requirement', (Join-Path $project 'backend\requirements.txt'),
            '--requirement', $lock, '--target', $sitePackages)
    }

    $embedded = Join-Path $runtime 'python.exe'
    $checkVersions = @'
import importlib.metadata as metadata
import sys
from pathlib import Path
for requirement in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if requirement and not requirement.startswith("#"):
        name, version = requirement.split("==")
        if metadata.version(name) != version:
            raise RuntimeError(f"Unexpected version of {name}")
import cv2, docling.document_converter, easyocr, fastapi, kiwipiepy
import pymupdf, sentence_transformers, torch, torchvision, uvicorn
'@
    Run $embedded @('-B', '-c', $checkVersions, $lock)

    $icon = Join-Path $stage 'SpeakSpot.ico'
    Copy-Item -LiteralPath (Join-Path $frontend 'public\speakspot-window.ico') -Destination $icon
    Run $csc @('/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001',
        "/win32icon:$icon", "/out:$stage\SpeakSpot.exe", (Join-Path $PSScriptRoot 'Launcher.cs'),
        (Join-Path $PSScriptRoot 'WindowBranding.cs'))
    Run $inno @("/DPayload=$stage", "/DAppVersion=$version", "/O$output", (Join-Path $PSScriptRoot 'SpeakSpot.iss'))
    Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $output 'SpeakSpot-Setup.exe')
} finally {
    $resolvedStage = (Resolve-Path -LiteralPath $stage).ProviderPath
    $resolvedRoot = (Resolve-Path -LiteralPath $BuildRoot).ProviderPath
    if ([IO.Path]::GetDirectoryName($resolvedStage) -eq $resolvedRoot) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
