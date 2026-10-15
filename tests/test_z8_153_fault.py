# SPDX-License-Identifier: GPL-2.0-or-later
import copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import z8_153_fault as observer

class Fault153Tests(unittest.TestCase):
    def fixture(self,d):
        calls=[];banks=[]
        for p in (0xa02c97500,0xa02c97d00):
            fields=[0]*84;fields[0x48//4]=p&0xffffffff;fields[0x4c//4]=p>>32
            banks.append(dict(fields=fields,writes=70,reset_pulses=1))
        states={'engine153-config-state':dict(enabled=True,banks=banks),
                'engine153-frame-pass-state':dict(enabled=False,banks=[{'jobs':0},{'jobs':1}]),
                'engine153-inplace-identity-state':dict(enabled=True,jobs=1,consumed_dma_frame=1)}
        def command(name,**kw):
            calls.append((name,kw))
            if name=='query-status':return {'running':False}
            self.assertEqual(name,'qom-get');return json.dumps(states[kw['property']])
        def read(pa,n):calls.append(('read',pa,n));return bytes([pa&255])*n
        return SimpleNamespace(out=Path(d),qmp=SimpleNamespace(command=command),read_bytes=read),calls,states
    def test_default_off_or_foreign_fault_has_no_io(self):
        with tempfile.TemporaryDirectory() as d:
            g,c,s=self.fixture(d)
            self.assertIsNone(observer.capture(g,'Z9_UNKNOWN write addr=0x15340004 size=4 value=0x1'))
            g._still_153_observer_enabled=True
            self.assertIsNone(observer.capture(g,'Z9_UNKNOWN write addr=0x15100008 size=4 value=0x1'))
            self.assertEqual(c,[])
    def test_both_real_program_blocks_and_qom_saved_without_writes(self):
        with tempfile.TemporaryDirectory() as d:
            g,c,s=self.fixture(d);g._still_153_observer_enabled=True
            r=observer.capture(g,'Z9_UNKNOWN write addr=0x15340004 size=4 value=0x1')
            self.assertTrue(r['collection_complete']);self.assertEqual(r['ram_bytes'],3968)
            self.assertEqual(len(r['programs']),2)
            self.assertTrue(all(x[0] in ('query-status','qom-get','read') for x in c))
            self.assertTrue(all(x[2]<=512 for x in c if x[0]=='read'))
            self.assertEqual((g.out/'153-fault/bank0-program.bin').stat().st_size,0x7c0)
            self.assertEqual(r['states']['engine153-inplace-identity-state']['jobs'],1)
            with self.assertRaises(FileExistsError):observer.capture(g,'Z9_UNKNOWN addr=0x15340004')
    def test_non_ram_pointer_short_read_and_identity_change_fail(self):
        for mode in ('pointer','short','changed','running'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                g,c,s=self.fixture(d);g._still_153_observer_enabled=True
                if mode=='pointer':s['engine153-config-state']['banks'][0]['fields'][0x48//4]=0
                if mode=='short':g.read_bytes=lambda p,n:bytes(n-1)
                if mode=='changed':
                    original=g.read_bytes
                    def read(p,n):s['engine153-config-state']['banks'][0]['fields'][0x40//4]=7;return original(p,n)
                    g.read_bytes=read
                if mode=='running':g.qmp.command=lambda *a,**k:{'running':True}
                with self.assertRaises(ValueError):observer.capture(g,'Z9_UNKNOWN addr=0x15340004')
                r=json.loads((g.out/'153-fault/state.json').read_text());self.assertFalse(r['collection_complete'])
    def test_qom_budget_exhaustion_stops_before_ram_reads(self):
        with tempfile.TemporaryDirectory() as d:
            g,c,s=self.fixture(d);g._still_153_observer_enabled=True
            s['engine153-config-state']['large']='x'*(128*1024)
            with self.assertRaises(ValueError):observer.capture(g,'Z9_UNKNOWN addr=0x15340004')
            self.assertFalse(any(x[0]=='read' for x in c))
    def test_native_sync_caller_has_bounded_stack_and_request(self):
        with tempfile.TemporaryDirectory() as d:
            g,c,s=self.fixture(d);g._still_153_observer_enabled=True
            regs={'0':'PC=40c28974 SP=a06c9de00 X30=40cba4d4 X23=a03b69c20'}
            r=observer.capture(g,'Z9_UNKNOWN cpu=0 addr=0x15340004',regs)
            self.assertEqual(r['ram_bytes'],3968+256+0x134)
            self.assertEqual(r['stack']['bytes'],256);self.assertEqual(r['sync_request']['address'],'0xa03b69c20')
if __name__=='__main__':unittest.main()
