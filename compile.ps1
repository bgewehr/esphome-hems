# Usage: .\compile.ps1 [--upload] [--device <ip>]
# Compiles and optionally uploads to the device.
# The target IP is read from secrets.yaml (hems_ip key) unless --device is given.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Upload = $false
$Device = ""
for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        { $_ -in '-upload', '--upload' } { $Upload = $true }
        { $_ -in '-device', '--device' } { $i++; $Device = $args[$i] }
    }
}

if ($Upload -and $Device -eq "") {
    $secrets = Get-Content "secrets.yaml" -ErrorAction Stop | Where-Object { $_ -match "^hems_ip:" }
    if ($secrets) {
        $Device = ($secrets -split ":", 2)[1].Trim().Trim('"').Trim("'")
    }
    if ($Device -eq "") {
        Write-Error "hems_ip not set in secrets.yaml - add 'hems_ip: 192.168.x.x' or pass --device <ip>"
        exit 1
    }
}

Write-Host "=== Compiling ===" -ForegroundColor Cyan
esphome compile esphome-hems.yaml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Upload) {
    Write-Host "=== Uploading to $Device ===" -ForegroundColor Cyan
    esphome upload --device $Device esphome-hems.yaml
}
