# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit RTC input format and serialized delivery; no software flag writes."""
from datetime import datetime, timezone
from types import SimpleNamespace
import struct
import unittest
from z8_rtc import status_body, enqueue_status


class RTCInput(unittest.TestCase):
    def test_calendar_wire_fields(self):
        body=status_body(datetime(2026,10,14,12,34,56))
        self.assertEqual(body,bytes((1,0,0,0,26,9,13,12,34,56,0,0,0,0)))

    def test_boundary_year_and_status(self):
        body=status_body(datetime(2255,12,31,23,59,59),reset_flag=1,auxiliary_status=3)
        self.assertEqual(body[4:10],bytes((255,11,30,23,59,59)))
        self.assertEqual(body[12:],bytes((1,3)))
        self.assertEqual(status_body(datetime(2000,2,29))[4:7],bytes((0,1,28)))

    def test_invalid_input_is_rejected(self):
        for value in (None,'2026-10-14',datetime(1999,12,31),datetime(2256,1,1),datetime(2026,1,1,tzinfo=timezone.utc)):
            with self.subTest(value=value),self.assertRaises(ValueError):status_body(value)
        for kwargs in ({'reset_flag':2},{'reset_flag':True},{'auxiliary_status':4},{'auxiliary_status':1.0}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):status_body(datetime(2026,1,1),**kwargs)

    def test_serialized_family_seven_delivery(self):
        events=[]
        peer=SimpleNamespace(pending=[('existing',)],record={},start_rx=lambda:events.append('start'))
        enqueue_status(peer,datetime(2026,10,14,12,34,56))
        self.assertEqual(peer.pending[0],('existing',))
        family,body,packet=peer.pending[1]
        self.assertEqual((family,packet[0],packet[1]),(7,7,1))
        self.assertEqual(struct.unpack_from('<H',packet,2)[0],14)
        self.assertEqual(packet[8:22],body)
        self.assertEqual(sum(packet)&255,255)
        self.assertEqual(events,['start'])
        self.assertFalse(peer.record['rtc_status_inputs'][0]['physical_protocol_verified'])

    def test_bad_calendar_does_not_enqueue(self):
        peer=SimpleNamespace(pending=[],record={},start_rx=lambda:self.fail('Unexpected delivery'))
        with self.assertRaises(ValueError):enqueue_status(peer,datetime(1900,1,1))
        self.assertEqual(peer.pending,[])
        self.assertEqual(peer.record,{})

if __name__=='__main__':unittest.main()
