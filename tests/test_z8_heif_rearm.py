# SPDX-License-Identifier: GPL-2.0-or-later
import json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import z8_heif_rearm as rearm

class RearmTests(unittest.TestCase):
    def test_gate_does_not_expand_existing_recovery_ram_budget(self):
        import z8_capture_recovery
        g,_,_,_=self.guest();shared=z8_capture_recovery.Observer(g)
        shared.bytes_requested=z8_capture_recovery.MAX_BYTES-49
        gate=rearm.Gate(g,True,shared=shared)
        with self.assertRaisesRegex(ValueError,'64 KiB'):gate.sample('shot2-release',0)
        self.assertLessEqual(shared.bytes_requested,z8_capture_recovery.MAX_BYTES)
        shared=z8_capture_recovery.Observer(g);shared.qom_bytes=z8_capture_recovery.MAX_QOM_BYTES
        with self.assertRaisesRegex(ValueError,'shared QOM'):
            rearm.Gate(g,True,shared=shared).sample('shot2-release',0)
    def test_scheduler_keeps_ready_or_failed_vm_paused_but_services_wait(self):
        for result in (True,False,RuntimeError('timeout')):
            calls=[]
            def sample(label,t):
                calls.append(('sample',label,t))
                if isinstance(result,Exception):raise result
                return result
            gate=SimpleNamespace(g=SimpleNamespace(qmp=SimpleNamespace(command=lambda c:calls.append(c))),sample=sample)
            peer=SimpleNamespace(handle=lambda:calls.append('handle'),now=lambda:42)
            if isinstance(result,Exception):
                with self.assertRaisesRegex(RuntimeError,'timeout'):rearm.pause_and_sample(peer,gate,'shot2-full')
            else:self.assertEqual(rearm.pause_and_sample(peer,gate,'shot2-full'),(result,42))
            self.assertEqual(calls[:2],['stop','handle']);self.assertEqual('cont' in calls,result is False)
    def guest(self):
        calls=[]
        values={rearm.NATIVE:bytes(8)+(2).to_bytes(4,'little')+(1).to_bytes(4,'little'),
                rearm.CONTEXT:(2).to_bytes(4,'little'),rearm.PERMISSION:b'\1',rearm.INHIBIT:b'\0',
                rearm.MODE_START:bytes(4),rearm.MODE_STOP:bytes(4)}
        lcd=dict(active=True,last_frame_has_video=True,video_frames=100)
        def read(a,n):calls.append(('read',a,n));return values[a]
        def command(name,**args):
            calls.append((name,args))
            if name=='query-status':return {'running':False}
            if name=='qom-get':return json.dumps(lcd)
            raise AssertionError('Observer attempted mutation')
        g=SimpleNamespace(read_bytes=read,qmp=SimpleNamespace(command=command),data={'capture_trace':{}})
        return g,calls,values,lcd
    def test_default_off_never_reads(self):
        g,calls,_,_=self.guest();gate=rearm.Gate(g,False)
        self.assertTrue(gate.sample('shot2-release',0));self.assertEqual(calls,[])
    def test_ready_then_rechecks_full(self):
        g,calls,v,lcd=self.guest();gate=rearm.Gate(g,True)
        self.assertTrue(gate.sample('shot2-release',1))
        v[rearm.PERMISSION]=b'\0'
        self.assertFalse(gate.sample('shot2-full',2))
        self.assertEqual(gate.rows[-1]['reasons'],['full-worker-permission'])
        self.assertTrue(all(c[0] in ('read','query-status','qom-get') for c in calls))
    def test_native_not_ready_and_timeout_before_input(self):
        g,_,v,_=self.guest();v[rearm.NATIVE]=bytes(8)+(1).to_bytes(4,'little')+(1).to_bytes(4,'little')
        gate=rearm.Gate(g,True)
        self.assertFalse(gate.sample('shot2-release',10))
        with self.assertRaisesRegex(RuntimeError,'Native rearm gate timeout'):
            gate.sample('shot2-release',10+rearm.WAIT_NS)
        self.assertEqual(gate.rows[-1]['status'],'timeout')
    def test_false_positive_scalar_without_video_is_rejected(self):
        g,_,_,lcd=self.guest();lcd['last_frame_has_video']=False
        gate=rearm.Gate(g,True);self.assertFalse(gate.sample('shot2-release',0))
        self.assertIn('liveview-video',gate.rows[-1]['reasons'])
    def test_all_predicates_fail_closed(self):
        for addr,bad in ((rearm.CONTEXT,bytes(4)),(rearm.PERMISSION,b'\2'),(rearm.INHIBIT,b'\1'),
                         (rearm.NATIVE,bytes(8)+(2).to_bytes(4,'little')+bytes(4))):
            g,_,v,_=self.guest();v[addr]=bad
            self.assertFalse(rearm.Gate(g,True).sample('shot2-release',0))
    def test_running_short_read_and_change_rejected(self):
        g,_,_,_=self.guest();g.qmp.command=lambda *a,**kw:{'running':True}
        with self.assertRaisesRegex(ValueError,'stopped'):rearm.Gate(g,True).sample('shot2-release',0)
        g,_,v,_=self.guest();v[rearm.NATIVE]=b'\0'
        with self.assertRaisesRegex(ValueError,'Short'):rearm.Gate(g,True).sample('shot2-release',0)
        g,_,_,_=self.guest();old=g.read_bytes;count=0
        def changed(a,n):
            nonlocal count
            if a==rearm.NATIVE:
                count+=1
                if count==2:return bytes(16)
            return old(a,n)
        g.read_bytes=changed
        with self.assertRaisesRegex(ValueError,'changed'):rearm.Gate(g,True).sample('shot2-release',0)
    def test_budgets_and_unknown_label(self):
        g,calls,_,_=self.guest();gate=rearm.Gate(g,True)
        with self.assertRaises(ValueError):gate.sample('full',0)
        self.assertEqual(calls,[])
        gate.ram_bytes=rearm.MAX_RAM
        with self.assertRaisesRegex(ValueError,'RAM budget'):gate.sample('shot2-release',0)
        g,_,_,_=self.guest();gate=rearm.Gate(g,True);gate.qom_bytes=rearm.MAX_QOM
        with self.assertRaisesRegex(ValueError,'QOM budget'):gate.sample('shot2-release',0)
        g,_,_,_=self.guest();gate=rearm.Gate(g,True);gate.samples=rearm.MAX_SAMPLES
        with self.assertRaisesRegex(ValueError,'sample budget'):gate.sample('shot2-release',0)
    def test_time_and_snapshot_receipt(self):
        g,_,_,_=self.guest();gate=rearm.Gate(g,True);gate.sample('shot2-release',100)
        self.assertEqual(gate.ram_bytes,50);self.assertFalse(gate.rows[0]['guest_state_modified'])
        self.assertIs(g.data['capture_trace']['heif_native_rearm_gate'],gate.rows)
        with self.assertRaisesRegex(ValueError,'monotonic'):gate.sample('shot2-full',99)

if __name__=='__main__':unittest.main()
