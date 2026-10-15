# SPDX-License-Identifier: GPL-2.0-or-later
"""Authored tiny exFAT/transport fixtures, never decodable camera photographs."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_export_nefs as export

CARD_BYTES, PARTITION, SECTOR = 65536, 512, 512
FAT, HEAP, CLUSTERS = 24, 25, 102


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_entry(name, length, first, *, contiguous=False, valid=None):
    encoded = name.encode('utf-16le')
    if len(encoded) > 30:
        raise ValueError('The small synthetic fixture uses one name entry')
    group = bytearray(96)
    group[0:2] = bytes((0x85, 2))
    struct.pack_into('<H', group, 4, 0x20)
    group[32:36] = bytes((0xc0, 3 if contiguous else 1, 0, len(name)))
    struct.pack_into('<Q', group, 40, length if valid is None else valid)
    struct.pack_into('<I', group, 52, first)
    struct.pack_into('<Q', group, 56, length)
    group[64] = 0xc1
    group[66:66 + len(encoded)] = encoded
    checksum = 0
    for index, value in enumerate(group):
        if index not in (2, 3):
            checksum = (((checksum >> 1) | ((checksum & 1) << 15)) + value) & 0xffff
    struct.pack_into('<H', group, 2, checksum)
    return group


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.output = self.root / 'export'
        self.firmware = self.root / 'authored-program.bin'
        self.firmware.write_bytes(b'authored test program, not Nikon firmware')
        self.card = self.run / 'capture-card.raw'
        self.volume = self.run / 'capture-card-after-volume.raw'
        self.receipt = self.run / 'capture-card.audit.json'
        self.result = self.run / 'result.json'
        self.program = dict(owned_qemu_stopped=True, error=None, input_unchanged=True,
                            cpu_register_redirection=False, ram_patches=[],
                            firmware_sha256=digest(self.firmware.read_bytes()),
                            private_virtual_card=dict(path=str(self.card), bytes=CARD_BYTES,
                                                      format='raw', camera_files_supplied=False))
        self.result.write_text(json.dumps(self.program))
        self.payloads = [bytes(i % 251 for i in range(700)), bytes((i * 3) % 251 for i in range(600))]
        self.write_card()
        self.addCleanup(patch.stopall)
        patch.object(export, 'CARD_BYTES', CARD_BYTES).start()
        self.fsck = patch.object(export, '_fsck', return_value=dict(exit_code=0, output='synthetic fsck transport')).start()

    def write_card(self, names=('A.NEF', 'B.NEF'), *, valid=None, cycle=False, long_chain=False):
        card = bytearray(CARD_BYTES)
        struct.pack_into('<B3sB3sII', card, 446, 0, bytes(3), 7, bytes(3), 1, 127)
        card[510:512] = b'\x55\xaa'
        boot = memoryview(card)[PARTITION:PARTITION + 512]
        boot[3:11] = b'EXFAT   '
        struct.pack_into('<QQ', boot, 64, 1, 127)
        struct.pack_into('<IIIII', boot, 80, FAT, 1, HEAP, CLUSTERS, 2)
        boot[108:111] = bytes((9, 0, 1))
        boot[510:512] = b'\x55\xaa'
        for cluster, next_cluster in ((2, 0xffffffff), (3, 3 if cycle else 7),
                                      (7, 8 if long_chain else 0xffffffff), (8, 9), (9, 0xffffffff)):
            struct.pack_into('<I', card, PARTITION + FAT * SECTOR + cluster * 4, next_cluster)
        root_at = PARTITION + HEAP * SECTOR
        for index, name in enumerate(names):
            content = self.payloads[index]
            group = file_entry(name, len(content), (3, 8)[index], contiguous=index == 1,
                               valid=valid if index == 0 else None)
            card[root_at + index * 96:root_at + (index + 1) * 96] = group
            for part, cluster in enumerate(((3, 7), (8, 9))[index]):
                offset = PARTITION + HEAP * SECTOR + (cluster - 2) * SECTOR
                chunk = content[part * SECTOR:(part + 1) * SECTOR]
                card[offset:offset + len(chunk)] = chunk
        self.card.write_bytes(card)
        self.volume.write_bytes(card[PARTITION:])
        self.receipt.write_text(json.dumps(dict(source_card=str(self.card), derived_volume=str(self.volume),
                                              fsck_exit=0, source_unchanged=True)))

    def perform(self):
        return export.export_run(self.run, self.output, firmware=self.firmware)

    def snapshot(self):
        return {p.name: (digest(p.read_bytes()), p.stat().st_mtime_ns) for p in self.run.iterdir()}

    def replace_bytes(self, offset, data):
        raw = bytearray(self.card.read_bytes())
        raw[offset:offset + len(data)] = data
        self.card.write_bytes(raw)
        self.volume.write_bytes(raw[PARTITION:])

    def test_all_nefs_exported_by_safe_ordinal_with_absolute_card_extents(self):
        before = self.snapshot()
        with patch.object(export, 'audit_card') as audit_card:
            report = self.perform()
        audit_card.assert_not_called()
        self.assertEqual(report['status'], 'exported')
        self.assertFalse(report['native_accepted'])
        self.assertFalse(report['writer_mapping_verified'])
        self.assertEqual(report['output_order_basis'], 'directory_observation_not_writer')
        self.assertEqual(len(report['original_catalog']['files']), 2)
        self.assertEqual(len(report['storage']['nef_files']), 2)
        self.assertTrue(report['storage']['catalog_complete'])
        for index, row in enumerate(report['storage']['nef_files']):
            target = Path(report['audit_base_dir']) / row['path']
            self.assertEqual(target.name, f'file-{index + 1:06}.NEF')
            self.assertEqual(target.read_bytes(), self.payloads[index])
            self.assertEqual(row['output_index'], index)
            self.assertNotIn('job_id', row)
            self.assertNotIn('frame_id', row)
            self.assertEqual(sum(x['length'] for x in row['extents']), len(self.payloads[index]))
        self.assertEqual(report['storage']['nef_files'][0]['extents'],
                         [dict(offset=PARTITION + (HEAP + 1) * SECTOR, length=512),
                          dict(offset=PARTITION + (HEAP + 5) * SECTOR, length=188)])
        self.assertEqual(len(report['storage']['nef_files'][1]['extents']), 1)
        self.assertEqual(before, self.snapshot())

    def test_unstopped_or_changed_program_is_rejected_before_output_creation(self):
        for changes in (dict(owned_qemu_stopped=False), dict(error='failure'),
                        dict(input_unchanged=False), dict(cpu_register_redirection=True), dict(ram_patches=[1])):
            with self.subTest(changes=changes):
                self.result.write_text(json.dumps(dict(self.program, **changes)))
                with self.assertRaises(export.ExportError): self.perform()
                self.assertFalse(self.output.exists())
        self.result.write_text(json.dumps(self.program))
        self.firmware.write_bytes(b'changed program')
        with self.assertRaises(export.ExportError): self.perform()
        self.assertFalse(self.output.exists())

    def test_missing_old_z9_input_flag_still_requires_real_program_digest(self):
        row = dict(self.program); row.pop('input_unchanged')
        self.result.write_text(json.dumps(row))
        report = self.perform()
        self.assertEqual(report['status'], 'exported')
        self.assertTrue(report['firmware_rechecked'])
        self.assertFalse(report['native_accepted'])

    def test_explicit_derived_kernel_identity_is_retained_without_native_acceptance(self):
        row = dict(self.program, executed_program_sha256=digest(b'authored derivative'),
                   patch_provenance={'scope': 'simulation-only fixture'})
        self.result.write_text(json.dumps(row))
        self.firmware.write_bytes(b'authored derivative')
        report = self.perform()
        self.assertEqual(report['status'], 'exported')
        self.assertTrue(report['derived_program'])
        self.assertEqual(report['source_main_sha256'], self.program['firmware_sha256'])
        self.assertEqual(report['program_provenance']['patch_provenance'], row['patch_provenance'])
        self.assertFalse(report['native_accepted'])

    def test_program_change_between_hash_and_preflight_completion_is_rejected(self):
        real_sha = export._sha
        def change_after_hash(path):
            result = real_sha(path)
            if path == self.firmware:
                self.firmware.write_bytes(b'changed after digest, before final preflight')
            return result
        with patch.object(export, '_sha', side_effect=change_after_hash):
            with self.assertRaises(export.ExportError): self.perform()
        self.assertFalse(self.output.exists())

    def test_run_record_change_after_read_is_rejected_before_output_creation(self):
        real_json = export._json
        def change_after_read(path):
            result = real_json(path)
            if path == self.result:
                self.result.write_text(json.dumps(dict(result, altered_after_read=True)))
            return result
        with patch.object(export, '_json', side_effect=change_after_read):
            with self.assertRaises(export.ExportError): self.perform()
        self.assertFalse(self.output.exists())

    def test_existing_output_is_never_replaced(self):
        self.output.mkdir(); sentinel = self.output / 'keep.txt'; sentinel.write_text('keep')
        with self.assertRaises(export.ExportError): self.perform()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_zero_nef_is_explicitly_incomplete_and_keeps_full_catalog(self):
        self.write_card(names=('A.DAT', 'B.BIN'))
        report = self.perform()
        self.assertEqual(report['status'], 'incomplete')
        self.assertFalse(report['native_accepted'])
        self.assertEqual(len(report['original_catalog']['files']), 2)
        self.assertEqual(report['storage']['nef_files'], [])

    def test_bad_initialized_length_preserves_collection_without_partial_success(self):
        self.write_card(valid=12)
        report = self.perform()
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['storage']['catalog_complete'])
        self.assertEqual(len(report['original_catalog']['files']), 2)
        self.assertEqual(len(report['storage']['nef_files']), 2)
        self.assertEqual(list(self.output.glob('*.NEF')), [])

    def test_cyclic_or_overlong_fat_chain_fails_before_any_nef_write(self):
        for name in ('cycle', 'long_chain'):
            with self.subTest(name=name):
                self.write_card(**{name: True})
                self.output = self.root / name
                report = self.perform()
                self.assertEqual(report['status'], 'failed')
                self.assertFalse(report['storage']['catalog_complete'])
                self.assertEqual(list(self.output.glob('*.NEF')), [])

    def test_short_fat_chain_and_cross_file_cluster_ownership_are_rejected(self):
        self.replace_bytes(PARTITION + FAT * SECTOR + 3 * 4, struct.pack('<I', 0xffffffff))
        self.assertEqual(self.perform()['status'], 'failed')
        self.output = self.root / 'overlap'
        self.write_card()
        self.replace_bytes(PARTITION + HEAP * SECTOR + 96, file_entry('B.NEF', 600, 3))
        report = self.perform()
        self.assertEqual(report['issues'][0]['code'], 'cluster_overlap')
        self.assertEqual(list(self.output.glob('*.NEF')), [])

    def test_unknown_active_directory_entry_cannot_be_silently_dropped(self):
        self.replace_bytes(PARTITION + HEAP * SECTOR + 192, b'\x86')
        report = self.perform()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['issues'][0]['code'], 'directory')
        self.assertFalse(report['storage']['catalog_complete'])

    def test_multiple_fats_are_rejected_even_if_mock_fsck_reports_clean(self):
        self.replace_bytes(PARTITION + 110, b'\x02')
        report = self.perform()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['issues'][0]['code'], 'geometry')

    def test_traversal_and_case_insensitive_duplicate_card_names_are_rejected(self):
        for names in (('..\\A.NEF', 'B.NEF'), ('A.NEF', 'a.nef')):
            with self.subTest(names=names):
                self.write_card(names=names)
                self.output = self.root / ('names-' + str(len(list(self.root.iterdir()))))
                report = self.perform()
                self.assertEqual(report['status'], 'failed')
                self.assertFalse(report['storage']['catalog_complete'])
                self.assertEqual(len(report['original_catalog']['files']), 2)
                self.assertEqual(list(self.output.glob('*.NEF')), [])

    def test_truncated_card_or_wrong_partition_length_is_rejected(self):
        with self.card.open('r+b') as stream: stream.truncate(CARD_BYTES - 1)
        with self.assertRaises(export.ExportError): self.perform()
        self.write_card()
        with self.card.open('r+b') as stream:
            stream.seek(458); stream.write(struct.pack('<I', 0xffffffff))
        with self.assertRaises(export.ExportError): self.perform()

    def test_stale_audit_volume_cannot_hide_new_card_directory_entries(self):
        before = self.snapshot()
        with self.card.open('r+b') as stream:
            stream.seek(PARTITION + HEAP * SECTOR + 192); stream.write(b'\x85')
        changed = self.card.read_bytes()
        report = self.perform()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['issues'][0]['code'], 'partition_mismatch')
        self.assertEqual(self.card.read_bytes(), changed)
        self.assertEqual(before[self.receipt.name], self.snapshot()[self.receipt.name])

    def test_boolean_fsck_success_is_not_integer_zero(self):
        row = json.loads(self.receipt.read_text()); row['fsck_exit'] = False
        self.receipt.write_text(json.dumps(row))
        report = self.perform()
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['native_accepted'])

    def test_fresh_fsck_failure_cannot_reuse_old_success(self):
        self.fsck.return_value = dict(exit_code=4, output='synthetic corruption')
        report = self.perform()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['issues'][0]['code'], 'filesystem')

    def test_cli_zero_files_returns_incomplete_and_does_not_touch_readonly_inputs(self):
        self.write_card(names=('A.DAT', 'B.BIN'))
        before = self.snapshot()
        self.card.chmod(0o444)
        try:
            with redirect_stdout(io.StringIO()):
                result = export.main(['--run', str(self.run), '--output', str(self.output),
                                      '--firmware', str(self.firmware)])
            self.assertEqual(result, 2)
            self.assertEqual(before, self.snapshot())
        finally:
            self.card.chmod(0o644)

    def test_disappearing_input_still_leaves_failed_collection_receipt(self):
        real_extract = export.extract_file
        def remove_card(volume, name, target):
            receipt = real_extract(volume, name, target)
            self.card.unlink()
            return receipt
        with patch.object(export, 'extract_file', side_effect=remove_card):
            report = self.perform()
        self.assertEqual(report['status'], 'failed')
        self.assertTrue((self.output / 'export-manifest.json').exists())
        self.assertFalse(report['storage']['catalog_complete'])

    def test_shared_audit_without_receipt_writes_only_in_fresh_output(self):
        self.receipt.unlink(); self.volume.unlink()
        before = self.snapshot()
        def audit_copy(card):
            self.assertEqual(card.parent, self.output)
            self.assertEqual(card.read_bytes(), self.card.read_bytes())
            volume = card.with_name(card.stem + '-after-volume.raw')
            volume.write_bytes(card.read_bytes()[PARTITION:])
            receipt = dict(source_card=str(card), derived_volume=str(volume), fsck_exit=0, source_unchanged=True)
            card.with_suffix('.audit.json').write_text(json.dumps(receipt))
            return receipt
        with patch.object(export, 'audit_card', side_effect=audit_copy) as called:
            report = self.perform()
        called.assert_called_once()
        self.assertEqual(report['status'], 'exported')
        self.assertEqual(before, self.snapshot())

    def test_exporter_checks_shared_extract_bytes_instead_of_trusting_receipt(self):
        real_extract = export.extract_file
        def corrupt(volume, name, target):
            result = real_extract(volume, name, target)
            target.write_bytes(bytes(result['bytes']))
            return result
        with patch.object(export, 'extract_file', side_effect=corrupt):
            report = self.perform()
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['storage']['catalog_complete'])
        self.assertFalse(report['native_accepted'])
        self.assertEqual(len(report['storage']['nef_files']), 2)


if __name__ == '__main__':
    unittest.main()
