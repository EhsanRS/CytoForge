$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Start-CytoForge.cmd') @args
exit $LASTEXITCODE
