# SPDX-License-Identifier: GPL-2.0-or-later
param(
    [switch]$BootstrapFixture,
    [switch]$BusWindowFixture,
    [switch]$SerialFixture,
    [switch]$NandFixture,
    [ValidateRange(1,120)][int]$Seconds = 5
)
$ErrorActionPreference = 'Stop'
if ($BusWindowFixture -and -not $BootstrapFixture) { throw '-BusWindowFixture requires -BootstrapFixture.' }
if ($SerialFixture -and (-not $BootstrapFixture -or -not $BusWindowFixture)) { throw '-SerialFixture requires both -BootstrapFixture and -BusWindowFixture.' }
if ($NandFixture -and -not $BootstrapFixture) { throw '-NandFixture requires -BootstrapFixture.' }
$project = $PSScriptRoot
$z8LinuxHome = (& wsl.exe -d Ubuntu --exec printenv HOME).Trim()
if ($LASTEXITCODE -ne 0 -or -not $z8LinuxHome.StartsWith('/')) { throw 'Could not resolve the Ubuntu home directory.' }
$linuxProject = (& wsl.exe -d Ubuntu --exec wslpath -u $project).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the Z8 project in Ubuntu.' }
$runName = 'z8-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$runArguments = @(
    "$linuxProject/tools/z8_bringup.py",
    '--firmware', "$linuxProject/.local/firmware/z8-c311-main.bin",
    '--qemu', "$z8LinuxHome/.local/share/nikon-z8-qemu/work/build/qemu-system-aarch64",
    '--output', "$linuxProject/.local/runs/$runName",
    '--seconds', $Seconds.ToString()
)
if ($BootstrapFixture) { $runArguments += '--bootstrap-fixture' }
if ($BusWindowFixture) { $runArguments += '--bus-window-fixture' }
if ($SerialFixture) { $runArguments += '--serial-fixture' }
if ($NandFixture) { $runArguments += '--nand-fixture' }
& wsl.exe -d Ubuntu --exec "$z8LinuxHome/.local/share/nikon-z8-qemu/venv/bin/python" @runArguments
if ($LASTEXITCODE -ne 0) { throw 'Z8 diagnostic execution failed; inspect its result.json.' }
Write-Output "Evidence: $project\.local\runs\$runName\result.json"
Write-Output 'This is bounded firmware bring-up; camera menus and photography are not verified.'
