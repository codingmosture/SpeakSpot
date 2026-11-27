param(
    [Parameter(Mandatory)][string]$Launcher,
    [Parameter(Mandatory)][string]$LogPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Install-Prerequisite($Uri, $FileName, $Publisher, $Program, $Arguments) {
    $download = Join-Path $PSScriptRoot $FileName
    Add-Content -LiteralPath $LogPath -Value "Downloading $FileName"
    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $download -TimeoutSec 900
    $signature = Get-AuthenticodeSignature -FilePath $download
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.GetNameInfo('SimpleName', $false) -ne $Publisher) {
        throw "Authenticode verification failed for $FileName"
    }
    if (!$Program) {
        $Program = $download
    }
    $process = Start-Process -FilePath $Program -ArgumentList $Arguments.Replace('{download}', $download) -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -in 1641, 3010) {
        exit 3010
    }
    if ($process.ExitCode -ne 0) {
        throw "$FileName exited with code $($process.ExitCode)"
    }
}

function Test-Chrome {
    $process = Start-Process -FilePath $Launcher -ArgumentList '--check-chrome' -WindowStyle Hidden -Wait -PassThru
    return $process.ExitCode -eq 0
}

try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
    Set-Content -LiteralPath $LogPath -Value 'Checking SpeakSpot prerequisites.'
    $runtime = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
    if ($runtime.Installed -ne 1 -or [version]$runtime.Version.TrimStart('v') -lt [version]'14.50.35719' -or
        !(Test-Path -LiteralPath "$env:WINDIR\System32\msvcp140.dll")) {
        Install-Prerequisite 'https://aka.ms/vc14/vc_redist.x64.exe' 'vc_redist.x64.exe' `
            'Microsoft Corporation' '' '/install /quiet /norestart'
    }
    if (!(Test-Chrome)) {
        Install-Prerequisite 'https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi' `
            'GoogleChromeStandaloneEnterprise64.msi' 'Google LLC' "$env:WINDIR\System32\msiexec.exe" `
            '/i "{download}" /qn /norestart NOGOOGLEUPDATEPING=1'
        if (!(Test-Chrome)) {
            throw 'Google Chrome could not be located after running its installer.'
        }
    }
    Add-Content -LiteralPath $LogPath -Value 'Prerequisites are ready.'
} catch {
    Add-Content -LiteralPath $LogPath -Value ($_ | Out-String)
    exit 1
}
