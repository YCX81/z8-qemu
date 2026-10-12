# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise optional public CapturePeer hooks with no real guest."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import z8_capture_probe as probe
from z8_writer_integration import RESERVED
from z8_writer_observer import SITES
from test_z8_writer_integration import Guest,Journal
from test_z8_writer_observer import regs,SP,CTX,REC,OUTER


class ProbeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.g=Guest(self.tmp.name);self.j=Journal()
        self.g._native_writer_observer_enabled=True;self.g._writer_host_check=self.g.budget
        self.g.data['capture_pixels_enabled']=False
        self.patches=[patch.object(probe.Peer,'__init__',lambda obj,g,**kw:(setattr(obj,'g',g),setattr(obj,'points',{})) and None),
            patch.object(probe,'load_bindings',return_value={}),
            patch.object(probe,'load_configuration',return_value={'boot':{'points':{'boot4':0x40160400}}}),
            patch.object(probe,'capture_bindings',return_value={}),
            patch.object(probe,'observation_points',return_value={}),
            patch.object(probe.z8_capture_resources,'observation_points',return_value={})]
        for p in self.patches:p.start()
        self.peer=probe.CapturePeer(self.g,self.j)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_initial_reservation_and_menu_do_not_activate(self):
        self.assertFalse(RESERVED&self.peer.trace_points.keys())
        self.assertFalse(self.peer.writer_session.enabled)
        with patch.object(probe.Peer,'enqueue_external'):
            self.peer.begin('menu',bytes(124),bytes(44))
        self.assertFalse(RESERVED&self.peer.points.keys())
        self.assertFalse(self.peer.writer_session.enabled)
    def test_shutter_is_only_activation_and_contact_does_not_rearm(self):
        with patch.object(probe.CseqObserver,'enable'):
            self.peer.start_shutter_observation(repeat=False)
        pc=SITES['writer'].call;before=self.g.rsp_log.count(f'Z1,{pc:x},4')
        with patch.object(probe.Peer,'enqueue_external'):
            self.peer.begin('half',bytes(124),bytes(44))
        self.assertEqual(self.g.rsp_log.count(f'Z1,{pc:x},4'),before)
        with self.assertRaises(RuntimeError):self.peer.arm(pc,'competing_parent')
    def test_real_handle_feeds_before_parent_disarm_and_returns_handled(self):
        self.peer.writer_session.enable()
        self.g.registers[0]=regs(SITES['writer'].call,SP,X00=0,X01=1,X02=REC,X20=REC,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        def parent_handle(obj):
            self.assertNotIn(SITES['writer'].call,obj.points)
            self.assertIn(SITES['writer'].target,obj.points)
            return False
        with patch.object(probe.Peer,'handle',parent_handle):self.assertTrue(self.peer.handle())
        self.assertEqual(self.peer.observation_count,0)
        self.assertEqual(self.peer.writer_session.observer.events[0]['kind'],'writer_call')
    def test_default_disabled_keeps_legacy_point(self):
        self.g._native_writer_observer_enabled=False
        peer=probe.CapturePeer(self.g,self.j)
        self.assertIsNone(peer.writer_session)
        self.assertIn(SITES['writer'].call,peer.trace_points)
    def test_writer_cleanup_error_still_collects_nvme_and_closes_parent(self):
        order=[]
        def broken_close(**kw):order.append('writer');raise RuntimeError('pending cleanup')
        peer=SimpleNamespace(writer_session=SimpleNamespace(close=broken_close),close=lambda:order.append('peer'))
        self.g.data.update(capture_trace={},nvme_trace={'enabled':True})
        budget=SimpleNamespace(report=lambda:{'limit_host_seconds':1200})
        def collect(*a,**kw):order.append('nvme');return {'collection_complete':True}
        with patch.object(probe.z8_nvme_evidence,'collect',collect),patch.object(probe,'state',return_value={}):
            with self.assertRaises(RuntimeError):probe.finalize_trace(self.g,peer,budget)
        self.assertEqual(order,['writer','nvme','peer'])
    def test_public_entry_keeps_existing_limits_and_collectors(self):
        source=Path(probe.__file__).read_text()
        self.assertIn("choices=range(15,601)",source)
        self.assertIn("primary-d980-state",source)
        self.assertIn("z8_nvme_evidence.collect",source)
        self.assertIn("capture_d980(self.g",source)
        import contextlib,io
        argv=['z8_capture_probe','--firmware','main.bin','--qemu','qemu',
              '--raw','input.raw','--output','fresh-output','--seconds','1200']
        with patch.object(sys,'argv',argv),patch.object(probe,'run') as run,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):probe.main()
        run.assert_not_called()
    def test_cli_defaults_off_and_requires_explicit_flag(self):
        argv=['z8_capture_probe','--firmware','main.bin','--qemu','qemu',
              '--raw','input.raw','--output','fresh-output']
        for extra,wanted in (([],False),(['--native-writer-observer'],True)):
            with patch.object(sys,'argv',argv+extra),patch.object(probe,'run') as run:
                probe.main()
            self.assertIs(run.call_args.args[0].native_writer_observer,wanted)
    def test_disabled_handle_never_constructs_or_queries_writer(self):
        self.g._native_writer_observer_enabled=False
        with patch.object(probe,'WriterSession',side_effect=AssertionError('disabled observer constructed')):
            peer=probe.CapturePeer(self.g,self.j)
        self.assertIsNone(peer.writer_session)
        self.assertIn(SITES['writer'].call,peer.trace_points)
        self.assertEqual(self.g.commands,[])


if __name__=='__main__':unittest.main()
