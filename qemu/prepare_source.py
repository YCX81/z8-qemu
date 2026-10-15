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
# Reuse the bounded PCIe aperture fix and passive NVMe completion evidence.
# Every patch is verified in both fresh and already-applied build caches.
for name in ('designware-dma.patch','nvme-completion-trace.patch','nvme-audit-state.patch'):
    command = ['patch','--batch','--fuzz=0','-p1','-i',str(here/name)]
    if subprocess.run(command+['--forward','--dry-run'],cwd=root,
                      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
        subprocess.run(command+['--forward'],cwd=root,check=True)
    elif subprocess.run(command+['--reverse','--dry-run'],cwd=root,
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0:
        raise SystemExit('Unexpected PCIe/NVMe source: '+name)
shutil.copyfile(here / 'nikon-z9-lab.c', root / 'hw/arm/nikon-z9-lab.c')
shutil.copyfile(here / 'nikon-raw-pixels.h', root / 'hw/arm/nikon-raw-pixels.h')
shutil.copyfile(here / 'nikon-z9-pixels.h', root / 'hw/arm/nikon-z9-pixels.h')
shutil.copyfile(here / 'nikon-z8-video-ring.h', root / 'hw/arm/nikon-z8-video-ring.h')
shutil.copyfile(here / 'nikon-z8-input-pair.h', root / 'hw/arm/nikon-z8-input-pair.h')
shutil.copyfile(here / 'nikon-z8-video-state.h', root / 'hw/arm/nikon-z8-video-state.h')
for name in ('nikon-z8-video-c1.h', 'nikon-z8-video-c1-types.h', 'nikon-bayer-packet.h'):
    shutil.copyfile(here / name, root / 'hw/arm' / name)
for name in ('nikon-video-plane.h', 'nikon-z8-video-fourth.h', 'nikon-z8-video-processing.h',
             'nikon-z9-wave5.h', 'nikon-wave5-codec.h'):
    shutil.copyfile(here / name, root / 'hw/arm' / name)
for name in ('nikon-z9-pcie.h','nikon-z8-pcie-config.h'):
    shutil.copyfile(here/name,root/'hw/arm'/name)
shutil.copyfile(here / 'nikon-persistent-media.h', root / 'hw/arm/nikon-persistent-media.h')
for filename, marker, addition in [
    ('hw/arm/Kconfig', 'config NIKON_Z9_LAB',
     '\nconfig NIKON_Z9_LAB\n    bool\n    depends on AARCH64\n    select ARM_GIC\n'),
    ('configs/devices/aarch64-softmmu/default.mak', 'CONFIG_NIKON_Z9_LAB=y',
     '\nCONFIG_NIKON_Z9_LAB=y\n'),
    # Separate, explicitly named diagnostic ROM data images. The minimal build
    # otherwise omits QEMU's loader device; no board code/CPU patch is needed.
    ('configs/devices/aarch64-softmmu/default.mak', 'CONFIG_GENERIC_LOADER=y',
     '\nCONFIG_GENERIC_LOADER=y\n'),
]:
    path = root / filename
    body = path.read_text()
    if marker not in body:
        path.write_text(body + addition)
# Minimal builds need these explicit upstream device/link dependencies.
path = root / 'hw/arm/Kconfig'
body = path.read_text()
block = body[body.index('config NIKON_Z9_LAB'):].split('\nconfig ',1)[0]
if '    select PCI_EXPRESS_DESIGNWARE' not in block:
    path.write_text(body.replace('config NIKON_Z9_LAB\n',
        'config NIKON_Z9_LAB\n    select PCI_EXPRESS_DESIGNWARE\n    select NVME_PCI\n    select SPDM_SOCKET\n',1))
path = root / 'hw/arm/meson.build'
body = path.read_text()
line = "arm_ss.add(when: 'CONFIG_NIKON_Z9_LAB', if_true: files('nikon-z9-lab.c'))\n"
if line not in body:
    body = body.replace("hw_arch += {'arm': arm_ss}", line + "hw_arch += {'arm': arm_ss}")
    path.write_text(body)

# Public functional HEVC/AVC backends, used only with the explicit diagnostic option.
for library in ('x265', 'x264'):
    line = f"arm_ss.add(when: 'CONFIG_NIKON_Z9_LAB', if_true: dependency('{library}', required: true))\n"
    body = path.read_text()
    if line not in body:
        path.write_text(body.replace("hw_arch += {'arm': arm_ss}", line + "hw_arch += {'arm': arm_ss}"))

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
