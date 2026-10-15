# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic external RTC contract and original C3.11 decoder execution."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import struct
import unittest

from z8_datetime import rtc_body, validate_rtc_body, MAIN_SHA
from service_z9_mcu import Peer


class Input(unittest.TestCase):
    def test_wire_and_validation(self):
        body=rtc_body(datetime(2026,10,14,12,34,56))
        self.assertEqual(body,bytes([1,0,0,0,26,9,13,12,34,56,0,0,0,0]))
        self.assertEqual(validate_rtc_body(body),body)

    def test_limits_and_leap_day(self):
        validate_rtc_body(rtc_body(datetime(2000,2,29),status12=1,status13=2))
        validate_rtc_body(rtc_body(datetime(2255,12,31,23,59,59)))
        for moment in (datetime(1999,1,1),datetime(2256,1,1),datetime.now(timezone.utc),None):
            with self.assertRaises(ValueError):rtc_body(moment)
        for key,value in (('status12',2),('status12',True),('status13',3),('status13',-1)):
            with self.assertRaises(ValueError):rtc_body(datetime(2026,1,1),**{key:value})

    def test_malformed_external_frames_fail_before_queue(self):
        good=rtc_body(datetime(2026,10,14))
        bad=[good[:-1],good+b'\0',None,'date',bytes(14)]
        for offset,value in ((1,1),(2,1),(3,1),(10,1),(11,1),(5,12),(6,31),(7,24),(8,60),(9,60),(12,2),(13,3)):
            body=bytearray(good);body[offset]=value;bad.append(bytes(body))
        for body in bad:
            with self.subTest(body=body),self.assertRaises(ValueError):validate_rtc_body(body)
        b=bytearray(good);b[5]=1;b[6]=29
        with self.assertRaises(ValueError):validate_rtc_body(bytes(b))

    def peer(self,sha):
        peer=Peer.__new__(Peer);peer.pending=[];peer.record={}
        peer.g=SimpleNamespace(data={'firmware_sha256':sha})
        peer.start_rx=lambda:None
        return peer

    def test_explicit_z8_only_peer_routing(self):
        body=rtc_body(datetime(2026,10,14))
        peer=self.peer(MAIN_SHA);peer.enqueue_external(body,7)
        family,payload,wire=peer.pending[0]
        self.assertEqual((family,payload),(7,body))
        self.assertEqual(wire[:4],bytes([7,1,14,0]))
        self.assertEqual(sum(wire)&255,255)
        for sha in ('wrong',None):
            peer=self.peer(sha)
            with self.assertRaises(ValueError):peer.enqueue_external(body,7)
            self.assertEqual(peer.pending,[])
        peer=self.peer(MAIN_SHA)
        with self.assertRaises(ValueError):peer.enqueue_external(bytes(14),7)
        with self.assertRaises(ValueError):peer.enqueue_external(body,8)
        self.assertEqual(peer.pending,[])


@unittest.skipUnless(os.environ.get('Z8_MAIN'),'Pinned local MAIN required')
class Native(unittest.TestCase):
    def test_original_dispatch_and_full_date_status_decode(self):
        from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM
        from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_LR,UC_ARM64_REG_SP,UC_ARM64_REG_PC
        source=Path(os.environ['Z8_MAIN']).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),MAIN_SHA)
        for moment in (datetime(2000,2,29),datetime(2026,10,14,12,34,56),datetime(2255,12,31,23,59,59)):
            for s12 in range(2):
                for s13 in range(3):
                    u=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
                    u.mem_map(0x40160000,(len(source)+4095)&~4095);u.mem_write(0x40160000,source)
                    u.mem_map(0xa03e07000,0x1000);u.mem_map(0xa07000000,0x10000)
                    raw,result,stop=0xa07001000,0xa07002000,0xa07000000
                    u.mem_write(raw,rtc_body(moment,status12=s12,status13=s13))
                    for reg,value in ((UC_ARM64_REG_X0,raw),(UC_ARM64_REG_X1,14),(UC_ARM64_REG_X2,result),(UC_ARM64_REG_LR,stop),(UC_ARM64_REG_SP,0xa0700fff0)):
                        u.reg_write(reg,value)
                    u.emu_start(0x4101a110,stop,count=300)
                    self.assertEqual(u.reg_read(UC_ARM64_REG_PC),stop)
                    self.assertEqual(u.reg_read(UC_ARM64_REG_X0),1)
                    abstract=struct.unpack('<Q',u.mem_read(result,8))[0]
                    self.assertEqual(u.mem_read(abstract,1),b'\x03')
                    payload=struct.unpack('<Q',u.mem_read(abstract+8,8))[0]
                    data=bytes(u.mem_read(payload,20))
                    self.assertEqual(struct.unpack_from('<H5B',data),(moment.year,moment.month,moment.day,moment.hour,moment.minute,moment.second))
                    self.assertEqual(struct.unpack_from('<II',data,12),(s12+1,s13+1))


if __name__=='__main__':unittest.main()
