# SPDX-License-Identifier: GPL-2.0-or-later
"""Integrate only this experimental board into a fresh QEMU 10.2.4 tree."""
from pathlib import Path
import argparse
import shutil
import subprocess

p = argparse.ArgumentParser()
p.add_argument('source', type=Path)
a = p.parse_args()
root = a.source.resolve()
if root.name != 'qemu-10.2.4' or not (root/'.wonderlantern-extracted').is_file():
    p.error('expected the verified upstream tree created by tools/build.py')
here = Path(__file__).resolve().parent
# Refuse an unexpected or partially patched CPU tree. A reverse dry run
# verifies an already applied patch; repeat builds must not silently skip it.
patch = here / 'arm-service-events.patch'
command = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)]
if subprocess.run(command + ['--forward', '--dry-run'], cwd=root,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    subprocess.run(command + ['--forward'], cwd=root, check=True)
elif subprocess.run(command + ['--reverse', '--dry-run'], cwd=root,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
    raise SystemExit('Unexpected ARM source: service event patch cannot be verified')
dma_patch = here / 'designware-dma.patch'
command = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(dma_patch)]
if subprocess.run(command + ['--forward', '--dry-run'], cwd=root,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
    subprocess.run(command + ['--forward'], cwd=root, check=True)
elif subprocess.run(command + ['--reverse', '--dry-run'], cwd=root,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
    raise SystemExit('Unexpected DesignWare source: DMA patch cannot be verified')

# Standard trace output is disabled by default. This patch observes original
# NVMe backend/CQE completion without changing command or completion behavior.
for name in ('nvme-completion-trace.patch', 'nvme-audit-state.patch'):
    command = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(here / name)]
    if subprocess.run(command + ['--forward', '--dry-run'], cwd=root,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        subprocess.run(command + ['--forward'], cwd=root, check=True)
    elif subprocess.run(command + ['--reverse', '--dry-run'], cwd=root,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        raise SystemExit('Unexpected NVMe source: patch cannot be verified: ' + name)
for overlay in here.glob('nikon-*'):
    if overlay.suffix in ('.c', '.h'):
        shutil.copyfile(overlay, root / 'hw/arm' / overlay.name)
# QEMU 10.2.4's NVMe controller links spdm_socket_* helpers even when no
# spdm_port is configured. NVME_PCI does not select the backend, so the
# --without-default-devices build explicitly selects SPDM_SOCKET for linking.
# This does not enable a Nikon authentication protocol or open a connection.
for filename, marker, addition in [
    ('hw/arm/Kconfig', 'config NIKON_Z9_LAB',
     '\nconfig NIKON_Z9_LAB\n    bool\n    depends on AARCH64\n    select ARM_GIC\n    select PCI_EXPRESS_DESIGNWARE\n    select NVME_PCI\n    select SPDM_SOCKET\n'),
    ('configs/devices/aarch64-softmmu/default.mak', 'CONFIG_NIKON_Z9_LAB=y',
     '\nCONFIG_NIKON_Z9_LAB=y\n'),
    # Separate, explicitly named diagnostic ROM data images. The minimal build
    # otherwise omits QEMU's loader device; no board code/CPU patch is needed.
    ('configs/devices/aarch64-softmmu/default.mak', 'CONFIG_GENERIC_LOADER=y',
     '\nCONFIG_GENERIC_LOADER=y\n'),
    ('configs/devices/aarch64-softmmu/default.mak', 'CONFIG_NVME_PCI=y',
     '\nCONFIG_NVME_PCI=y\n'),
]:
    path = root / filename
    body = path.read_text()
    if marker not in body:
        path.write_text(body + addition)
path = root / 'hw/arm/Kconfig'
body = path.read_text()
block = body[body.index('config NIKON_Z9_LAB'):].split('\nconfig ', 1)[0]
for select in ('PCI_EXPRESS_DESIGNWARE', 'NVME_PCI', 'SPDM_SOCKET'):
    if '    select ' + select not in block:
        body = body.replace('config NIKON_Z9_LAB\n',
                            'config NIKON_Z9_LAB\n    select ' + select + '\n', 1)
path.write_text(body)
path = root / 'hw/arm/meson.build'
body = path.read_text()
line = "arm_ss.add(when: 'CONFIG_NIKON_Z9_LAB', if_true: files('nikon-z9-lab.c'))\n"
for dependency in ('x265','x264'):
    entry=f"arm_ss.add(when: 'CONFIG_NIKON_Z9_LAB', if_true: dependency('{dependency}', required: true))\n"
    if entry not in body:body+=entry
path.write_text(body)
if line not in body:
    body = body.replace("hw_arch += {'arm': arm_ss}", line + "hw_arch += {'arm': arm_ss}")
    path.write_text(body)
jpeg_line = "arm_ss.add(when: 'CONFIG_NIKON_Z9_LAB', if_true: dependency('libjpeg', required: true))\n"
body = path.read_text()
if jpeg_line not in body:
    path.write_text(body.replace("hw_arch += {'arm': arm_ss}",
                                 jpeg_line + "hw_arch += {'arm': arm_ss}"))

# QEMU's TCG GDB removal currently stops at the first CPU without a matching
# breakpoint. A global SMP breakpoint can be absent on one vCPU after a hit,
# while still being installed on another. Remove it from every CPU and report
# success if any copy existed; return ENOENT only when none existed anywhere.
path = root / 'accel/tcg/tcg-accel-ops.c'
body = path.read_text()
old = '\n'.join([
    '    case GDB_BREAKPOINT_SW:',
    '    case GDB_BREAKPOINT_HW:',
    '        CPU_FOREACH(cpu) {',
    '            err = cpu_breakpoint_remove(cpu, addr, BP_GDB);',
    '            if (err) {',
    '                break;',
    '            }',
    '        }',
    '        return err;',
]) + '\n'
new = '\n'.join([
    '    case GDB_BREAKPOINT_SW:',
    '    case GDB_BREAKPOINT_HW: {',
    '        int removed = 0;',
    '',
    '        CPU_FOREACH(cpu) {',
    '            err = cpu_breakpoint_remove(cpu, addr, BP_GDB);',
    '            if (!err) {',
    '                removed++;',
    '            } else if (err != -ENOENT) {',
    '                return err;',
    '            }',
    '        }',
    '        return removed ? 0 : -ENOENT;',
    '    }',
]) + '\n'
if new not in body:
    if body.count(old) != 1:
        raise SystemExit('unexpected TCG breakpoint-removal implementation')
    path.write_text(body.replace(old, new, 1))
