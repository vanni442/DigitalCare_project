$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$taskMetadata = Join-Path $taskRoot 'data\processes.json'
if (-not (Test-Path -LiteralPath $taskMetadata)) { Write-Output 'No managed processes recorded.'; exit }
$taskEntries = Get-Content -LiteralPath $taskMetadata -Raw | ConvertFrom-Json
foreach ($taskName in @('app', 'fuseki')) {
    $taskEntry = $taskEntries.$taskName
    if (-not $taskEntry) { continue }
    $taskProcess = Get-Process -Id ([int]$taskEntry.pid) -ErrorAction SilentlyContinue
    if (-not $taskProcess) { continue }
    $taskOwnsProcess = $taskEntry.project -eq $taskRoot -and $taskEntry.createdFileTime -and $taskProcess.Path -eq $taskEntry.command[0] -and $taskProcess.StartTime.ToFileTimeUtc().ToString() -eq $taskEntry.createdFileTime
    if ($taskOwnsProcess) {
        Stop-Process -Id $taskProcess.Id -Force
        Write-Output "Stopped project $taskName process."
    } else {
        Write-Warning "Skipped PID $($taskEntry.pid): executable and creation-time ownership could not be confirmed."
    }
}
