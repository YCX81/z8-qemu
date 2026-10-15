# SPDX-License-Identifier: GPL-2.0-or-later
"""Firmware-free checks of stream integrity and resource-search boundaries."""
import bz2
import gzip
import lzma
from pathlib import Path
import sys
import struct
import unittest
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_af_compression_scan import decode, scan, inspect_cpio, inspect_nisi


def cpio_entry(name, data=b''):
    encoded=name.encode()+b'\0'
    fields=[1,0o100644,0,0,1,0,len(data),0,0,0,0,len(encoded),0]
    result=b'070701'+b''.join(f'{v:08x}'.encode() for v in fields)+encoded
    result+=bytes(-len(result)%4)
    result+=data
    return result+bytes(-len(result)%4)


def nisi_fixture():
    image=bytearray(0x3c)
    image[0x38:0x3c]=b'ARMd'
    archive=cpio_entry('AF-resource',b'AF'+bytes(14))+cpio_entry('TRAILER!!!')
    fdt=b'\xd0\x0d\xfe\xed'+struct.pack('>I',8)
    payloads=[bytes(image),gzip.compress(archive),fdt,fdt]
    result=bytearray(5*0x800)
    result[:16]=b'NISI'+bytes(12)
    struct.pack_into('<I',result,0x10,4)
    for i,payload in enumerate(payloads):
        struct.pack_into('<4I',result,0x20+i*16,i+1,1,len(payload),0)
        result[(i+1)*0x800:(i+1)*0x800+len(payload)]=payload
    return result


class CompressionSearchTests(unittest.TestCase):
    def test_embedded_resource_in_each_supported_container(self):
        body=b'AF'+bytes(14)+bytes(range(256))*44
        for kind,compress in [('zlib',zlib.compress),('gzip',gzip.compress),
                              ('bzip2',bz2.compress),('xz',lzma.compress)]:
            with self.subTest(kind=kind):
                packed=compress(body)
                container=b'prefix'+packed+b'irrelevant trailing bytes'
                result=scan(container,{'af':b'AF'+bytes(14)})
                hits=[v for v in result['complete_streams'] if v['format']==kind and v['offset']=='0x6']
                self.assertEqual(len(hits),1)
                self.assertEqual(hits[0]['compressed_bytes'],len(packed))
                self.assertEqual(hits[0]['decoded_bytes'],len(body))
                self.assertEqual(hits[0]['matches']['af'],['0x0'])

    def test_corrupt_checksum_is_not_a_successful_stream(self):
        packed=bytearray(zlib.compress(b'AF'+bytes(14000)))
        packed[-1]^=1
        self.assertEqual(decode(packed,0,'zlib')[0],'invalid_or_unsupported')

    def test_expansion_limit_is_reported(self):
        packed=zlib.compress(bytes(10000))
        self.assertEqual(decode(packed,0,'zlib',output_limit=100)[0],'output_bound')

    def test_truncation_is_not_a_successful_stream(self):
        packed=zlib.compress(bytes(range(256))*50)
        self.assertEqual(decode(packed,0,'zlib',input_limit=10)[0],'incomplete_or_input_bound')

    def test_unimplemented_frame_is_reported_as_unresolved(self):
        result=scan(b'\x04\x22\x4d\x18'+bytes(20),{})
        self.assertEqual(result['complete_streams'],[])
        self.assertEqual(result['unresolved_candidates'][0]['reason'],'decoder_not_enabled')

    def test_png_context_requires_chunk_crc(self):
        packed=zlib.compress(b'AF'+bytes(14))
        body=b'IDAT'+packed
        chunk=struct.pack('>I',len(packed))+body+struct.pack('>I',zlib.crc32(body))
        self.assertTrue(scan(chunk,{})['complete_streams'][0]['png_idat_chunk_crc_valid'])
        corrupt=bytearray(chunk)
        corrupt[-1]^=1
        self.assertFalse(scan(corrupt,{})['complete_streams'][0]['png_idat_chunk_crc_valid'])

    def test_nested_nisi_gzip_cpio_resource_is_found(self):
        result=inspect_nisi(nisi_fixture(),{'af':b'AF'+bytes(14)})
        self.assertEqual(result['directory_records'],4)
        cpio=result['parts'][1]['cpio']
        self.assertEqual(cpio['records_including_trailer'],2)
        self.assertEqual(cpio['entries'][0]['matches']['af'],['0x0'])

    def test_nisi_overlap_is_rejected(self):
        data=nisi_fixture()
        struct.pack_into('<I',data,0x30,1)
        with self.assertRaisesRegex(ValueError,'overlapping'):
            inspect_nisi(data,{})

    def test_cpio_truncated_file_is_rejected(self):
        data=cpio_entry('sample',b'x'*100)
        with self.assertRaisesRegex(ValueError,'Truncated'):
            inspect_cpio(data[:-1],{})

    def test_cpio_nonzero_tail_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError,'nonzero'):
            inspect_cpio(cpio_entry('TRAILER!!!')+b'another archive',{})

    def test_dictionary_header_is_explicitly_unresolved(self):
        compressor=zlib.compressobj(zdict=b'AF'*100)
        packed=compressor.compress(b'AF'*200)+compressor.flush()
        result=scan(packed,{})
        self.assertGreaterEqual(result['counts']['zlib']['preset_dictionary_header'],1)


if __name__=='__main__':
    unittest.main()
