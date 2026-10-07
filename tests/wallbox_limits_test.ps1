$ErrorActionPreference = "Stop"
$checker = Join-Path $PSScriptRoot "../tools/diagnostics/check_wallbox_limits.ps1"

$result = & $checker -StatusJson '{"maxLimit":{"current":50},"connectors":[{"id":1,"max":{"current":16}}]}'
if (-not $result.GridLimitMatches -or -not $result.ChargeLimitMatches -or $result.ConfigurationChanged) {
    throw "Expected matching limits and no configuration change."
}
$result = & $checker -StatusJson '{"maxLimit":{"current":32},"connectors":[{"id":2,"max":{"current":32}},{"id":1,"max":{"current":10}}]}'
if ($result.GridLimitMatches -or $result.ChargeLimitMatches -or $result.ChargeLimitA -ne 10) {
    throw "Expected mismatch diagnostics for connector 1."
}

$invalid = @(
    '{}',
    '{"maxLimit":{"current":50},"connectors":[{"id":1,"max":{"current":16}},{"id":1,"max":{"current":16}}]}',
    '{"maxLimit":{"current":"50"},"connectors":[{"id":1,"max":{"current":16}}]}',
    '{"maxLimit":{"current":50},"connectors":[{"id":1,"max":{"current":64}}]}',
    '{"maxLimit":{"current":50},"connectors":[{"id":"1","max":{"current":16}}]}',
    '{"maxLimit":{"current":true},"connectors":[{"id":1,"max":{"current":16}}]}'
)
foreach ($fixture in $invalid) {
    $rejected = $false
    try { & $checker -StatusJson $fixture | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "Invalid wallbox fixture was accepted." }
}
Write-Host "Wallbox limit tests passed (8 fixtures; no network access)."