# SPDX-License-Identifier: GPL-2.0-or-later
import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import z8_capture_recovery as recovery

class RecoveryTests(unittest.TestCase):
    def test_rearm_points_are_opt_in_once_after_intershot_only(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls,_=self.flag_guest(d);g._heif_intershot_menu_cycle=True;g._heif_native_rearm_gate=True
            s=recovery.Observer(g);s.active=True;pc=0x40f447a8
            self.assertFalse(s.can_arm(pc));self.assertIn(pc,s.points)
            s.intershot_started=True;self.assertTrue(s.can_arm(pc))
            receipt=s.point(pc,0,{},1);self.assertFalse(s.can_arm(pc))
            r=json.loads((g.out/receipt['path']).read_text());self.assertIn('resume_mode',r['memory'])
            self.assertEqual(r['observation_phase'],'intershot-once-per-pc')
            self.assertIn('stop_mode',r['memory']);self.assertEqual(r['snapshot_layout'],'flag800-compact-v1')
            with self.assertRaises(ValueError):s.point(pc,0,{},2)
            self.assertNotIn(pc,recovery.Observer(self.flag_guest(d)[0]).points)
    def test_menu_experiment_reuses_bounded_existing_points_only(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls,_=self.flag_guest(d);g._heif_intershot_menu_cycle=True
            s=recovery.Observer(g)
            self.assertLess(len(s.points),len(recovery.POINTS))
            self.assertLessEqual(set(s.points),set(recovery.POINTS))
            self.assertIn(0x40f44b38,s.points);self.assertIn(0x40f44734,s.points)
            self.assertNotIn(0x40f555dc,s.points)
            s.active=True
            with self.assertRaises(ValueError):s.point(0x40f555dc,0,{},0)
            self.assertEqual(calls,[])
            self.assertEqual(recovery.Observer(self.flag_guest(d)[0]).points,recovery.POINTS)
    def test_early_setters_require_explicit_enable_and_are_once_per_pc(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls,_=self.flag_guest(d);s=recovery.Observer(g);pc=0x40f4d35c
            self.assertFalse(s.can_arm(pc));self.assertIsNone(s.point(pc,0,{},0))
            self.assertEqual(calls,[])
            s.enable_early();self.assertTrue(s.can_arm(pc))
            r=s.point(pc,0,{},1);self.assertFalse(s.can_arm(pc))
            n=len(calls)
            with self.assertRaises(ValueError):s.point(pc,0,{},2)
            self.assertEqual(len(calls),n)
            self.assertEqual(json.loads((g.out/r['path']).read_text())['observation_phase'],'early-first-hit')
            self.assertIsNone(s.contact('menu-test',3))
            s.active=True;self.assertTrue(s.can_arm(pc))
    def test_recovery_request13_snapshots_original_argument_and_gate_state(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls,values=self.flag_guest(d);values[0xa03b934b8]=bytes(40)+(4).to_bytes(4,'little')+bytes(4)
            values[0xa06c4ee00]=bytes(range(12))
            s=recovery.Observer(g);s.active=True
            r=s.point(0x40f463bc,0,{'X00':0xa06c4ee00,'X01':0x40f4bf84},1)
            row=json.loads((g.out/r['path']).read_text())
            self.assertEqual(row['request13_argument']['hex'],bytes(range(12)).hex())
            self.assertEqual(int.from_bytes(bytes.fromhex(row['memory']['native_request_queue']['hex'])[40:44],'little'),4)
            self.assertLessEqual(s.bytes_requested,512)
            self.assertTrue(all(c[0]=='read' for c in calls))
    def test_request13_rejects_non_ram_argument_without_reading_it(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls,_=self.flag_guest(d);s=recovery.Observer(g);s.active=True
            with self.assertRaises(ValueError):s.point(0x40f463bc,0,{'X00':0x16020000},1)
            self.assertTrue(all(c[1]!=0x16020000 for c in calls))
    def flag_guest(self,path):
        g,calls=self.guest(path)
        obj=0xa049871c8;attr=0xa02900000
        values={0xa03bba4f8:(17).to_bytes(4,'little'),
                0xa02aaaad4:(100).to_bytes(4,'little'),
                0xa049ae038+16*8:obj.to_bytes(8,'little'),
                obj:bytes(16)+attr.to_bytes(8,'little')+(0x800).to_bytes(4,'little')+bytes(4),
                attr:(2).to_bytes(4,'little')}
        def read(pa,n):
            calls.append(('read',pa,n));return values.get(pa,bytes(n))
        g.read_bytes=read
        return g,calls,values
    def test_flag800_points_are_compact_readonly_and_resolve_actual_object(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls,_=self.flag_guest(d);s=recovery.Observer(g);s.active=True
            r=s.point(0x40f44b38,0,{'X00':0xa03bba4f8,'X01':0x800},1)
            row=json.loads((g.out/r['path']).read_text())
            self.assertEqual(row['event_flag']['pattern'],0x800)
            self.assertEqual(row['event_flag']['attribute'],2)
            self.assertEqual(row['event_flag']['object'],'0xa049871c8')
            self.assertTrue(row['event_flag']['identity_stable'])
            self.assertLessEqual(s.bytes_requested,300)
            self.assertTrue(all(c[0]=='read' for c in calls))
    def test_flag800_bad_handle_pointer_and_identity_change_fail_closed(self):
        for mode in ('handle','pointer','changed'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                g,calls,values=self.flag_guest(d)
                if mode=='handle':values[0xa03bba4f8]=bytes(4)
                if mode=='pointer':values[0xa049ae038+16*8]=(0x16020000).to_bytes(8,'little')
                if mode=='changed':
                    original=g.read_bytes;reads=0
                    def read(pa,n):
                        nonlocal reads
                        if pa==0xa03bba4f8:
                            reads+=1
                            if reads>1:return (18).to_bytes(4,'little')
                        return original(pa,n)
                    g.read_bytes=read
                s=recovery.Observer(g);s.active=True
                with self.assertRaises(ValueError):s.point(0x40f44b38,0,{},1)
                row=json.loads(next((g.out/'capture-recovery').glob('*.json')).read_text())
                self.assertFalse(row['collection_complete'])
                self.assertTrue(all(c[0]=='read' and c[1]>=0xa00000000 for c in calls))
    def test_flag800_stays_off_and_budget_has_no_new_io_on_exhaustion(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls,_=self.flag_guest(d);s=recovery.Observer(g)
            self.assertIsNone(s.point(0x40f44b38,0,{},0));self.assertEqual(calls,[])
            s.active=True;s.bytes_requested=recovery.MAX_BYTES
            with self.assertRaises(ValueError):s.point(0x40f44b38,0,{},0)
            self.assertEqual(calls,[])
    def test_compact_points_do_not_increase_existing_worst_case_budget(self):
        old=sum(n for _,n in recovery.REGIONS.values())
        compact=sum(n for _,n in recovery.FLAG_REGIONS.values())+64
        self.assertLess(compact,old)
        self.assertLess(sum(n for _,n in recovery.POLICY_REGIONS.values())+64+12,old)
        # Existing ten contacts plus conservative stack reads at every event.
        contact_extra=sum(n for _,n in recovery.SCALARS.values())+44
        self.assertLessEqual(64*(old+8)+10*contact_extra,recovery.MAX_BYTES)
    def guest(self,path):
        calls=[]
        def read(pa,n):calls.append(('read',pa,n));return bytes(n)
        def qmp(name,**args):
            calls.append((name,args));self.assertEqual(name,'qom-get');return '{}'
        return SimpleNamespace(out=Path(path),read_bytes=read,qmp=SimpleNamespace(command=qmp)),calls
    def test_inactive_default_and_non_photo_contacts_do_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls=self.guest(d);s=recovery.Observer(g)
            self.assertIsNone(s.contact('full',0));self.assertEqual(calls,[])
            s.active=True;self.assertIsNone(s.contact('menu-j',0));self.assertEqual(calls,[])
    def test_readonly_contact_snapshot_and_registered_branch(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls=self.guest(d);s=recovery.Observer(g);s.active=True
            row=s.contact('shot2-full',123)
            saved=json.loads((g.out/row['path']).read_text());self.assertTrue(saved['collection_complete'])
            self.assertIn('full_worker_hold',saved['readiness'])
            row=s.point(0x40f44b3c,1,{'PC':0x40f44b3c,'X00':0,'X19':1},125)
            saved=json.loads((g.out/row['path']).read_text());self.assertEqual(saved['registers']['X00'],'0x0')
            self.assertTrue(all(x[0] in ('read','qom-get') for x in calls))
            self.assertTrue(all(x[2]<=512 for x in calls if x[0]=='read'))
    def test_limits_and_unknown_point_before_additional_io(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls=self.guest(d);s=recovery.Observer(g);s.active=True
            with self.assertRaises(ValueError):s.point(0x1234,0,{},0)
            s.events=recovery.MAX_EVENTS
            with self.assertRaises(ValueError):s.contact('full',0)
            self.assertEqual(calls,[])
            s.events=0;s.bytes_requested=recovery.MAX_BYTES
            with self.assertRaises(ValueError):s.contact('full',0)
            self.assertEqual(calls,[])
    def test_short_read_preserved_as_failed_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls=self.guest(d);g.read_bytes=lambda pa,n:bytes(n-1);s=recovery.Observer(g);s.active=True
            with self.assertRaises(ValueError):s.contact('full',0)
            files=list((g.out/'capture-recovery').glob('*.json'));self.assertEqual(len(files),1)
            self.assertFalse(json.loads(files[0].read_text())['collection_complete'])
    def test_shared_policy_helper_records_original_saved_caller(self):
        with tempfile.TemporaryDirectory() as d:
            g,calls=self.guest(d);sp=0xa06c4ed00
            g.read_bytes=lambda pa,n:(0x40f44d88).to_bytes(8,'little') if pa==sp+8 else bytes(n)
            s=recovery.Observer(g);s.active=True
            row=s.point(0x40f5d8a0,0,{'SP':sp,'X08':1},10)
            saved=json.loads((g.out/row['path']).read_text())
            self.assertTrue(saved['caller_in_full_start_path']);self.assertEqual(saved['saved_caller_lr'],'0x40f44d88')
if __name__=='__main__':unittest.main()
