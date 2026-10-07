[CmdletBinding(DefaultParameterSetName = "Live")]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Live")]
    [ValidateNotNullOrEmpty()]
    [string]$WallboxHost,

    [Parameter(Mandatory = $true, ParameterSetName = "Offline")]
    [string]$StatusJson,

    [ValidateRange(6, 200)]
    [double]$ExpectedGridLimitA = 50,

    [ValidateRange(6, 63)]
    [double]$ExpectedChargeLimitA = 16
)

$ErrorActionPreference = "Stop"

function Test-JsonNumber {
    param($Value)
    return ($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) -and
        -not [double]::IsNaN([double]$Value) -and -not [double]::IsInfinity([double]$Value)
}

if ($PSCmdlet.ParameterSetName -eq "Live") {
    if ([Uri]::CheckHostName($WallboxHost) -eq [UriHostNameType]::Unknown) {
        throw "WallboxHost must be a hostname or IP address, not a URL."
    }
    $endpoint = [UriBuilder]::new("http", $WallboxHost, 12800, "/user/status")
    $status = Invoke-RestMethod -Uri $endpoint.Uri -Method Get -TimeoutSec 5 -MaximumRedirection 0
} else {
    $status = ConvertFrom-Json -InputObject $StatusJson
}

$grid = $status.maxLimit.current
$connectors = @($status.connectors | Where-Object { (Test-JsonNumber $_.id) -and $_.id -eq 1 })
if ($connectors.Count -ne 1) {
    throw "Expected exactly one connector with numeric id 1."
}
$maximum = $connectors[0].max.current
if (-not (Test-JsonNumber $grid) -or $grid -lt 6 -or $grid -gt 200 -or
    -not (Test-JsonNumber $maximum) -or $maximum -lt 6 -or $maximum -gt 63) {
    throw "Missing or invalid maxLimit.current / connectors[id=1].max.current."
}

[pscustomobject]@{
    GridLimitA = [double]$grid
    ChargeLimitA = [double]$maximum
    ExpectedGridLimitA = $ExpectedGridLimitA
    ExpectedChargeLimitA = $ExpectedChargeLimitA
    GridLimitMatches = [math]::Abs($grid - $ExpectedGridLimitA) -lt 0.01
    ChargeLimitMatches = [math]::Abs($maximum - $ExpectedChargeLimitA) -lt 0.01
    Source = if ($PSCmdlet.ParameterSetName -eq "Live") { "Wallbox HTTP GET" } else { "Offline fixture" }
    ConfigurationChanged = $false
}