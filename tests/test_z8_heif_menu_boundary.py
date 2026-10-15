# SPDX-License-Identifier: GPL-2.0-or-later
import sys,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import z8_heif_menu_boundary as boundary
import z8_capture_recovery as recovery

class BoundaryTests(unittest.TestCase):
    def test_prepare_only_resumes_while_waiting_and_holds_ready(self):
        m,s,c,d,r=self.setup_guest();commands=[];armed=[]
        peer=SimpleNamespace(menu_boundary=m,g=SimpleNamespace(qmp=SimpleNamespace(command=lambda x:commands.append(x))),
            handle=lambda:None,now=lambda:10,arm=lambda pc,label:armed.append((pc,label)))
        self.assertFalse(boundary.prepare(peer));self.assertEqual(commands,['stop','cont'])
        self.assertEqual(armed[0][0],boundary.ENTER)
        self.assertFalse(boundary.prepare(peer));self.assertEqual(commands,['stop','cont'])
        m.observe(boundary.ENTER,0,r,11);m.observe(boundary.AFTER,0,r,12)
        self.assertTrue(boundary.prepare(peer));self.assertEqual(commands,['stop','cont'])
        m.sent(12)
        with self.assertRaises(ValueError):boundary.prepare(peer)
    def setup_guest(self):
        calls=[];parent=0xa028283f8
        data={0xa03bbb430:bytes(8)+(2).to_bytes(4,'little')+(1).to_bytes(4,'little'),
              0xa03bba97c:(2).to_bytes(4,'little'),0xa03bd042c:b'\1',
              0xa028296f0:parent.to_bytes(8,'little'),parent+0x680:bytes(28),
              parent+0x2b0:bytes(4),0xa028296f0+0x230:bytes(4)}
        def read(pa,n):calls.append((pa,n));return data[pa]
        g=SimpleNamespace(data={'capture_trace':{}},read_bytes=read)
        shared=recovery.Observer(g);m=boundary.MenuBoundary(g,True,shared)
        regs={'X19':0x800,'SP':0xa06c4ed70,'PC':boundary.ENTER}
        return m,shared,calls,data,regs
    def test_off_has_no_reads_or_receipt(self):
        g=SimpleNamespace(data={'capture_trace':{}});m=boundary.MenuBoundary(g,False,None)
        self.assertFalse(m.holds('shot2-menu'));self.assertIsNone(m.arm(0));self.assertEqual(g.data['capture_trace'],{})
    def test_native_two_point_sequence_holds_until_menu(self):
        m,s,c,d,r=self.setup_guest();self.assertEqual(m.arm(10),boundary.ENTER)
        self.assertEqual(m.observe(boundary.ENTER,0,r,11),boundary.AFTER)
        self.assertIsNone(m.observe(boundary.AFTER,0,dict(r,PC=boundary.AFTER),12))
        self.assertTrue(m.holds('shot2-menu'));self.assertFalse(m.holds('shot2-full'))
        self.assertEqual(s.events,2);self.assertGreater(s.bytes_requested,0)
        m.sent(12);self.assertFalse(m.holds('shot2-menu'));self.assertEqual(m.row['status'],'sent')
        with self.assertRaises(ValueError):m.sent(13)
    def test_wrong_mask_or_identity_fails_closed(self):
        for cpu,sp,mask in ((1,0xa06c4ed70,0x800),(0,0xa06c4ed80,0x800),(0,0xa06c4ed70,0x1800)):
            m,s,c,d,r=self.setup_guest();m.arm(10);m.observe(boundary.ENTER,0,r,11)
            with self.assertRaises(ValueError):m.observe(boundary.AFTER,cpu,dict(r,SP=sp,X19=mask),12)
            self.assertFalse(m.holds('shot2-menu'))
    def test_not_ready_cannot_send_menu(self):
        m,s,c,d,r=self.setup_guest();d[0xa03bd042c]=b'\0';m.arm(10);m.observe(boundary.ENTER,0,r,11)
        with self.assertRaisesRegex(ValueError,'native readiness'):m.observe(boundary.AFTER,0,r,12)
        self.assertFalse(m.holds('shot2-menu'));self.assertEqual(m.row['status'],'failed')
    def test_boundaries_and_shared_budgets(self):
        m,s,c,d,r=self.setup_guest();m.arm(10)
        with self.assertRaisesRegex(RuntimeError,'timeout'):m.check(10+boundary.WAIT_NS)
        m,s,c,d,r=self.setup_guest();s.events=recovery.MAX_EVENTS;m.arm(10)
        with self.assertRaises(ValueError):m.observe(boundary.ENTER,0,r,11)
        m,s,c,d,r=self.setup_guest();m.arm(10);m.observe(boundary.ENTER,0,r,11);s.bytes_requested=recovery.MAX_BYTES
        with self.assertRaises(ValueError):m.observe(boundary.AFTER,0,r,12)
    def test_stale_duplicate_and_missing_parent_are_rejected(self):
        m,s,c,d,r=self.setup_guest();m.arm(10)
        with self.assertRaises(ValueError):m.observe(boundary.AFTER,0,r,11)
        m,s,c,d,r=self.setup_guest();m.arm(10);m.observe(boundary.ENTER,0,r,11)
        d[0xa028296f0]=bytes(8)
        with self.assertRaises(ValueError):m.observe(boundary.AFTER,0,r,12)
    def test_only_half_contact_arms_new_branch_points(self):
        m,s,c,d,r=self.setup_guest();g=s.g;g._heif_menu_at_task800=True
        s=recovery.Observer(g);s.active=True;s.intershot_started=True
        for pc in recovery.HALF_POINTS:self.assertFalse(s.can_arm(pc))
        s.half_started=True
        for pc in recovery.HALF_POINTS:self.assertTrue(s.can_arm(pc))

if __name__=='__main__':unittest.main()
