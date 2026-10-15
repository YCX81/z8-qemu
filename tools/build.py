# SPDX-License-Identifier: GPL-2.0-or-later
"""Fetch verified upstream QEMU, apply the source overlay, and build AArch64 TCG."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '10.2.4'
SHA256 = '821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746'
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_DEADLINE_SECONDS = 120
DOWNLOAD_ATTEMPTS = 3
SOURCES = ['qemu/nikon-z9-lab.c','qemu/nikon-z9-pixels.h','qemu/nikon-raw-pixels.h','qemu/prepare_source.py']
SOURCES.append('qemu/arm-service-events.patch')
SOURCES.append('qemu/nikon-persistent-media.h')
<<<<<<< current
SOURCES.append('qemu/nikon-sensor-tag.h')
<<<<<<< current
SOURCES.append('qemu/nikon-buffer-ownership.h')
SOURCES.append('qemu/nikon-expeed-lossless.h')
=======
SOURCES.append('qemu/nikon-expeed-lossless.h')
SOURCES.extend(['qemu/nikon-z8-lossless-u16.h', 'qemu/nikon-z8-hsfc-encode.h'])
SOURCES.extend(['qemu/nikon-bayer-packet.h', 'qemu/nikon-z8-hsfc-pixels.h', 'qemu/nikon-z8-hsfc-decode.h'])
>>>>>>> incoming
SOURCES.extend(['qemu/nikon-producer-journal.h','qemu/nikon-producer-journal-qemu.h'])
SOURCES.append('qemu/designware-dma.patch')
SOURCES.append('qemu/nvme-completion-trace.patch')
SOURCES.append('qemu/nvme-audit-state.patch')
SOURCES.extend('qemu/' + name for name in (
    'nikon-z9-pcie.h', 'nikon-z9-d980.h', 'nikon-z9-jpeg.h',
    'nikon-z9-capture-nv16.h', 'nikon-z9-thumbnail-nv16.h'))
=======
SOURCES.extend('qemu/'+name for name in (
    'nikon-z8-video-ring.h','nikon-z8-input-pair.h','nikon-z8-video-data.h',
    'nikon-z8-video-processing.h','nikon-z8-wave5.h','nikon-z9-wave5.h','nikon-wave5-codec.h','nikon-z8-yuv.h','nikon-z8-yuv-wave5.h','nikon-z8-yuv-dma.h','nikon-z8-yuv-single.h',
    'nikon-z9-pcie.h','nikon-z8-pcie-config.h','designware-dma.patch','nvme-audit-state.patch','nvme-completion-trace.patch'))
>>>>>>> incoming

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(1024*1024): h.update(chunk)
    return h.hexdigest()


def verified_archive(work, supplied=None):
    """Never use an unverified cache or replace an explicit user archive."""
    archive = supplied.resolve() if supplied else work / f'qemu-{VERSION}.tar.xz'
    if archive.exists() and digest(archive) == SHA256:
        return archive
    if supplied:
        if not archive.exists():
            raise FileNotFoundError('Requested archive does not exist')
        raise RuntimeError('Upstream QEMU archive SHA-256 mismatch')
    url = f'https://download.qemu.org/qemu-{VERSION}.tar.xz'
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        print(f'Downloading original upstream QEMU ({attempt}/{DOWNLOAD_ATTEMPTS})', flush=True)
        temporary = None
        try:
            # Keep a previous cache untouched until a complete replacement is verified.
            with tempfile.NamedTemporaryFile(dir=archive.parent, prefix=archive.name + '.',
                                             suffix='.part', delete=False) as output:
                temporary = Path(output.name)
                deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
                with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if time.monotonic() > deadline:
                            raise TimeoutError('QEMU archive download exceeded its time budget')
                        if not chunk:
                            break
                        output.write(chunk)
            if digest(temporary) != SHA256:
                raise RuntimeError('Upstream QEMU archive SHA-256 mismatch')
            temporary.replace(archive)
            return archive
        except (OSError, RuntimeError) as exc:
            if attempt == DOWNLOAD_ATTEMPTS:
                raise RuntimeError(f'Unable to obtain verified QEMU archive after {attempt} attempts') from exc
            time.sleep(attempt)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir',type=Path,default=ROOT/'.build')
    p.add_argument('--archive',type=Path,help='Optional pre-downloaded original upstream tarball')
    p.add_argument('--jobs',type=int,default=4)
    a = p.parse_args()
    if os.name == 'nt': p.error('Build in Linux or an explicitly selected WSL distribution')
    if not 1 <= a.jobs <= 64: p.error('--jobs must be 1..64')
    work = a.work_dir.resolve()
    work.mkdir(parents=True,exist_ok=True)
    archive = verified_archive(work, a.archive)
    source = work/f'qemu-{VERSION}'
    marker = source/'.wonderlantern-extracted'
    if not marker.exists() or marker.read_text().strip() != SHA256:
        def source_filter(member, destination):
            # Upstream EDK2 contains host-header links for a separate ROM build.
            # They are unnecessary for this ARM emulator; never extract links
            # pointing outside the verified source archive's destination.
            try:
                return tarfile.data_filter(member, destination)
            except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError):
                return None
        with tarfile.open(archive,'r:xz') as t: t.extractall(work,filter=source_filter)
        marker.write_text(SHA256+'\n')
    subprocess.run(['python3',str(ROOT/'qemu/prepare_source.py'),str(source)],check=True)
    build = work/'build'
    build.mkdir(exist_ok=True)
    options = ['--target-list=aarch64-softmmu','--without-default-devices','--disable-docs','--disable-tools','--disable-user','--disable-werror','--disable-gtk','--disable-sdl','--disable-vnc','--disable-opengl','--disable-guest-agent','--disable-slirp','--disable-rust','--disable-debug-info']
    if not (build/'build.ninja').exists(): subprocess.run([str(source/'configure'),*options],cwd=build,check=True)
    subprocess.run(['ninja','-j',str(a.jobs),'qemu-system-aarch64'],cwd=build,check=True)
    binary = build/'qemu-system-aarch64'
    manifest = dict(qemu_version=VERSION,upstream_archive_sha256=SHA256,binary=str(binary),binary_sha256=digest(binary),sources={name:digest(ROOT/name) for name in SOURCES},configure=options)
    destination = ROOT/'.local/build-manifest.json'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    subprocess.run([str(binary),'--version'],check=True)
    print('Build manifest: '+str(destination),flush=True)

if __name__ == '__main__': main()
