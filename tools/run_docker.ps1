$ErrorActionPreference = "Stop"

$command = Get-Command "docker.exe" -CommandType Application -ErrorAction SilentlyContinue
if ($command) {
    $docker = $command.Source
} else {
    $docker = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path -LiteralPath $docker -PathType Leaf)) {
        throw "docker.exe was not found in PATH or the Docker Desktop install location."
    }
}

$started = Get-Date
Write-Host "Docker executable: $docker"
Write-Host ("=== Docker started {0:yyyy-MM-dd HH:mm:ss} ===" -f $started) -ForegroundColor Cyan
& $docker @args
if ($LASTEXITCODE -ne 0) {
    throw "Docker failed with exit code $LASTEXITCODE"
}
$elapsed = (Get-Date) - $started
Write-Host ("=== Docker completed successfully in {0:c} (exit 0) ===" -f $elapsed) -ForegroundColor Green