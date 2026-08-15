$ErrorActionPreference = "Stop"

function Resolve-Tool {
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

    throw "$Name was not found in PATH or known install locations."
}

$tools = [ordered]@{
    Git = Resolve-Tool "git.exe" @("%ProgramFiles%\Git\cmd\git.exe")
    Docker = Resolve-Tool "docker.exe" @("%ProgramFiles%\Docker\Docker\resources\bin\docker.exe")
    Python = Resolve-Tool "python.exe" @("%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe")
    ESPHome = Resolve-Tool "esphome.exe" @(
        "%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts\esphome.exe",
        "%APPDATA%\Python\Python313\Scripts\esphome.exe"
    )
}

foreach ($entry in $tools.GetEnumerator()) {
    Write-Host ("{0}: {1}" -f $entry.Key, $entry.Value)
}

& $tools.Docker version --format "Docker client {{.Client.Version}}; server {{.Server.Version}}"
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon check failed with exit code $LASTEXITCODE"
}

& $tools.Git --version
if ($LASTEXITCODE -ne 0) { throw "Git check failed with exit code $LASTEXITCODE" }
& $tools.Python --version
if ($LASTEXITCODE -ne 0) { throw "Python check failed with exit code $LASTEXITCODE" }
& $tools.ESPHome version
if ($LASTEXITCODE -ne 0) { throw "ESPHome check failed with exit code $LASTEXITCODE" }

Write-Host "=== Environment validation completed successfully (exit 0) ===" -ForegroundColor Green