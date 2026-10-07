$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Bundled Python is missing. Extract the complete ZIP before running start.cmd.' }
& $taskPython -B (Join-Path $PSScriptRoot 'manage.py') start --open
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Read the message above and README.md.' }
