# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute the guest two-block commit protocol; device fixture is explicit."""
import struct
import unittest
from test_z8_c10_media import AVAILABLE
if not AVAILABLE:
    raise unittest.SkipTest('Install requirements-c10.txt for native media contracts')
from test_z8_c10_media import Device as OldDevice, Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ
from test_z8_c10_media import UC_ARM64_REG_SP, UC_ARM64_REG_PC, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X30, UC_ARM64_REG_PSTATE
from z8_slots_media import build_media_code,SAVE,LOAD,CRC,BLOCKS,RECORD_BYTES,HEADER_BUFFER,LOAD_BUFFER,DATA_BASE
from z8_slots_layout import Layout


class Device(OldDevice):
    def __init__(self,pages=None):
        self.pages={} if pages is None else dict(pages)
        self.fail_next_program=False;self.fail_read_pages=set();self.fail_read_occurrences=set()
        self.fail_program_pages=set();self.read_counts={};self.commands=[];self.mmio_daif=[]
        self.uc=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
        self.uc.mem_map(Layout.media_code,0x2000);self.uc.mem_map(DATA_BASE,Layout.reserved_bytes)
        self.uc.mem_map(0x17800000,0x2000);self.uc.mem_map(0x17a00000,0x1000)
        self.uc.mem_write(0x17a0032c,struct.pack('<I',1));self.uc.mem_write(0x17a00300,struct.pack('<I',7))
        region=build_media_code();self.uc.mem_write(region['address'],region['data'])
        self.uc.hook_add(UC_HOOK_MEM_WRITE,self.write,begin=0x17800000,end=0x17800003)
        for first,last in ((0x17800000,0x17801FFF),(0x17A00000,0x17A00FFF)):
            self.uc.hook_add(UC_HOOK_MEM_READ|UC_HOOK_MEM_WRITE,self.observe_mask,begin=first,end=last)

    def write(self,uc,access,address,size,command,data):
        first=self.word(4);count=(command&255)+1
        if command&~255 in (0x40202100,0x40202200):
            if first//64 != (first+count-1)//64: raise AssertionError('Cross-block NAND transfer')
            if command&~255==0x40202100 and first in self.fail_program_pages:
                self.commands.append((command,first));uc.mem_write(0x17800014,struct.pack('<I',0xC000));return
        super().write(uc,access,address,size,command,data)

    def call(self,entry,pointer,size=RECORD_BYTES,daif=0):
        self.uc.reg_write(UC_ARM64_REG_PSTATE,0x4|daif)
        self.uc.reg_write(UC_ARM64_REG_SP,DATA_BASE+0xBFFF0)
        self.uc.reg_write(UC_ARM64_REG_X0,pointer);self.uc.reg_write(UC_ARM64_REG_X1,size)
        self.uc.reg_write(UC_ARM64_REG_X30,Layout.media_code+0x1FF0)
        self.uc.emu_start(entry,Layout.media_code+0x1FF0,count=20_000_000)
        if self.uc.reg_read(UC_ARM64_REG_PC)!=Layout.media_code+0x1FF0:raise RuntimeError('Guest did not return')
        return self.uc.reg_read(UC_ARM64_REG_X0)


R=Layout().address('records')

class MediaTests(unittest.TestCase):
    def test_guest_crc32_matches_independent_standard_oracle(self):
        import zlib
        d=Device()
        for payload in (b'',b'123456789',bytes(range(256))*(RECORD_BYTES//256)):
            if payload:d.uc.mem_write(R,payload)
            self.assertEqual(d.call(CRC,R,len(payload)),zlib.crc32(payload))

    def seed(self):
        d=Device();d.uc.mem_write(R,b'A'*RECORD_BYTES)
        self.assertEqual(d.call(SAVE,R),RECORD_BYTES);return d

    def test_blank_and_wrong_size_fail_without_writes(self):
        d=Device();self.assertEqual(d.call(LOAD,LOAD_BUFFER),0)
        d.commands.clear();self.assertEqual(d.call(SAVE,R,0x6000),0);self.assertEqual(d.commands,[])

    def test_payload_spans_two_blocks_and_header_commits_last(self):
        d=self.seed();programs=[(c,p) for c,p in d.commands if c&~255==0x40202100]
        self.assertEqual([(p,(c&255)+1) for c,p in programs],[(BLOCKS[0]+1,63),(BLOCKS[0]+64,2),(BLOCKS[0],1)])
        cold=Device(d.pages);self.assertEqual(cold.call(LOAD,LOAD_BUFFER),RECORD_BYTES)
        self.assertEqual(bytes(cold.uc.mem_read(LOAD_BUFFER,RECORD_BYTES)),b'A'*RECORD_BYTES)

    def test_second_block_program_failure_preserves_old_generation(self):
        d=self.seed();d.uc.mem_write(R,b'B'*RECORD_BYTES);d.fail_program_pages.add(BLOCKS[1]+64)
        self.assertEqual(d.call(SAVE,R),0)
        cold=Device(d.pages);self.assertEqual(cold.call(LOAD,LOAD_BUFFER),RECORD_BYTES)
        self.assertEqual(bytes(cold.uc.mem_read(LOAD_BUFFER,RECORD_BYTES)),b'A'*RECORD_BYTES)

    def test_corrupt_second_payload_block_falls_back_to_complete_old_copy(self):
        d=self.seed();d.uc.mem_write(R,b'B'*RECORD_BYTES);self.assertEqual(d.call(SAVE,R),RECORD_BYTES)
        d.pages[BLOCKS[1]+64]=b'X'*2048
        cold=Device(d.pages);self.assertEqual(cold.call(LOAD,LOAD_BUFFER),RECORD_BYTES)
        self.assertEqual(bytes(cold.uc.mem_read(LOAD_BUFFER,RECORD_BYTES)),b'A'*RECORD_BYTES)

    def test_second_block_scan_read_error_does_not_erase_anything(self):
        d=self.seed();before=dict(d.pages);d.commands.clear();d.fail_read_pages.add(BLOCKS[0]+64)
        self.assertEqual(d.call(SAVE,R),0);self.assertEqual(d.pages,before)
        self.assertTrue(all(c&~255==0x40202200 for c,_ in d.commands))

    def test_irq_state_restored_on_success_failure_and_overlap(self):
        for incoming in (0,0x300):
            d=self.seed();d.commands.clear();d.mmio_daif.clear()
            self.assertEqual(d.call(LOAD,LOAD_BUFFER,daif=incoming),RECORD_BYTES)
            self.assertEqual(set(d.mmio_daif),{incoming|0xC0})
            self.assertEqual(d.uc.reg_read(UC_ARM64_REG_PSTATE)&0x3C0,incoming)
            for pointer in (LOAD_BUFFER,LOAD_BUFFER-16,HEADER_BUFFER):
                d.commands.clear();self.assertEqual(d.call(SAVE,pointer,daif=incoming),0)
                self.assertEqual(d.commands,[]);self.assertEqual(d.uc.reg_read(UC_ARM64_REG_PSTATE)&0x3C0,incoming)


if __name__=='__main__':unittest.main()
