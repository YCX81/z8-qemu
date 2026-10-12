# SPDX-License-Identifier: GPL-2.0-or-later
"""No-QEMU integration tests for breakpoint ownership and paused translation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import json
import tempfile
import unittest
from test_z8_writer_observer import Fake,Harness,regs,SP,CTX,REC,OUTER,FILE
from z8_writer_observer import EXPECTED,SITES,MAIN_SHA,ObservationError
from z8_writer_integration import WriterSession,without_reserved,BINARY_SHA,IMAGE_RANGES
from test_z8_writer_code_reader import TREE


class QMP:
    def __init__(self,g):self.g=g;self.running=False;self.size=1024<<20;self.ranges=IMAGE_RANGES
    def command(self,name,**kwargs):
        self.g.commands.append((name,kwargs))
        if name=='query-status':return {'running':self.running}
        if name=='stop':self.running=False;return {}
        if name=='query-memory-size-summary':return {'base-memory':self.size,'plugged-memory':0}
        if name=='qom-get' and kwargs['property']=='image-bank-state':
            return json.dumps({'enabled':True,'software_extents_only':True,
                              'ranges':[{'base':hex(a),'end':hex(b)} for a,b in self.ranges]})
        if name=='qom-get' and kwargs['property']=='runtime-clock-state':return json.dumps({'virtual_ns':self.g.t})
        raise AssertionError('Unexpected QMP operation '+name)
    def hmp(self,command,cpu=0):
        self.g.commands.append((command,cpu))
        if command=='info registers':return ' '.join(f'{k}={v:x}' for k,v in self.g.registers[cpu].items())+' PSTATE=60000004'
        if command=='info mtree -f':return TREE
        if command.startswith('gva2gpa '):
            va=int(command.split()[1],0)
            return 'gpa: '+hex(self.g.fake.translate(cpu,va)+
                (self.g.code_delta.get(cpu,0) if 0x40000000<=va<0x48000000 else 0))+'\n'
        raise AssertionError(command)


class Guest:
    def __init__(self,out):
        self.fake=Fake();self.commands=[];self.t=0;self.out=Path(out);self.deadline=False
        self.qmp=QMP(self);self.rsp_log=[];self.fail_packet=None;self.breakpoints=set()
        self.data={'firmware_sha256':MAIN_SHA,'qemu_sha256':BINARY_SHA}
        self.command=['qemu','-M','nikon-z8-bringup,image-bank-model=on','-m','1024M','-smp','4']
        self.ram_mib=1024;self.registers={c:regs(0x40160000,SP+c*0x1000) for c in range(4)}
        self.code_delta={};self.code_reads=[];self.bad_code=False
    def read_bytes(self,pa,n):
        candidates={pa,*[pa-d for d in self.code_delta.values()]}
        found=[a for a in candidates if a in EXPECTED]
        if found and n==4:
            self.code_reads.append(pa);return b'xxxx' if self.bad_code else EXPECTED[found[0]]
        return self.fake.read(pa,n)
    def rsp(self,cmd):
        self.rsp_log.append(cmd)
        if cmd==self.fail_packet:raise TimeoutError('injected RSP transport failure')
        verb,a,n=cmd.split(',');a=int(a,16)
        if verb=='Z1':self.breakpoints.add(a)
        elif verb=='z1':self.breakpoints.discard(a)
        else:raise AssertionError('Only passive breakpoint packets allowed')
        return 'OK'
    def budget(self):
        if self.deadline:raise TimeoutError('host deadline')


class Journal:
    def __init__(self):self.events=[]
    def add(self,kind,t_ns,**fields):self.events.append(dict(kind=kind,t_ns=t_ns,**fields))


class Peer:
    def __init__(self,g):self.g=g;self.points={0x40160200:'unrelated'}


class IntegrationHarness(Harness):
    def __init__(self,path):
        self.g=Guest(path);self.f=self.g.fake;self.p=Peer(self.g);self.j=Journal()
        self.s=WriterSession(self.p,self.j,self.g.budget);self.s.enable()
        self.o=self.s.observer;self.t=0
    def hit(self,r,cpu=0):
        self.t+=1;self.g.t=self.t
        self.g.registers={c:r if c==cpu else regs(0x40160000,SP+c*0x1000) for c in range(4)}
        self.s.handle();return self.s.last_plan


class IntegrationTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.h=IntegrationHarness(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_complete_passive_lifecycle(self):
        h=self.h;h.begin();h.segment(100,4);h.end();r=h.s.close(reason='normal')
        self.assertTrue(r['interval_closed']);self.assertTrue(r['observed_writer_lifetimes'])
        self.assertEqual(h.p.points,{0x40160200:'unrelated'})
        self.assertFalse(h.g.breakpoints)
        self.assertTrue(all(x.startswith(('Z1,','z1,')) for x in h.g.rsp_log))
        self.assertEqual(len([e for e in h.j.events if e['kind']=='file_writer_return']),1)
    def test_reserved_points_excluded_before_contacts(self):
        d={pc:'legacy' for pc in EXPECTED};d[0x1]='other'
        self.assertEqual(without_reserved(d),{0x1:'other'})
        self.assertEqual(without_reserved(without_reserved(d)),{0x1:'other'})
    def test_activation_collision_does_not_steal_foreign_point(self):
        g=Guest(self.tmp.name);p=Peer(g);pc=SITES['writer'].call;p.points[pc]='foreign'
        s=WriterSession(p,Journal(),g.budget)
        with self.assertRaises(ObservationError):s.enable()
        self.assertEqual(p.points[pc],'foreign');self.assertEqual(g.rsp_log,[])
    def test_bad_plan_prevalidated_before_any_remove(self):
        h=self.h;pc=SITES['writer'].call
        with self.assertRaises(ObservationError):h.s.apply_plan({'remove':[pc],'add':[0x40160200]})
        self.assertIn(pc,h.p.points);self.assertIn(pc,h.g.breakpoints)
        self.assertFalse(h.s.observer.summary()['interval_closed'])
    def test_label_changed_mid_run_is_failure_not_disarm(self):
        h=self.h;pc=SITES['writer'].call;h.p.points[pc]='foreign'
        with self.assertRaises(ObservationError):h.s.handle()
        report=h.s.close(reason='ownership failure')
        self.assertEqual(h.p.points[pc],'foreign');self.assertTrue(report['cleanup_errors'])
    def test_plan_transport_failure_latches_gap_and_cleanup_is_owned(self):
        h=self.h;pc=SITES['writer'].call;target=SITES['writer'].target
        h.g.fail_packet=f'Z1,{target:x},4'
        with self.assertRaises(ObservationError):h.call('writer',X00=0,X01=1,X02=REC,X20=REC,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        h.g.fail_packet=None;r=h.s.close(reason='transport failure')
        self.assertFalse(r['interval_closed']);self.assertTrue(r['gaps'])
        self.assertEqual(h.p.points,{0x40160200:'unrelated'})
    def test_deadline_pending_return_stays_incomplete(self):
        h=self.h;h.begin();h.g.deadline=True
        with self.assertRaises(ObservationError):h.s.handle()
        r=h.s.close(reason='deadline')
        self.assertFalse(r['interval_closed']);self.assertEqual(len(r['lifetimes']),1)
        self.assertFalse(r['lifetimes'][0]['result_store_observed'])
    def test_no_writer_never_invents_success_or_file(self):
        r=self.h.s.close(reason='bounded no writer')
        self.assertTrue(r['interval_closed']);self.assertFalse(r['observed_writer_lifetimes'])
        self.assertEqual(r['lifetimes'],[]);self.assertFalse(r['native_accepted'])
    def test_multi_cpu_not_deduplicated(self):
        h=self.h;r=regs(SITES['writer'].call)
        h.g.registers[0]=r;h.g.registers[1]=r
        with self.assertRaises(ObservationError):h.s.handle()
        self.assertFalse(h.s.close(reason='SMP ambiguity')['interval_closed'])
    def test_runtime_ram_geometry_must_match_launch(self):
        for change in ('size','gap'):
            g=Guest(self.tmp.name);p=Peer(g)
            if change=='size':g.qmp.size=512<<20
            else:g.qmp.ranges=((0x840000000,0x900000000),)
            with self.assertRaises(ObservationError):WriterSession(p,Journal(),g.budget).enable()
            self.assertEqual(g.rsp_log,[])
    def test_running_activation_refused(self):
        g=Guest(self.tmp.name);g.qmp.running=True
        with self.assertRaises(ObservationError):WriterSession(Peer(g),Journal(),g.budget).enable()
        self.assertEqual(g.rsp_log,[])
    def test_a_new_guard_cannot_be_armed_under_another_cpu(self):
        h=self.h;r=regs(SITES['writer'].call,X00=0,X01=1,X02=REC,X20=REC,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        h.g.registers[0]=r;h.g.registers[1]=regs(SITES['writer'].target)
        with self.assertRaises(ObservationError):h.s.handle()
        self.assertFalse(h.s.close(reason='guard overlap')['interval_closed'])
    def test_actual_cpu2_hit_uses_its_fresh_mapping_not_cpu0(self):
        h=self.h;pc=SITES['writer'].call
        h.g.code_delta={0:0x9c0600000,2:0x9c0900000}
        h.hit(regs(pc,X00=0,X01=1,X02=REC,X20=REC,X24=CTX,X25=OUTER,X26=CTX+0xc0),cpu=2)
        row=h.s.code_reader.receipt['reads'][-1]
        self.assertEqual(row['cpu'],2);self.assertEqual(row['phase'],'hit')
        self.assertEqual(row['before']['exact']['pa'],pc+h.g.code_delta[2])
        self.assertEqual(h.g.code_reads[-1],pc+h.g.code_delta[2])
        self.assertIsNone(h.s.read_cpu);self.assertIsNone(h.s.code_phase)
    def test_failed_actual_cpu2_opcode_survives_close(self):
        h=self.h;h.g.code_delta={2:0x9c0900000};h.g.bad_code=True;pc=SITES['writer'].call
        with self.assertRaises(ObservationError):h.hit(regs(pc),cpu=2)
        report=h.s.close(reason='bad translated opcode')
        saved=json.loads((h.g.out/'native-code-guards.json').read_text())
        self.assertEqual(saved['first_failure']['cpu'],2)
        self.assertEqual(saved['reads'][-1]['before']['exact']['pa'],pc+h.g.code_delta[2])
        self.assertFalse(saved['reads'][-1]['opcode_matches'])
        self.assertEqual(report['code_guard']['first_failure'],saved['first_failure'])
        self.assertFalse(report['interval_closed']);self.assertFalse(h.g.breakpoints)
    def test_failed_activation_retains_code_receipt_and_installs_nothing(self):
        g=Guest(self.tmp.name);g.bad_code=True;p=Peer(g);s=WriterSession(p,Journal(),g.budget)
        with self.assertRaises(ObservationError):s.enable()
        self.assertFalse(s.enabled);self.assertEqual(g.rsp_log,[])
        report=s.close(reason='activation opcode failure')
        saved=json.loads((g.out/'native-code-guards.json').read_text())
        self.assertEqual(saved['first_failure']['cpu'],0)
        self.assertEqual(saved['first_failure']['va'],next(iter(EXPECTED)))
        self.assertEqual(report['code_guard']['reads'],1)
    def test_finalize_identity_failure_survives_close_with_actual_before_and_after(self):
        h=self.h;h.begin();h.segment();h.call('final',X00=FILE,X01=100,X19=REC,X22=FILE)
        h.f.put(FILE+8,'IiQQQ',0,7,0,100,0);h.f.put(FILE+0x70,'I',1)
        with self.assertRaises(ObservationError):h.ret('final',X00=0x04320080,X19=REC,X22=FILE)
        report=h.s.close(reason='unexpected final handle')
        saved=json.loads((h.g.out/'native-writer-observations.json').read_text())
        failure=saved['first_file_failure']
        self.assertEqual(failure['decoded']['handle'],7)
        self.assertEqual(failure['previous_valid']['handle'],3)
        self.assertEqual(len(bytes.fromhex(failure['raw_hex'])),0xa0)
        self.assertEqual(len(bytes.fromhex(failure['previous_raw_hex'])),0xa0)
        self.assertTrue(failure['translation']['segments'])
        self.assertEqual(saved['lifetimes'][0]['opened_handle'],3)
        self.assertFalse(report['interval_closed']);self.assertFalse(h.g.breakpoints)


if __name__=='__main__':unittest.main()
