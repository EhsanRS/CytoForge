@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
set "CYTOFORGE_ROOT=%CD%"
set "CYTOFORGE_WINDOWS_ARCH=%PROCESSOR_ARCHITECTURE%"
if defined PROCESSOR_ARCHITEW6432 set "CYTOFORGE_WINDOWS_ARCH=%PROCESSOR_ARCHITEW6432%"
if /I not "%CYTOFORGE_WINDOWS_ARCH%"=="AMD64" (
  echo This trial requires 64-bit Windows on an Intel or AMD PC.
  goto :failed
)
if exist ".local\tools\node\node.exe" goto :launch
echo Preparing CytoForge for Windows. First launch needs an internet connection.
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $root=$env:CYTOFORGE_ROOT; $pin=(Get-Content -LiteralPath (Join-Path $root 'tools/windows/toolchain.json') -Raw | ConvertFrom-Json).node; $cache=Join-Path $root '.cache/downloads'; $tools=Join-Path $root '.local/tools'; New-Item -ItemType Directory -Force $cache,$tools,(Join-Path $root '.tmp') | Out-Null; $archive=Join-Path $cache ($pin.folder+'.zip'); Invoke-WebRequest -UseBasicParsing -Uri $pin.url -OutFile $archive; if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pin.sha256) { Remove-Item -LiteralPath $archive; throw 'Node.js download checksum failed' }; $stage=Join-Path $root ('.tmp/node-bootstrap-'+[guid]::NewGuid().ToString('N')); try { Expand-Archive -LiteralPath $archive -DestinationPath $stage; Move-Item -LiteralPath (Join-Path $stage $pin.folder) -Destination (Join-Path $tools 'node') } finally { if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force } }"
if errorlevel 1 goto :failed
:launch
".local\tools\node\node.exe" tools/windows/run.mjs %*
set "CYTOFORGE_WINDOWS_EXIT=%ERRORLEVEL%"
if not "%CYTOFORGE_WINDOWS_EXIT%"=="0" goto :failed
popd
exit /b 0
:failed
echo.
echo CytoForge could not start. Keep this message for troubleshooting.
echo If setup reached the app launcher, see .cache\logs\windows-start.log.
if not defined CI pause
popd
exit /b 1
