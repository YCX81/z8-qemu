# SPDX-License-Identifier: GPL-2.0-or-later
param(
    [switch]$Headless,
    [switch]$RequireLiveView,
    [switch]$MenuTest,
    [ValidateRange(0,3600)][int]$Seconds = 0,
    [ValidateRange(1,65535)][int]$Port = 8788
)
$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$z8LinuxHome = (& wsl.exe -d Ubuntu --exec printenv HOME).Trim()
if ($LASTEXITCODE -ne 0 -or -not $z8LinuxHome.StartsWith('/')) { throw 'Could not resolve the Ubuntu home directory.' }
$linuxProject = (& wsl.exe -d Ubuntu --exec wslpath -u $project).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the Z8 project in Ubuntu.' }
$runName = 'z8-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$linuxOutput = "$z8LinuxHome/.local/share/nikon-z8-qemu/runs/" + $runName
$runArguments = @(
    "$linuxProject/tools/z8_run.py",
    '--firmware', "$linuxProject/.local/firmware/z8-c311-main.bin",
    '--qemu', "$z8LinuxHome/.local/share/nikon-z8-qemu/work/build/qemu-system-aarch64",
    '--raw', "$linuxProject/.local/input.raw",
    '--output', $linuxOutput,
    '--seconds', $Seconds.ToString(),
    '--port', $Port.ToString()
)
if ($Headless) { $runArguments += '--headless' }
if ($RequireLiveView) { $runArguments += '--require-liveview' }
if ($MenuTest) { $runArguments += '--menu-test' }
Write-Output "Z8 QEMU output: $linuxOutput"
Write-Output 'Original firmware with explicit virtual peripherals. Menu and live-view acceptance are recorded separately.'
& wsl.exe -d Ubuntu --exec "$z8LinuxHome/.local/share/nikon-z8-qemu/venv/bin/python" @runArguments
if ($LASTEXITCODE -ne 0) { throw 'Z8 run failed or required acceptance did not pass; inspect its result.json.' }
