# SPDX-License-Identifier: GPL-2.0-or-later
"""Native reset tuple regression; fixtures are synthetic, not capture15 values."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import unittest
from test_z8_writer_observer import Harness,FILE,REC,INNER,WHOLE,CTX,regs
from z8_writer_observer import ObservationError,OK


def prepared():
    h=Harness();h.f.put(FILE,'Q',FILE+0x74);h.f.put(FILE+8,'I',0x301);h.f.put(FILE+0x70,'I',3)
    h.begin();h.segment();return h


def final_call(h):h.call('final',X00=FILE,X01=100,X19=REC,X22=FILE)


def reset(h):
    h.f.put(FILE+8,'IiQQQ',0,-1,0,100,0);h.f.put(FILE+0x70,'I',1)


def final_return(h,status=OK):h.ret('final',X00=status,X19=REC,X22=FILE)


def cleanup(h):
    h.call('cleanup',X00=FILE,X19=REC,X22=FILE);h.ret('cleanup',X00=0,X19=REC)


class FinalizeTests(unittest.TestCase):
    def test_only_paired_successful_finalize_accepts_exact_native_reset(self):
        h=prepared();final_call(h);reset(h);final_return(h);cleanup(h)
        life=h.o.lifetimes[0]
        self.assertEqual(life['opened_handle'],3)
        self.assertTrue(life['final_reset_observed']);self.assertTrue(life['cleanup_returned'])
        self.assertFalse(life['native_accepted']);self.assertFalse(life['final_reset']['internal_close_success_verified'])
        self.assertEqual(life['final_reset']['after']['handle'],-1)
    def test_early_minus_one_remains_failure(self):
        h=prepared();h.f.put(FILE+0xc,'i',-1)
        with self.assertRaises(ObservationError):final_call(h)
        snap=h.o.events[-1]['file_snapshots'][-1]
        self.assertEqual(snap['decoded']['handle'],-1);self.assertEqual(snap['previous_valid']['handle'],3)
    def test_wrong_final_tuple_is_never_accepted(self):
        fields=[(8,'I',1),(0xc,'i',4),(0xc,'i',3),(0xc,'i',-2),(0x10,'Q',100),
                (0x18,'Q',99),(0x20,'Q',1),(0x70,'I',3)]
        for off,fmt,value in fields:
            with self.subTest(off=off,value=value):
                h=prepared();final_call(h);reset(h);h.f.put(FILE+off,fmt,value)
                with self.assertRaises(ObservationError):final_return(h)
                snap=h.o.events[-1]['file_snapshots'][-1]
                self.assertEqual(len(bytes.fromhex(snap['raw_hex'])),0xa0)
                self.assertFalse(h.o.lifetimes[0].get('final_reset_observed',False))
    def test_outer_error_status_does_not_authorize_reset(self):
        h=prepared();final_call(h);reset(h)
        with self.assertRaises(ObservationError):final_return(h,0x04320089)
        self.assertEqual(h.o.events[-1]['file_snapshots'][-1]['decoded']['handle'],-1)
    def test_correct_after_tuple_cannot_hide_unsupported_before_state_or_mode(self):
        for off,value in ((8,2),(0x70,4)):
            with self.subTest(off=off):
                h=prepared();h.f.put(FILE+off,'I',value);final_call(h);reset(h)
                with self.assertRaises(ObservationError):final_return(h)
                self.assertFalse(h.o.lifetimes[0].get('final_reset_observed',False))
    def test_original_close_call_return_still_requires_the_exact_saved_sp(self):
        h=prepared();final_call(h);reset(h);final_return(h)
        h.call('cleanup',X00=FILE,X19=REC,X22=FILE)
        from z8_writer_observer import SITES
        with self.assertRaises(ObservationError):h.hit(regs(SITES['cleanup'].ret,INNER+16,X00=0))
        self.assertFalse(h.o.lifetimes[0]['cleanup_returned'])
    def test_same_id_changed_path_or_pointer_is_rejected_before_finalize(self):
        for which in ('bytes','length','pointer'):
            with self.subTest(which=which):
                h=prepared()
                if which=='bytes':h.f.raw(FILE+0x74,b'XSC_0001.NEF\0')
                elif which=='length':h.f.put(FILE+0x94,'I',11);h.f.raw(FILE+0x74+11,b'\0')
                else:h.f.put(FILE,'Q',FILE+0x75)
                with self.assertRaises(ObservationError):final_call(h)
    def test_bad_path_length_retains_raw_and_decoded_first_failure(self):
        h=prepared();final_call(h);reset(h);h.f.put(FILE+0x94,'I',32)
        with self.assertRaises(ObservationError):final_return(h)
        snap=h.o.events[-1]['file_snapshots'][-1]
        self.assertEqual(snap['decoded']['path_length'],32)
        self.assertEqual(snap['decoded']['handle'],-1);self.assertTrue(snap['previous_valid'])
    def test_final_same_va_different_physical_span_is_rejected(self):
        h=prepared();final_call(h);reset(h)
        page=FILE&~4095;old=h.f.translate
        # Explicit changed synthetic page table. Copy physical bytes only to
        # make identity, rather than missing data, the reason for rejection.
        for i in range(0x1000):h.f.data[page+0x30000+i]=h.f.data.get(page+i,0)
        h.f.translate=lambda cpu,a:old(cpu,a)+0x30000 if a==page else old(cpu,a)
        h.o.memory.translator=h.f.translate
        with self.assertRaises(ObservationError):final_return(h)
        snap=h.o.events[-1]['file_snapshots'][-1]
        self.assertNotEqual(snap['physical_span'],h.o.lifetimes[0]['object_physical_span'])
    def test_same_id_path_change_at_final_reset_is_rejected(self):
        h=prepared();final_call(h);reset(h);h.f.raw(FILE+0x74,b'XSC_0001.NEF\0')
        with self.assertRaises(ObservationError):final_return(h)
        self.assertEqual(h.o.events[-1]['file_snapshots'][-1]['decoded']['handle'],-1)
    def test_cleanup_cannot_accept_second_handle_transition(self):
        h=prepared();final_call(h);reset(h);final_return(h);h.f.put(FILE+0xc,'i',3)
        with self.assertRaises(ObservationError):cleanup(h)
    def test_finalize_cannot_be_followed_by_more_selection_or_data(self):
        h=prepared();final_call(h);reset(h);final_return(h)
        with self.assertRaises(ObservationError):
            h.call('select',X00=1,X01=REC,X02=2,X03=INNER+0x70,X19=REC,X25=0)
    def test_new_file_id_still_fails_and_is_preserved(self):
        h=prepared();final_call(h);reset(h);h.f.put(FILE+0x28,'I',78)
        with self.assertRaises(ObservationError):final_return(h)
        self.assertEqual(h.o.events[-1]['file_snapshots'][-1]['decoded']['file_id'],78)


if __name__=='__main__':unittest.main()
