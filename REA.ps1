# SPDX-License-Identifier: GPL-2.0-or-later
# Optional REA CLI wrapper; arguments use Linux paths.
$ErrorActionPreference = 'Stop'
$z8LinuxHome = (& wsl.exe -d Ubuntu --exec printenv HOME).Trim()
if ($LASTEXITCODE -ne 0 -or -not $z8LinuxHome.StartsWith('/')) { throw 'Could not resolve the Ubuntu home directory.' }
$reaEnvironment = @(
    "GHIDRA_INSTALL_DIR=$z8LinuxHome/.local/share/nikon-z8-tools/ghidra_12.1.4_PUBLIC",
    'JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64',
    'GHIDRA_HEADLESS_MAXMEM=2G',
    "$z8LinuxHome/.local/share/nikon-z8-tools/rea/node_modules/.bin/rea"
)
& wsl.exe -d Ubuntu --exec env @reaEnvironment @args
exit $LASTEXITCODE
