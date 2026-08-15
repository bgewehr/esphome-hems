param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("git", "python")]
    [string]$Tool,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ToolArgs
)

$ErrorActionPreference = "Stop"

$candidates = switch ($Tool) {
    "git" { @("%ProgramFiles%\Git\cmd\git.exe") }
    "python" { @("%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe") }
}

$command = Get-Command "$Tool.exe" -CommandType Application -ErrorAction SilentlyContinue
if ($command) {
    $executable = $command.Source
} else {
    $executable = $null
    foreach ($candidate in $candidates) {
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
        if (Test-Path -LiteralPath $expanded -PathType Leaf) {
            $executable = $expanded
            break
        }
    }
    if (-not $executable) {
        throw "$Tool.exe was not found in PATH or known install locations."
    }
}

$started = Get-Date
Write-Host ("{0} executable: {1}" -f $Tool, $executable)
Write-Host ("=== {0} started {1:yyyy-MM-dd HH:mm:ss} ===" -f $Tool, $started) -ForegroundColor Cyan
& $executable @ToolArgs
if ($LASTEXITCODE -ne 0) {
    throw "$Tool failed with exit code $LASTEXITCODE"
}
$elapsed = (Get-Date) - $started
Write-Host ("=== {0} completed successfully in {1:c} (exit 0) ===" -f $Tool, $elapsed) -ForegroundColor Green