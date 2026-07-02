# Usage: .\compile.ps1 [--upload]
# Syncs patches/, compiles, and optionally uploads to the device.

param(
    [switch]$Upload,
    [string]$Device = "192.168.178.24"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Syncing patches ===" -ForegroundColor Cyan
python scripts/sync_patches.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== Compiling ===" -ForegroundColor Cyan
esphome compile esphome-hems.yaml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Upload) {
    Write-Host "=== Uploading to $Device ===" -ForegroundColor Cyan
    esphome upload --device $Device esphome-hems.yaml
}
