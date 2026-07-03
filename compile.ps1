# Usage: .\compile.ps1 [--upload]
# Compiles and optionally uploads to the device.
# All source (openeebus/, port/, components/) is local — no sync step needed.

param(
    [switch]$Upload,
    [string]$Device = "192.168.178.24"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Compiling ===" -ForegroundColor Cyan
esphome compile esphome-hems.yaml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Upload) {
    Write-Host "=== Uploading to $Device ===" -ForegroundColor Cyan
    esphome upload --device $Device esphome-hems.yaml
}
