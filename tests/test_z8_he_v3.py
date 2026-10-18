# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import z8_he_v3_audit as audit
import z8_he_v3_image as image


class V3Guards(unittest.TestCase):
    def fixture(self, root):
        profile = {'entries': {'entry': '0x40960000'}}
        raw = json.dumps(profile).encode()
        code = b'authored non-executable loader fixture'
        (root/'resources').mkdir()
        (root/'resources/precapture.json').write_bytes(raw)
        (root/'resources/precapture.bin').write_bytes(code)
        pin = root/'pin.json'
        pin.write_text(json.dumps(dict(profile_sha256=hashlib.sha256(raw).hexdigest(),
            code_sha256=hashlib.sha256(code).hexdigest(), code_bytes=len(code), entries=profile['entries'])))
        return pin, code

    def test_exact_asset_loader_and_corruption_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pin,code=self.fixture(root)
            with patch.object(audit,'PIN',pin):
                self.assertEqual(audit.load_prebuilt(root)[0],code)
                for bad in (code[:-1],bytes([code[0]^1])+code[1:]):
                    (root/'resources/precapture.bin').write_bytes(bad)
                    with self.assertRaisesRegex(ValueError,'machine code'):audit.load_prebuilt(root)

    def test_profile_drift_is_not_admitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pin,_=self.fixture(root)
            (root/'resources/precapture.json').write_text('{}')
            with patch.object(audit,'PIN',pin),self.assertRaisesRegex(ValueError,'profile'):
                audit.load_prebuilt(root)

    def test_missing_asset_does_not_compile(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pin,_=self.fixture(root)
            (root/'resources/precapture.bin').unlink()
            with patch.object(audit,'PIN',pin),self.assertRaises(FileNotFoundError):audit.load_prebuilt(root)

    def test_v3_configuration(self):
        raw=audit.v3_config()
        self.assertEqual(raw[:16],b'Z8PRECFGv3'.ljust(16,b'\0'))
        self.assertEqual(struct.unpack_from('<4I',raw,16),(0x93,30,1500000,0))
        self.assertEqual(struct.unpack_from('<I',raw,60)[0],zlib.crc32(raw[:60]))

    def test_invalid_stock_never_derives_image(self):
        with self.assertRaisesRegex(ValueError,'exact stock'):
            image.derive(b'not firmware',Path('missing-input'))

    def test_branch_target_and_bounds(self):
        at,to=0x40ea3638,0x40961ea4
        word=struct.unpack('<I',image.branch(at,to))[0]
        immediate=word&0x3ffffff
        if immediate&(1<<25):immediate-=1<<26
        self.assertEqual(at+immediate*4,to)
        for bad in (to+1,at+(1<<27),at-(1<<27)-4):
            with self.assertRaises(ValueError):image.branch(at,bad)

    def test_nef_parser_rejects_invalid_output(self):
        with self.assertRaisesRegex(ValueError,'TIFF'):
            audit.inspect_nef(None,'invalid.NEF',{'prefix':b'bad!'})


@unittest.skipUnless(os.environ.get('Z8_HE_V3_TOOLKIT'),'Exact release toolkit is an optional external fixture')
class ExactAsset(unittest.TestCase):
    def test_reviewed_asset_identity(self):
        code,entries,profile=audit.load_prebuilt(os.environ['Z8_HE_V3_TOOLKIT'])
        self.assertEqual(len(code),30301)
        self.assertEqual(len(entries),8)
        self.assertEqual(hashlib.sha256(code).hexdigest(),profile['code_sha256'])


if __name__=='__main__':unittest.main()
