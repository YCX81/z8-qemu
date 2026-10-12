# SPDX-License-Identifier: GPL-2.0-or-later
"""Check the explicitly reviewed source inventory and every reachable Git blob.

This is a guard against unintended additions, not a license/copyright oracle.
--refresh-manifest is for maintainers after manual content/provenance review.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'publication-manifest.json'
FILES = {'tools/service_z9_mcu.py', 'docs/build.md', 'tools/z9_primary_gate_input.py', 'qemu/nikon-raw-pixels.h', 'tools/mcu_frames.py', 'tools/probe.py', 'docs/firmware.md', 'tools/observe_serial_channel.py', 'tools/interactive.html', 'tools/smoke_qemu.py', 'tools/init_table_input.py', 'tools/z9_global1_boundary_input.py', 'tools/guest.py', 'tools/probe_adc_capture.py', 'tools/probe_tasks.py', 'tools/receive_mcu_tx.py', 'tools/service_serial8.py', 'tools/z9_processing_index_input.py', 'tools/make_cmos.py', 'requirements.txt', 'docs/publication.md', 'docs/testing.md', 'tools/observe_z9_state8.py', 'docs/research-notes.md', '.gitattributes', 'tools/parameter_input.py', 'tools/guest_transport.py', 'tools/audit_publication.py', 'docs/usage.md', 'NOTICE.md', 'tools/deliver_mcu_frame.py', 'LICENSES/QEMU-LICENSE.txt', 'README.md', 'README.zh-CN.md', 'tools/boot.py', 'tools/model-properties.json', 'tools/run.py', 'qemu/prepare_source.py', '.github/workflows/ci.yml', 'COPYING', 'tests/test_inputs.py', 'docs/debugging.md', 'tools/build.py', 'tools/probe_graphics_image.py', 'qemu/nikon-z9-lab.c', 'tools/z9_sequence_resource_input.py', '.gitignore', 'tests/test_pixels.c', 'docs/architecture.md', 'CONTRIBUTING.md', 'tests/run.sh', 'qemu/nikon-z9-pixels.h'}
FILES.add('docs/TODO.md')
IGNORED = {'.git','.local','.build','.venv','__pycache__'}
FILES.add('tools/validate_run.py')
FILES.update({'qemu/arm-service-events.patch','tools/z9_timing_input.py',
              'tools/check_service_events.py','docs/service-events.md'})
FILES.update({'profiles/z8-c311.json', 'tools/z8_bringup.py', 'tools/z8_elf.py',
              'tests/test_z8_bringup.py', 'tests/test_z8_elf.py',
              'tests/check_z8_machine.py', 'docs/z8-status.md'})
FILES.update({'profiles/z8-c311-bindings.json', 'tools/z8_boot.py', 'tools/z8_guest.py',
              'tools/z8_resources.py', 'tools/z8_run.py', 'tools/z8-interactive.html',
              'tools/z8-interactive.js', 'tests/test_z8_boot.py', 'tests/test_z8_guest.py',
              'tests/test_z8_resources.py', 'tests/test_z8_run.py', 'tests/test_z8_syscon.py',
              'tests/test_z8_storage_control.py'})
FILES.update({'profiles/z8-c311-acceptance.json','docs/z8-battery-resources.md',
              'tests/test_z8_cancellation.py'})
FILES.update({'Start-Z8.ps1','Test-Z8.ps1','REA.ps1'})
FILES.update({'tools/z8_properties.py', 'profiles/z8-model-properties.json',
              'tests/test_z8_properties.py', 'docs/z8-parameters.md', 'tests/test_build.py'})
SECRET_PATTERNS = [
    re.compile(r'gh[opusr]_[A-Za-z0-9]{30,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{30,}'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
]

def validate(path, data):
    if path not in FILES | {MANIFEST}: raise ValueError('Unreviewed public path: '+path)
    if len(data)>(640*1024 if path == 'qemu/nikon-z9-lab.c' else 512*1024) or b'\0' in data: raise ValueError('Unexpected binary/large content: '+path)
    text = data.decode('utf-8')
    if any(pattern.search(text) for pattern in SECRET_PATTERNS): raise ValueError('Credential pattern in '+path)
    if re.search(r"['\"][0-9a-fA-F]{512,}['\"]",text): raise ValueError('Large embedded hex data needs review: '+path)
    if path.endswith(('.py','.c','.h','.sh')) and 'SPDX-License-Identifier: GPL-2.0-or-later' not in text[:200]:
        raise ValueError('Missing source license: '+path)
    return hashlib.sha256(data).hexdigest()

def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--refresh-manifest',action='store_true')
    a = p.parse_args()
    discovered = set()
    for directory,dirs,files in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in IGNORED]
        for name in files:
            path = Path(directory)/name
            if path.is_symlink(): raise ValueError('Symlink in public tree: '+str(path))
            discovered.add(path.relative_to(ROOT).as_posix())
    if discovered - {MANIFEST} != FILES:
        raise ValueError('Inventory differs: '+str({'extra':sorted(discovered-FILES-{MANIFEST}),'missing':sorted(FILES-discovered)}))
    hashes = {name:validate(name,(ROOT/name).read_bytes()) for name in sorted(FILES)}
    if a.refresh_manifest:
        (ROOT/MANIFEST).write_text(json.dumps({'schema':1,'scope':'reviewed source-only publication inventory; no runtime artifacts','files':hashes},indent=2)+'\n',encoding='utf-8',newline='\n')
    manifest = json.loads((ROOT/MANIFEST).read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or manifest.get('files') != hashes: raise ValueError('Content changed since reviewed manifest')
    validate(MANIFEST,(ROOT/MANIFEST).read_bytes())
    commits = 0; blobs = set()
    if (ROOT/'.git').exists():
        # Inspect all refs, rather than only the currently visible checkout.
        refs = git('rev-list','--all').decode().splitlines()
        commits = len(refs)
        for ref in refs:
            tree = git('ls-tree','-rz','--full-tree',ref).split(b'\0')
            for row in tree:
                if not row: continue
                meta,name = row.split(b'\t',1)
                mode,kind,oid = meta.decode().split()
                name = name.decode('utf-8')
                if kind != 'blob' or mode not in ('100644','100755'): raise ValueError('Unexpected history object: '+name)
                if (oid,name) not in blobs:
                    validate(name,git('cat-file','blob',oid)); blobs.add((oid,name))
        tracked = set()
        for row in git('ls-files','--stage','-z').split(b'\0'):
            if not row: continue
            meta,name = row.split(b'\t',1)
            mode,oid,stage = meta.decode().split()
            name = name.decode('utf-8')
            if stage != '0' or mode not in ('100644','100755'):
                raise ValueError('Unexpected index entry: '+name)
            staged_hash = validate(name,git('cat-file','blob',oid))
            if staged_hash != hashlib.sha256((ROOT/name).read_bytes()).hexdigest():
                raise ValueError('Staged content differs from reviewed worktree: '+name)
            tracked.add(name)
        if tracked != FILES | {MANIFEST}: raise ValueError('Git index differs from reviewed inventory')
    print(json.dumps({'passed':True,'reviewed_files':len(hashes)+1,'history_commits':commits,'history_blobs':len(blobs),'runtime_data_excluded':True}))

if __name__ == '__main__': main()
