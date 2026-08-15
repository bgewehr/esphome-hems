# Usage: .\compile.ps1 [--clean] [--upload] [--clean-only] [--upload-only] [--device <ip>]
# Cleans, compiles, and optionally uploads to the device.
# The target IP is read from secrets.yaml (hems_ip key) unless --device is given.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
& "$env:SystemRoot\System32\chcp.com" 65001 | Out-Null

$Clean = $false
$Upload = $false
$CleanOnly = $false
$UploadOnly = $false
$Device = ""
for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        { $_ -in '-clean', '--clean' } { $Clean = $true }
        { $_ -in '-upload', '--upload' } { $Upload = $true }
        { $_ -in '-clean-only', '--clean-only' } { $CleanOnly = $true }
        { $_ -in '-upload-only', '--upload-only' } { $UploadOnly = $true }
        { $_ -in '-device', '--device' } { $i++; $Device = $args[$i] }
    }
}

function Resolve-RequiredCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Candidates = @()
    )

    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    foreach ($candidate in $Candidates) {
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
        if (Test-Path -LiteralPath $expanded -PathType Leaf) {
            return $expanded
        }
    }

    throw "Required command '$Name' was not found in PATH or known install locations."
}

function Invoke-Stage {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    $started = Get-Date
    Write-Host ("=== {0} started {1:yyyy-MM-dd HH:mm:ss} ===" -f $Name, $started) -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
    $elapsed = (Get-Date) - $started
    Write-Host ("=== {0} completed successfully in {1:c} (exit 0) ===" -f $Name, $elapsed) -ForegroundColor Green
}

$esphome = Resolve-RequiredCommand -Name "esphome.exe" -Candidates @(
    "%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts\esphome.exe",
    "%APPDATA%\Python\Python313\Scripts\esphome.exe"
)
Write-Host "ESPHome executable: $esphome"

if (($Upload -or $UploadOnly) -and $Device -eq "") {
    $secrets = Get-Content "secrets.yaml" -ErrorAction Stop | Where-Object { $_ -match "^hems_ip:" }
    if ($secrets) {
        $Device = ($secrets -split ":", 2)[1].Trim().Trim('"').Trim("'")
    }
    if ($Device -eq "") {
        Write-Error "hems_ip not set in secrets.yaml - add 'hems_ip: 192.168.x.x' or pass --device <ip>"
        exit 1
    }
}

if ($Clean -or $CleanOnly) {
    Invoke-Stage -Name "ESPHome clean" -Action { & $esphome clean esphome-hems.yaml }
}

if (-not $CleanOnly -and -not $UploadOnly) {
    Invoke-Stage -Name "ESPHome compile" -Action { & $esphome compile esphome-hems.yaml }
}

if ($Upload -or $UploadOnly) {
    Invoke-Stage -Name "ESPHome OTA upload to $Device" -Action {
        & $esphome upload esphome-hems.yaml --device $Device
    }
}
