# SPDX-License-Identifier: GPL-2.0-or-later
import struct
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_nef_codec import f0_metadata,image_ifds,require_f0
class CodecMetadataTests(unittest.TestCase):
    def test_profile_excludes_uncompressed_size_and_requires_predictors(self):
        tags={256:[64],257:[16],258:[14],259:[34713],273:[128],279:[852]}
        table=b'F0'+struct.pack('<4H',2048,2048,2048,2048)
        result=f0_metadata(tags,table,4096)
        self.assertTrue(result['libraw_packed_fallback_excluded'])
        for bad in ({279:[1792]},{279:[4]},{279:[4000]},{258:[12]},{259:[1]},{256:[63]}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):f0_metadata(tags|bad,table,4096)
        with self.assertRaises(ValueError):f0_metadata(tags,b'F0'+bytes(8),4096)
    def test_truncated_or_cyclic_ifd_is_rejected(self):
        for data in (b'II*\0',b'II*\0'+struct.pack('<I',7),b'II*\0'+struct.pack('<I',8)+b'\0\0'+struct.pack('<I',8)):
            with self.subTest(data=data),self.assertRaises(ValueError):image_ifds(data)
        data=b'II*\0'+struct.pack('<I',8)+b'\0\0'+bytes(4)
        self.assertEqual(image_ifds(data),[{}])

    def test_actual_raw_ifd_bounds_and_metadata_command(self):
        tags={254:0,256:64,257:16,258:14,259:34713,262:32803,273:128,279:852}
        def tiff(rows):
            payload=b'II*\0'+struct.pack('<I',8)+struct.pack('<H',len(rows))
            for tag,value in rows:payload+=struct.pack('<HHII',tag,4,1,value)
            return (payload+bytes(4)).ljust(980,b'\0')
        table=b'F0'+struct.pack('<4H',2048,2048,2048,2048)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.NEF';path.write_bytes(tiff(tags.items()))
            self.assertEqual(image_ifds(path.read_bytes()),[{k:[v] for k,v in tags.items()}])
            with patch('z8_nef_codec.subprocess.check_output',return_value=table) as command:
                self.assertEqual(require_f0(path)['raw_strip_bytes'],852)
                command.assert_called_once_with(['exiftool','-b','-NEFLinearizationTable',str(path)],timeout=30)
            path.write_bytes(tiff([(254,0),(254,0)]))
            with self.assertRaises(ValueError):image_ifds(path.read_bytes())
            path.write_bytes(tiff([(330,7)]))
            with self.assertRaises(ValueError):image_ifds(path.read_bytes())
            path.write_bytes(tiff([(254,1)]))
            with self.assertRaises(ValueError):require_f0(path)
