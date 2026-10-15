# SPDX-License-Identifier: GPL-2.0-or-later
"""SYNTHETIC metadata fixtures, not decodable camera output or Photos proof."""
import hashlib
import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_live_photo_pair_audit as audit

IDENT = '12345678-1234-1234-1234-123456789abc'
CONTENT = b'com.apple.quicktime.content.identifier'
STILL = b'com.apple.quicktime.still-image-time'


def u32(*values):
    return struct.pack('>' + 'I' * len(values), *values)


def box(kind, data=b''):
    return u32(len(data) + 8) + kind + data


def full(kind, data):
    return box(kind, bytes(4) + data)


def jpeg(identifier=IDENT, nikon=False):
    # Real TIFF offsets; image payload deliberately absent (metadata unit fixture).
    if nikon:
        note = b'Nikon\0\x02\x10\0\0II*\0\x08\0\0\0\x00\0' + bytes(4)
    else:
        value = identifier.encode() + b'\0'
        note = (b'Apple iOS\0\0\x01MM' + struct.pack('>H', 1) +
                struct.pack('>HHII', 17, 2, len(value), 32) + bytes(4) + value)
    tiff = (b'II*\0\x08\0\0\0' + struct.pack('<H', 1) +
            struct.pack('<HHII', 0x8769, 4, 1, 26) + bytes(4) +
            struct.pack('<H', 1) + struct.pack('<HHII', 0x927c, 7, len(note), 44) +
            bytes(4) + note)
    payload = b'Exif\0\0' + tiff
    return b'\xff\xd8\xff\xe1' + struct.pack('>H', len(payload) + 2) + payload + b'\xff\xd9'


def movie(identifier=IDENT, still=True, marker=-1, offset=1500, frames=90,
          delta=100, sample=True, key=STILL, rate=1, duplicate=False, iso=False, cdsc=1):
    meta = (full if iso else box)(b'meta', full(b'hdlr', bytes(4) + b'mdta' + bytes(12)) +
                full(b'keys', u32(1) + box(b'mdta', CONTENT)) +
                box(b'ilst', box(u32(1), box(b'data', u32(1, 0) + identifier.encode()))))
    metadata_sample = box(u32(1), struct.pack('b', marker))
    data = box(b'ftyp', b'qt  ' + bytes(4) + b'qt  ')
    start = len(data) + 8
    data += box(b'mdat', b'v' * frames + metadata_sample)

    def track(video):
        count = frames if video else 1
        duration = frames * delta if video else 100
        desc = box(b'avc1', bytes(6) + b'\0\1' + bytes(70)) if video else box(
            b'mebx', bytes(6) + b'\0\1' + box(b'keys', box(u32(1),
                box(b'keyd', b'mdta' + key) + box(b'dtyp', u32(0, 65)))))
        stbl = (full(b'stsd', u32(1) + desc) + full(b'stts', u32(1, count, delta if video else 100)) +
                full(b'stsc', u32(1, 1, count, 1)) + full(b'stsz', u32(1 if video else len(metadata_sample), count)) +
                full(b'stco', u32(1, start if video else start + frames if sample else 0)))
        mdia = full(b'mdhd', u32(0, 0, 3000, duration) + bytes(4))
        mdia += full(b'hdlr', bytes(4) + (b'vide' if video else b'meta') + bytes(12))
        mdia += box(b'minf', box(b'dinf', full(b'dref', u32(1) + box(b'url ', u32(1)))) + box(b'stbl', stbl))
        edit = b'' if video else box(b'edts', full(b'elst', u32(2) +
            struct.pack('>IiHH', offset, -1, 1, 0) + struct.pack('>IiHH', 33, 0, rate, 0)))
        tkhd = full(b'tkhd', u32(0, 0, 1 if video else 2))
        refs = box(b'tref', box(b'cdsc', u32(cdsc))) if not video and cdsc is not None else b''
        return box(b'trak', tkhd + refs + edit + box(b'mdia', mdia))
    tracks = track(True) + (track(False) if still else b'')
    if duplicate:
        tracks += track(False)
    return data + box(b'moov', full(b'mvhd', u32(0, 0, 1000, 3000) + bytes(80)) + meta + tracks)


class PairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'DCIM' / '100NZ8LP'
        self.folder.mkdir(parents=True)
        self.photo = self.folder / 'LVP_0001.JPG'
        self.mov = self.folder / 'LVP_0001.MOV'
        self.original = self.folder / 'DSC_0001.JPG'
        self.photo.write_bytes(jpeg())
        self.mov.write_bytes(movie())
        self.original.write_bytes(jpeg(nikon=True))
        self.digest = hashlib.sha256(self.original.read_bytes()).hexdigest()

    def run_audit(self, **kwargs):
        return audit.audit_pair(self.photo, self.mov, nikon_original=self.original,
                                nikon_sha256=self.digest, **kwargs)

    def test_valid_structure_not_camera_acceptance(self):
        r = self.run_audit()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['movie']['still_time_seconds'], 1.5)
        self.assertEqual(r['movie']['fps'], 30)
        self.assertFalse(r['camera_verified'])
        self.assertFalse(r['photos_import_verified'])
        self.assertEqual(r['nikon']['location'], 'separate_original')

    def test_native_structure_required(self):
        for kwargs in ({'iso': True}, {'marker': 0}, {'cdsc': None}, {'cdsc': 2}, {'cdsc': 99}):
            with self.subTest(kwargs=kwargs):
                self.mov.write_bytes(movie(**kwargs))
                self.assertFalse(self.run_audit()['ok'])

    def test_diagnostic_is_redacted_and_not_acceptance(self):
        r = audit.audit_pair(self.photo, self.mov, apple_structure_only=True)
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['diagnostic_mode'], 'apple_structure_only')
        self.assertFalse(r['z8_acceptance_eligible'])
        self.assertNotIn(IDENT, json.dumps(r))
        self.assertNotIn('nikon', r)

    def test_self_contained_alias_and_external_rejection(self):
        self.mov.write_bytes(movie().replace(b'url ', b'alis'))
        self.assertTrue(self.run_audit()['ok'])
        self.mov.write_bytes(movie().replace(b'url ' + u32(1), b'alis' + u32(0)))
        self.assertFalse(self.run_audit()['ok'])

    def test_diagnostic_keeps_core_guards(self):
        for kwargs in ({'iso': True}, {'marker': 0}, {'cdsc': None}, {'cdsc': 99}, {'identifier': 'different'}):
            self.mov.write_bytes(movie(**kwargs))
            self.assertFalse(audit.audit_pair(self.photo, self.mov, apple_structure_only=True)['ok'])

    def test_heic_diagnostic_and_truncation(self):
        tiff = jpeg()[12:-2]
        exif = u32(0) + tiff
        def heic(offset=None):
            info = full(b'iinf', struct.pack('>H', 1) + box(b'infe',
                        b'\x02\0\0\0' + struct.pack('>HH', 7, 0) + b'Exif\0'))
            def meta(pos):
                loc = full(b'iloc', b'\x44\0' + struct.pack('>HHHH', 1, 7, 0, 1) + u32(pos, len(exif)))
                return full(b'meta', info + loc + full(b'pitm', struct.pack('>H', 1)) +
                            full(b'iref', box(b'cdsc', struct.pack('>HHH', 7, 1, 1))))
            prefix = box(b'ftyp', b'heic' + bytes(4))
            return prefix + meta(len(prefix) + len(meta(0)) + 8 if offset is None else offset) + box(b'mdat', exif)
        self.photo = self.photo.with_suffix('.HEIC')
        self.photo.write_bytes(heic())
        self.assertTrue(audit.audit_pair(self.photo, self.mov, apple_structure_only=True)['ok'])
        self.assertFalse(self.run_audit()['ok'])
        for blob in (heic()[:-1], heic(0xffffff), b'bad'):
            self.photo.write_bytes(blob)
            self.assertFalse(audit.audit_pair(self.photo, self.mov, apple_structure_only=True)['ok'])

    def test_diagnostic_cli_redacts(self):
        args = [str(self.photo), str(self.mov), '--apple-structure-only']
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(audit.main(args), 0)
        self.assertNotIn(IDENT, output.getvalue())
        self.assertFalse(json.loads(output.getvalue())['z8_acceptance_eligible'])

    def test_movie_failures(self):
        for kwargs in ({'identifier': 'wrong'}, {'still': False}, {'offset': 3000},
                       {'offset': 1200}, {'frames': 89}, {'delta': 101},
                       {'sample': False}, {'key': b'fake'}, {'rate': 2},
                       {'duplicate': True}, {'marker': 7}):
            with self.subTest(kwargs=kwargs):
                self.mov.write_bytes(movie(**kwargs))
                self.assertFalse(self.run_audit()['ok'])

    def test_minus_one_marker_is_not_a_negative_timestamp(self):
        self.mov.write_bytes(movie(marker=-1))
        self.assertTrue(self.run_audit()['ok'])

    def test_missing_or_changed_nikon_fails(self):
        self.assertFalse(audit.audit_pair(self.photo, self.mov)['ok'])
        self.original.write_bytes(jpeg())
        self.assertFalse(self.run_audit()['ok'])
        self.original.unlink()
        self.assertFalse(self.run_audit()['ok'])

    def test_missing_apple_and_string_spoof_fails(self):
        for data in (jpeg(nikon=True), b'\xff\xd8' + IDENT.encode() + b'\xff\xd9', jpeg('')):
            self.photo.write_bytes(data)
            self.assertFalse(self.run_audit()['ok'])

    def test_truncation_and_unsupported_are_json_failures(self):
        for data in (b'', movie()[:-1], box(b'moov', b'bad'), movie() + box(b'moof')):
            self.mov.write_bytes(data)
            self.assertFalse(self.run_audit()['ok'])
        self.photo = self.photo.with_suffix('.HEIC')
        self.photo.write_bytes(b'not a supported HEIC')
        self.assertFalse(self.run_audit()['ok'])

    def test_names_and_dcim(self):
        for name in ('WRONG.MOV', 'LVP_0002.MOV'):
            self.mov = self.mov.rename(self.folder / name)
            self.assertFalse(self.run_audit()['ok'])
        self.mov = self.mov.rename(self.root / 'LVP_0001.MOV')
        self.assertFalse(self.run_audit()['ok'])

    def test_invalid_parameters(self):
        for kwargs in ({'pre': float('nan')}, {'post': 0}, {'fps': -30}, {'fps': float('inf')}):
            self.assertFalse(self.run_audit(**kwargs)['ok'])

    def test_corrupt_metadata_is_rejected(self):
        valid = movie()
        for before, after in ((CONTENT, b'x' * len(CONTENT)),
                              (b'mdta', b'xxxx'), (b'stts', b'xxxx'),
                              (b'stco', b'xxxx'), (b'stsz', b'xxxx'),
                              (b'mebx', b'xxxx'), (b'keyd', b'xxxx'),
                              (b'dtyp', b'xxxx'), (b'avc1', b'xxxx'),
                              (u32(0, 65), u32(0, 1)),
                              (b'url ' + u32(1), b'url ' + u32(0))):
            with self.subTest(before=before):
                self.mov.write_bytes(valid.replace(before, after))
                self.assertFalse(self.run_audit()['ok'])

    def test_truncated_jpeg_and_duplicate_exif(self):
        valid = jpeg()
        for data in (valid[:10], valid[:-2], valid[:2] + valid[2:-2] * 2 + valid[-2:],
                     valid.replace(b'Apple iOS', b'Nikon iOS')):
            self.photo.write_bytes(data)
            self.assertFalse(self.run_audit()['ok'])

    def test_changed_original_bytes_fail_even_with_nikon_header(self):
        self.original.write_bytes(jpeg(nikon=True)[:-2] + b'extra' + b'\xff\xd9')
        self.assertFalse(self.run_audit()['ok'])

    def test_low_level_guards_and_extended_boxes(self):
        self.assertEqual(audit.boxes(u32(1) + b'free' + struct.pack('>Q', 17) + b'x')[0][1], b'x')
        for data in (b'', b'bad', u32(0), u32(999) + b'free'):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    audit.number(data, len(data))
        for data in (bytes(4) + u32(9), b'\1\0\0\0' + u32(0)):
            with self.assertRaises(ValueError):
                audit.table(data, 4)

    def test_v1_time_and_no_edits(self):
        self.assertEqual(audit.timing(b'\1\0\0\0' + bytes(16) + u32(1000) + struct.pack('>Q', 3000)), (1000, 3000))
        self.assertEqual(audit.edit_start([], 1000, 3000, 100), 0)
        edit = box(b'edts', box(b'elst', b'\1\0\0\0' + u32(2) +
            struct.pack('>QqHH', 1500, -1, 1, 0) + struct.pack('>QqHH', 33, 0, 1, 0)))
        self.assertEqual(audit.edit_start(audit.boxes(edit), 1000, 3000, 100), 1.5)

    def test_marker_interval_cannot_overrun_clip(self):
        self.mov.write_bytes(movie(offset=2990))
        self.assertFalse(self.run_audit(pre=2.99, post=0.01)['ok'])

    def test_main_in_process_and_bad_manifests(self):
        original = ['--nikon-original', str(self.original), '--nikon-sha256', self.digest]
        for arguments, expected in (([str(self.photo), str(self.mov)] + original, 0),
                                    ([str(self.root)] + original, 0),
                                    ([str(self.root / 'missing')], 1)):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(audit.main(arguments), expected)
            self.assertEqual(json.loads(output.getvalue())['ok'], expected == 0)
        for manifest in ([], {}, {'DCIM/100NZ8LP/LVP_0001.MOV': {}},
                         {'DCIM/100NZ8LP/LVP_0001.MOV': {'nikon_original': '../escape.JPG', 'nikon_sha256': self.digest}}):
            self.assertFalse(audit.audit_card(self.root, manifest)['ok'])

    def test_cli_failure_always_has_receipt(self):
        command = [sys.executable, str(Path(audit.__file__))]
        for args in ([str(self.root / 'missing')], [str(self.photo), str(self.mov), '--pre', 'nan']):
            result = subprocess.run(command + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)['ok'])

    def test_multi_pair_card_manifest_and_duplicate_ids(self):
        manifest = {}
        for i in (1, 2):
            name = f'LVP_{i:04d}'
            ident = IDENT[:-1] + str(i)
            (self.folder / (name + '.JPG')).write_bytes(jpeg(ident))
            (self.folder / (name + '.MOV')).write_bytes(movie(identifier=ident))
            original = self.folder / f'DSC_{i:04d}.JPG'
            original.write_bytes(jpeg(nikon=True))
            manifest[f'DCIM/100NZ8LP/{name}.MOV'] = {
                'nikon_original': f'DCIM/100NZ8LP/DSC_{i:04d}.JPG', 'nikon_sha256': self.digest}
        path = self.root / 'preservation.json'
        path.write_text(json.dumps(manifest))
        command = [sys.executable, str(Path(audit.__file__)), str(self.root), '--preservation-manifest', str(path)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(len(json.loads(result.stdout)['pairs']), 2)
        (self.folder / 'LVP_0002.JPG').write_bytes(jpeg(IDENT[:-1] + '1'))
        (self.folder / 'LVP_0002.MOV').write_bytes(movie(identifier=IDENT[:-1] + '1'))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('reused', json.loads(result.stdout)['errors'][0])

    def test_cli_pair_and_directory_fail_closed(self):
        command = [sys.executable, str(Path(audit.__file__))]
        extra = ['--nikon-original', str(self.original), '--nikon-sha256', self.digest]
        for paths in ([str(self.photo), str(self.mov)], [str(self.root)]):
            result = subprocess.run(command + paths + extra, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(json.loads(result.stdout)['ok'])
        (self.folder / 'LVP_0002.MOV').write_bytes(movie())
        result = subprocess.run(command + [str(self.root)] + extra, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
