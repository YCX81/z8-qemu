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
FILES.update({'tests/test_z8_d980_internal.py', 'docs/z8-d980-internal.md'})
FILES.add('docs/z8-raw-runtime.md')
FILES.add('docs/verification/raw-single-20261012.json')
FILES.update({'tests/test_z8_serial5_config.py', 'docs/z8-serial5-config.md'})
FILES.update({'profiles/z8-capture-properties.json', 'tools/z8_capture_probe.py',
              'tests/test_z8_capture_probe.py', 'tools/z8_hsfc_controls.py',
              'tests/test_z8_hsfc_controls.py', 'docs/z8-capture-probe.md'})
FILES.add('tests/test_z8_display_records.py')
FILES.update({'qemu/nvme-completion-trace.patch', 'qemu/nvme-audit-state.patch',
              'tests/test_z8_nvme_trace.py', 'docs/z8-nvme-evidence.md'})
FILES.update({'tools/z8_nvme_evidence.py', 'tests/test_z8_nvme_evidence.py'})
FILES.update({'tools/z8_capture_resources.py', 'tests/test_z8_capture_resources.py',
              'docs/z8-capture-resources.md'})
FILES.update({'tools/z8_capture_pixels.py', 'tools/z8_export_nefs.py',
              'tests/test_z8_capture_pixels.py', 'tests/test_z8_export_nefs.py',
              'docs/z8-capture-evidence.md'})
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
FILES.update({'requirements-c10.txt', 'docs/z8-c10.md',
              'docs/z8-persistent-media.md', 'docs/z8-raw-precapture-chain.md',
              'qemu/nikon-persistent-media.h', 'tools/z8_native_call.py',
              'tools/z8_picture_control.py', 'tools/z8_picture_control_menu.py',
              'tools/z8_picture_control_labels.py', 'tools/z8_c10_image.py',
              'tools/z8_c10_media.py', 'tools/z8_c10_gui.py',
              'tools/z8_c10_acceptance.py', 'tests/test_z8_native_call.py',
              'tests/test_z8_picture_control.py', 'tests/test_z8_picture_control_menu.py',
              'tests/test_z8_picture_control_labels.py', 'tests/test_z8_c10_image.py',
              'tests/test_z8_c10_media.py', 'tests/test_z8_c10_gui.py',
              'tests/test_z8_c10_acceptance.py', 'tests/test_z8_persistent_media.py'})
FILES.update({'tools/z8_c10_memory.py', 'tests/test_z8_c10_memory.py'})
FILES.update({'tools/z8_task_context.py', 'tests/test_z8_task_context.py',
              'tools/z8_picture_control_list.py', 'tests/test_z8_picture_control_list.py',
              'tests/test_z8_graphics_empty.py'})
FILES.update({'tools/z8_picture_control_main_menu.py', 'tests/test_z8_picture_control_main_menu.py'})
FILES.update({'tools/z8_c10_menu_acceptance.py', 'tests/test_z8_c10_menu_acceptance.py'})
FILES.update({'tools/z8_properties.py', 'profiles/z8-model-properties.json',
              'tests/test_z8_properties.py', 'docs/z8-parameters.md'})
FILES.update({'tests/test_build.py', 'docs/research-progress.md'})
FILES.update({'docs/z8-raw-next.md', 'docs/verification/slots-20261012.json'})
FILES.update({'docs/z8-c32.md', 'tools/z8_slots_acceptance.py',
              'tools/z8_slots_core.py', 'tools/z8_slots_image.py',
              'tools/z8_slots_imenu.py', 'tools/z8_slots_labels.py',
              'tools/z8_slots_layout.py', 'tools/z8_slots_main_menu.py',
              'tools/z8_slots_mapping.py', 'tools/z8_slots_media.py',
              'tests/test_z8_slots_core.py', 'tests/test_z8_slots_gui.py',
              'tests/test_z8_slots_image.py', 'tests/test_z8_slots_layout.py',
              'tests/test_z8_slots_main_menu.py', 'tests/test_z8_slots_media.py'})
FILES.update({'requirements-capture.txt', 'docs/z8-raw-foundation.md',
              'qemu/designware-dma.patch', 'qemu/nikon-sensor-tag.h',
              'qemu/nikon-z9-pcie.h', 'qemu/nikon-z9-d980.h',
              'qemu/nikon-z9-jpeg.h', 'qemu/nikon-z9-capture-nv16.h',
              'qemu/nikon-z9-thumbnail-nv16.h', 'tests/test_sensor_tag.c',
              'tests/test_capture_contracts.py', 'tools/virtual_recording_card.py',
              'tools/ccomp_command_peer.py', 'tools/capture_workflow_ready.py',
              'tools/inspect_virtual_exfat.py', 'tools/extract_virtual_exfat.py',
              'tools/private_bounded_qmp.py', 'tools/rtc_mcu_input.py',
              'profiles/z8-c311-capture.json', 'tools/z8_capture_bindings.py',
              'tools/z8_capture_readiness.py', 'tests/test_z8_capture_bindings.py',
              'tests/test_z8_capture_readiness.py', 'tools/z8_precapture_audit.py',
              'tests/test_z8_precapture_audit.py', 'docs/z8-precapture-audit.md'})
