# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic guards using observed nonidentity RAM and identity ROMD cases."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import json
import tempfile
import unittest
from z8_writer_code_reader import RuntimeCodeReader
from z8_writer_observer import EXPECTED,MAIN_SHA,ObservationError

VA=0x406cc134
RAM_PA=0xa0fccc134
TREE='''FlatView #0
 AS "memory", root: system
 Root memory region: system
  0000000040000000-0000000047ffffff (prio 0, romd): z9.main-image
  0000000a00000000-0000000a3fffffff (prio 0, ram): z8.bringup-work-ram
  0000000840000000-00000008adf8d9ff (prio 0, ram): z9.image-bank-0
  00000008adf8dc00-00000008fff473ff (prio 0, ram): z9.image-bank-1
'''
GEOMETRY={'base_memory':1024<<20,'image_bank_state':{'enabled':True,'ranges':[
    {'base':'0x840000000','end':'0x8adf8da00'},{'base':'0x8adf8dc00','end':'0x8fff47400'}]}}


class Guest:
    def __init__(self,out):
        self.out=Path(out);self.data={'firmware_sha256':MAIN_SHA};self.qmp=self
        self.pas={0:RAM_PA,1:RAM_PA,2:0xa10121134,3:VA};self.reads=[];self.commands=[]
        self.running=False;self.wrong=False;self.reply=None;self.tree=TREE;self.translations=0
        self.mapping_change=False;self.state_change=False;self.type_change=False;self.views=0
    def command(self,name,**kw):
        self.commands.append((name,kw))
        if name=='query-status':return {'running':self.running,'status':'paused'}
        if name=='qom-get' and kw['property']=='runtime-clock-state':return json.dumps({'virtual_ns':100})
        raise AssertionError(name)
    def hmp(self,command,cpu=0):
        self.commands.append((command,cpu))
        if command=='info mtree -f':
            self.views+=1
            return self.tree.replace('romd','i/o') if self.type_change and self.views>=2 else self.tree
        if command=='info registers':
            pc=VA+(4 if self.state_change and self.translations>=3 else 0)
            return f'PC={pc:x} SP=a06c4ed70 X30=40c00294 PSTATE=60000004\n'
        if command.startswith('gva2gpa '):
            self.translations+=1;va=int(command.split()[1],0)
            if self.reply is not None:return self.reply
            pa=self.pas[cpu]-(VA&4095)+(va&4095)
            if self.mapping_change and self.translations>=3:pa+=4096
            return f'gpa: {pa:#x}\r\n'
        raise AssertionError(command)
    def read_bytes(self,pa,n):
        self.reads.append((pa,n));return b'xxxx' if self.wrong else EXPECTED[VA]


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.g=Guest(self.tmp.name)
        self.r=RuntimeCodeReader(self.g,GEOMETRY,lambda:None)
    def tearDown(self):self.tmp.cleanup()
    def test_observed_nonidentity_ram_is_not_rejected(self):
        self.assertEqual(self.r.read(0,VA,phase='hit'),EXPECTED[VA])
        row=self.r.receipt['reads'][0]
        self.assertEqual(row['before']['exact']['raw_reply'],f'gpa: {RAM_PA:#x}\r\n')
        self.assertEqual(row['before']['exact']['pa'],RAM_PA)
        self.assertEqual(row['safe_region_before']['name'],'z8.bringup-work-ram')
        self.assertTrue(row['verified']);self.assertEqual(self.g.reads,[(RAM_PA,4)])
    def test_identity_romd_still_requires_current_type(self):
        self.assertEqual(self.r.read(3,VA,phase='hit'),EXPECTED[VA])
        self.assertEqual(self.r.receipt['reads'][0]['safe_region_before']['kind'],'romd')
        self.g.tree=TREE.replace('romd','i/o')
        with self.assertRaises(ObservationError):self.r.read(3,VA,phase='hit')
        self.assertEqual(len(self.g.reads),1)
    def test_actual_cpu_and_mapping_are_not_reused(self):
        self.r.read(0,VA,phase='hit');self.r.read(2,VA,phase='hit')
        self.assertEqual(self.g.reads,[(RAM_PA,4),(self.g.pas[2],4)])
        self.g.pas[2]+=0x3000;self.r.read(2,VA,phase='hit')
        self.assertEqual(self.g.reads[-1],(self.g.pas[2],4))
        self.assertEqual([x['cpu'] for x in self.r.receipt['reads']],[0,2,2])
    def test_activation_checks_all_four_cpus(self):
        self.assertEqual(self.r.activation_word(VA),EXPECTED[VA])
        self.assertEqual([x['cpu'] for x in self.r.receipt['reads']],[0,1,2,3])
        self.assertTrue(all(x['phase']=='activation' for x in self.r.receipt['reads']))
    def test_mapping_change_during_read_fails_with_both_replies(self):
        self.g.mapping_change=True
        with self.assertRaises(ObservationError):self.r.read(0,VA,phase='hit')
        row=self.r.receipt['reads'][0]
        self.assertNotEqual(row['before']['exact']['pa'],row['after']['exact']['pa'])
        self.assertFalse(row['verified']);self.assertTrue(row['opcode_matches'])
    def test_cpu_state_change_fails(self):
        self.g.state_change=True
        with self.assertRaises(ObservationError):self.r.read(0,VA,phase='hit')
        self.assertFalse(self.r.receipt['reads'][0]['cpu_state_unchanged'])
    def test_wrong_opcode_fails_without_losing_context(self):
        self.g.wrong=True
        with self.assertRaises(ObservationError):self.r.read(1,VA,phase='hit')
        saved=json.loads((self.g.out/'native-code-guards.json').read_text())
        row=saved['reads'][0]
        self.assertEqual(row['cpu'],1);self.assertEqual(row['va'],VA)
        self.assertEqual(row['before']['exact']['pa'],RAM_PA)
        self.assertEqual(row['actual_hex'],b'xxxx'.hex());self.assertFalse(row['opcode_matches'])
    def test_unknown_mmio_gap_and_end_never_read(self):
        for pa in (0x150c0134,0x8adf8da00,0xa40000000,0x10000000000000000):
            g=Guest(self.tmp.name);g.pas[0]=pa;r=RuntimeCodeReader(g,GEOMETRY,lambda:None)
            with self.assertRaises(ObservationError):r.read(0,VA,phase='hit')
            self.assertEqual(g.reads,[])
    def test_unmapped_raw_reply_retained_before_enable(self):
        self.g.reply='Unmapped\n'
        with self.assertRaises(ObservationError):self.r.activation_word(VA)
        self.assertEqual(self.g.reads,[])
        self.assertEqual(self.r.receipt['reads'][0]['before']['exact']['raw_reply'],'Unmapped\n')
        self.assertIn('native_code_guard_receipt',self.g.data)
    def test_type_changes_after_read_do_not_pass(self):
        self.g.type_change=True
        with self.assertRaises(ObservationError):self.r.read(3,VA,phase='hit')
        self.assertFalse(self.r.receipt['reads'][0]['verified'])
    def test_not_paused_and_invalid_cpu_never_read(self):
        self.g.running=True
        with self.assertRaises(ObservationError):self.r.read(0,VA,phase='hit')
        self.g.running=False
        other=RuntimeCodeReader(self.g,GEOMETRY,lambda:None)
        with self.assertRaises(ObservationError):other.read(4,VA,phase='hit')
        self.assertEqual(self.g.reads,[])
    def test_invalid_va_keeps_first_error_without_formatting_exception(self):
        with self.assertRaises(ObservationError):self.r.read(0,[],phase='hit')
        self.assertEqual(self.r.receipt['first_failure']['va'],'[]')
        with self.assertRaises(ObservationError):self.r.read(0,VA,phase='hit')
        self.assertEqual(len(self.r.receipt['reads']),1);self.assertEqual(self.g.reads,[])
    def test_virtual_time_and_budget_failure_are_recorded(self):
        self.r.MAX_READS=0
        with self.assertRaises(ObservationError):self.r.read(0,VA,phase='hit')
        self.assertEqual(self.g.reads,[]);self.assertIn('budget',self.r.receipt['first_failure']['reason'])
        other=RuntimeCodeReader(self.g,GEOMETRY,lambda:None);other.read(0,VA,phase='hit')
        self.assertEqual(other.receipt['reads'][0]['virtual_ns'],100)
    def test_full_four_byte_range_required(self):
        self.g.pas[0]=0x8adf8d9ff
        with self.assertRaises(ObservationError):self.r.read(0,VA,phase='hit')
        self.assertEqual(self.g.reads,[])


if __name__=='__main__':unittest.main()
