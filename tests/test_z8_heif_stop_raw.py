# SPDX-License-Identifier: GPL-2.0-or-later
import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import z8_heif_stop_raw as raw
class Tests(unittest.TestCase):
 def setup_observer(self,d,enabled=True):
  def read(pa,n):
   r.bytes_requested+=n;return (raw.ENTRY+4).to_bytes(8,'little')
  r=SimpleNamespace(read=read,bytes_requested=0);return raw.Observer(SimpleNamespace(out=Path(d)),r,enabled)
 def regs(self,pc,sp=0xa06000020,**extra):return dict(PC=pc,SP=sp,X00=0xa0496efe0,X01=0x40040,**extra)
 def test_default_off(self):
  with tempfile.TemporaryDirectory() as d:
   o=self.setup_observer(d,False);self.assertEqual(o.transition([(0,self.regs(raw.ENTRY))],{raw.ENTRY},1),[]);self.assertIsNone(o.identity);self.assertEqual(o.recovery.bytes_requested,0)
 def test_bound_raw_timeout(self):
  with tempfile.TemporaryDirectory() as d:
   o=self.setup_observer(d);self.assertEqual(o.transition([(0,self.regs(raw.ENTRY))],{raw.ENTRY},1),[raw.RETURN])
   r=self.regs(raw.RETURN,0xa06000000);r['X00']=0xffffffce
   self.assertEqual(o.transition([(1,r)],{raw.RETURN},2),[])
   x=json.loads((Path(d)/'stop-wait-raw.json').read_text());self.assertEqual(x['raw_os_signed'],-50);self.assertFalse(x['original_boolean']);self.assertTrue(x['matched'])
 def test_raw_success_and_one_receipt(self):
  with tempfile.TemporaryDirectory() as d:
   o=self.setup_observer(d);o.transition([(0,self.regs(raw.ENTRY))],{raw.ENTRY},1);r=self.regs(raw.RETURN,0xa06000000);r['X00']=0
   o.transition([(0,r)],{raw.RETURN},2);self.assertTrue(o.row['original_boolean']);self.assertEqual(o.transition([(0,r)],{raw.RETURN},3),[])
 def test_other_task_step_before_rearm(self):
  with tempfile.TemporaryDirectory() as d:
   o=self.setup_observer(d);o.transition([(0,self.regs(raw.ENTRY))],{raw.ENTRY},1)
   self.assertEqual(o.transition([(2,self.regs(raw.RETURN,0xa06001000))],{raw.RETURN},2),[raw.STEP]);self.assertFalse(o.row['matched']);self.assertEqual(o.recovery.bytes_requested,0)
   self.assertEqual(o.transition([(2,self.regs(raw.STEP,0xa06001000))],{raw.STEP},3),[raw.RETURN])
 def test_bad_entry_and_identity_and_budget(self):
  for mode in ('object','mask','caller','skip-budget','step-identity'):
   with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
    o=self.setup_observer(d);r=self.regs(raw.ENTRY)
    if mode=='object':r['X00']=0
    if mode=='mask':r['X01']=1
    if mode in ('object','mask'):
     with self.assertRaises(ValueError):o.transition([(0,r)],{raw.ENTRY},1)
     continue
    o.transition([(0,r)],{raw.ENTRY},1)
    if mode=='caller':
     o.recovery.read=lambda a,n:bytes(8)
     with self.assertRaises(ValueError):o.transition([(0,self.regs(raw.RETURN,0xa06000000))],{raw.RETURN},2)
    elif mode=='skip-budget':
     o.row['skips']=raw.MAX_SKIPS
     with self.assertRaises(ValueError):o.transition([(0,self.regs(raw.RETURN,0xa06001000))],{raw.RETURN},2)
    else:
     o.transition([(0,self.regs(raw.RETURN,0xa06001000))],{raw.RETURN},2)
     with self.assertRaises(ValueError):o.transition([(1,self.regs(raw.STEP,0xa06001000))],{raw.STEP},3)
    self.assertFalse((Path(d)/'stop-wait-raw.json').exists())
 def test_option_default_dependencies_and_identical_contacts(self):
  import z8_capture_probe as probe
  import test_z8_capture_probe as fixtures
  from unittest.mock import patch
  with patch.object(sys,'argv',['probe','--firmware','m','--qemu','q','--raw','r','--output','o']),patch.object(probe,'run') as run:probe.main()
  self.assertFalse(run.call_args.args[0].heif_stop_raw_observer)
  a=fixtures.FirstFaultSnapshotTests().menu_cycle_args();a.heif_stop_raw_observer=True
  with self.assertRaises(ValueError):probe.contact_sequence(a)
  a.heif_capture_status_observer=True;a.heif_menu_at_task800=True
  a.heif_menu_release_after_stop=True;a.heif_native_rearm_gate=True
  baseline=probe.contact_sequence(a);a.heif_stop_wait_return_only=True
  with self.assertRaises(ValueError):probe.contact_sequence(a)
  a.heif_stop_wait_return_only=False;a.heif_stop_raw_observer=False
  self.assertEqual(baseline,probe.contact_sequence(a))
 def test_secondary_points_default_off_and_intershot_bound(self):
  import z8_capture_recovery as recovery
  import test_z8_capture_recovery as fixtures
  with tempfile.TemporaryDirectory() as d:
   g,calls,v=fixtures.RecoveryTests().flag_guest(d)
   self.assertNotIn(0x41151ed4,recovery.Observer(g).points)
   g._heif_capture_status_observer=True;g._heif_stop_raw_observer=True;s=recovery.Observer(g)
   self.assertEqual(set(recovery.RAW_STOP_POINTS),{0x41151ed4,0x41151edc})
   self.assertTrue(set(recovery.STOP_WAIT_POINTS)<=set(s.points))
   self.assertFalse(s.can_arm(0x41151ed4));s.active=True;s.intershot_started=True
   self.assertTrue(s.can_arm(0x41151ed4));s.rearm_seen.add(0x41151ed4)
   self.assertFalse(s.can_arm(0x41151ed4));self.assertEqual(calls,[])
 def test_shared_ram_budget_rejection(self):
  with tempfile.TemporaryDirectory() as d:
   o=self.setup_observer(d);o.transition([(0,self.regs(raw.ENTRY))],{raw.ENTRY},1)
   def exhausted(pa,n):raise ValueError('Recovery 64 KiB RAM budget exhausted')
   o.recovery.read=exhausted
   with self.assertRaises(ValueError):o.transition([(0,self.regs(raw.RETURN,0xa06000000))],{raw.RETURN},2)
   self.assertFalse(o.row['matched']);self.assertFalse((Path(d)/'stop-wait-raw.json').exists())
if __name__=='__main__':unittest.main()