FILES.update({'docs/z8-primary-f04f-stop.md', 'tests/test_z8_f04f_stop.py'})
FILES.update({'docs/z8-1543-c0-stop.md', 'tests/test_z8_c0_stop.py'})
FILES.update({'docs/z8-writer-observer.md', 'tools/z8_writer_observer.py',
              'tools/z8_writer_integration.py', 'tools/z8_writer_code_reader.py',
              'tools/z8_writer_code_policy.py', 'tests/test_z8_writer_observer.py',
              'tests/test_z8_writer_integration.py', 'tests/test_z8_writer_code_reader.py',
              'tests/test_z8_writer_finalize.py', 'tests/test_z8_writer_capture_probe.py',
              'tests/test_z8_writer_options.py'})
FILES.update({'docs/z8-producer-journal.md', 'qemu/nikon-producer-journal.h',
              'qemu/nikon-producer-journal-qemu.h', 'tests/test_producer_journal.c',
              'tests/test_producer_journal_adapter.c', 'tests/test_producer_journal_helpers.py',
              'tests/test_producer_journal_contract.py', 'tests/test_z8_producer_journal.py'})
FILES.update({'docs/z8-d980-internal-wide.md', 'tests/test_z8_d980_wide_internal.py'})
FILES.update({'docs/z8-photo-audit.md','profiles/z8-lossless14-properties.json','tools/z8_photo_sequence.py','tools/z8_photo_audit.py',
    'tools/z8_nef_codec.py','qemu/nikon-buffer-ownership.h','qemu/nikon-expeed-lossless.h',
    'tests/test_buffer_ownership.c','tests/test_buffer_ownership_helpers.py',
    'tests/test_z8_photo_sequence.py','tests/test_z8_photo_audit.py','tests/test_z8_nef_codec.py',
    'tests/test_lossless14.py','tests/test_z8_buffer_ownership.py','tests/test_z8_lossless14.py'})
FILES.update({'docs/z8-experiment-assumptions.md', 'docs/verification/raw-research-log-20261013.md', 'docs/z8-model-split-plan.md', 'docs/z8-public-review-20261013.md'})
FILES.update({'docs/publication-checkpoint-20261015.md'})
FILES.add('docs/publication-progress-20261015.md')
FILES.update({
    'research/e7/README.md',
    'research/e7/README.zh-CN.md',
    'research/e7/animal_channels.py',
    'research/e7/docs/model-benchmark-20261022.zh-CN.txt',
    'research/e7/docs/peer-benchmark-20261022.zh-CN.txt',
    'research/e7/docs/resource-identities.json',
    'research/e7/docs/status.md',
    'research/e7/e7_inputs.py',
    'research/e7/external-reference.json',
    'research/e7/neupro_forward.py',
    'research/e7/parse_models.py',
    'research/e7/pcnn_inventory.py',
    'research/e7/pcnn_packets.py',
    'research/e7/pcnn_replay.py',
    'research/e7/requirements-replay.txt',
    'research/e7/requirements.txt',
    'research/e7/source-provenance.json',
    'research/e7/tests/test_animal_channels.py',
    'research/e7/tests/test_e7_inputs.py',
    'research/e7/tests/test_neupro_arithmetic.py',
    'research/e7/tests/test_parse_models.py',
    'research/e7/tests/test_public_contracts.py',
})
SECRET_PATTERNS = [
    re.compile(r'gh[opusr]_[A-Za-z0-9]{30,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{30,}'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
]

def validate(path, data):
    # Hash canonical Git text without rewriting CRLF source working copies.
    data = data.replace(b'\r\n', b'\n')
    if path not in FILES | {MANIFEST}: raise ValueError('Unreviewed public path: '+path)
    # Reviewed functional capture/peripheral code grows this one model. All
    # other files retain the 512-KiB ceiling; binary/hex guards still apply.
    if len(data)>(896*1024 if path == 'qemu/nikon-z9-lab.c' else 512*1024) or b'\0' in data: raise ValueError('Unexpected binary/large content: '+path)
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
            if name == '.git': continue  # Linked worktrees use a metadata file.
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
        # Inspect this publication and its ancestors; audit divergent branches separately.
        refs = git('rev-list','HEAD').decode().splitlines()
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
            if staged_hash != validate(name,(ROOT/name).read_bytes()):
                raise ValueError('Staged content differs from reviewed worktree: '+name)
            tracked.add(name)
        if tracked != FILES | {MANIFEST}: raise ValueError('Git index differs from reviewed inventory')
    print(json.dumps({'passed':True,'reviewed_files':len(hashes)+1,'history_commits':commits,'history_blobs':len(blobs),'runtime_data_excluded':True}))

if __name__ == '__main__': main()
