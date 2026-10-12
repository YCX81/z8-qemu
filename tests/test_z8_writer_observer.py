# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic passive-observer tests. No guest or firmware execution."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import struct
import unittest
from z8_writer_observer import (Memory, WriterObserver, ObservationError, MAIN_SHA,
                             EXPECTED, SITES, parse_gpa, verify_main,hmp_translator)

A=0xa00000000
CTX,OUTER,META,FILE=0xc427a6000,A+0x11000,A+0x15000,A+0x19000
REC,WHOLE=CTX+0x108,CTX+0xc0
SP=A+0x21000
INNER=SP-0x110
BUF=0x8a9dcda00


class Fake:
    def __init__(self):
        self.data={};self.calls=[];self.bad_page=None;self.short=False
        self.put(CTX+0x18,'Q',OUTER);self.put(CTX+0xbc,'I',1)
        self.put(OUTER+0x790,'Q',META)
        self.put(REC,'I',77);self.put(REC+8,'Q',META);self.put(REC+0x30,'Q',100)
        self.put(FILE+0xc,'I',3);self.put(FILE+0x28,'I',77)
        self.put(FILE,'Q',FILE+0x74);self.put(FILE+8,'I',0x301);self.put(FILE+0x70,'I',3)
        self.raw(FILE+0x74,b'DSC_0001.NEF\0');self.put(FILE+0x94,'I',12)
    def translate(self,cpu,page):
        self.calls.append((cpu,page))
        if page==self.bad_page:raise ObservationError('Unmapped')
        # Explicit synthetic page table; not a production alias rule.
        return page-0x400000000 if CTX&~4095<=page<(CTX&~4095)+0x2000 else page
    def pa(self,a):return self.translate(0,a&~4095)+(a&4095)
    def raw(self,a,b):
        for i,x in enumerate(b):self.data[self.pa(a+i)]=x
    def put(self,a,fmt,*v):self.raw(a,struct.pack('<'+fmt,*v))
    def read(self,a,n):
        b=bytes(self.data.get(a+i,0) for i in range(n))
        return b[:-1] if self.short else b
    def memory(self,**kwargs):return Memory(self.read,self.translate,system_ram_bytes=512<<20,**kwargs)


def regs(pc,sp=SP,**kw):
    r={f'X{i:02}':0 for i in range(31)};r.update(PC=pc,SP=sp);r.update(kw);return r


class Harness:
    def __init__(self,**limits):
        self.f=Fake();self.o=WriterObserver(self.f.memory(),lambda p:EXPECTED[p],**limits)
        self.t=0;self.o.enable(MAIN_SHA,paused=True,start_ns=0)
    def hit(self,r,cpu=0):
        self.t+=1
        allregs=[(c,r if c==cpu else regs(0x40160000,SP+c*0x1000)) for c in range(4)]
        return self.o.feed(allregs,t_ns=self.t,paused=True)
    def call(self,name,**kw):
        s=SITES[name];sp=SP if name=='writer' else INNER-0x70 if name in ('data','padding') else INNER
        r=regs(s.call,sp,**kw);p=self.hit(r)
        self.hit(dict(r,PC=s.target,X30=s.ret));return p
    def ret(self,name,**kw):
        s=SITES[name];sp=SP if name=='writer' else INNER-0x70 if name in ('data','padding') else INNER
        return self.hit(regs(s.ret,sp,X30=s.ret,**kw))
    def begin(self):
        self.call('writer',X00=0,X01=1,X02=REC,X20=REC,X21=1,X24=CTX,X25=OUTER,X26=CTX+0xc0,X27=0)
        self.call('lookup',X00=77,X19=REC);self.ret('lookup',X00=FILE,X19=REC)
        self.call('open',X00=FILE,X19=REC,X22=FILE)
        self.f.put(FILE+8,'Ii',0x301,3);self.f.put(FILE+0x70,'I',3)
        self.ret('open',X00=0x4320080,X19=REC,X22=FILE)
    def segment(self,total=100,padding=0,actual=None,source=BUF):
        self.f.put(INNER+0x70,'QII',source,total,padding)
        self.call('select',X00=1,X01=REC,X02=2,X03=INNER+0x70,X19=REC,X25=0)
        self.ret('select',X00=0,X19=REC)
        self.call('segment',X00=FILE,X01=77,X02=INNER+0x70,X03=INNER+0x9c,X19=REC,X22=FILE,X25=0)
        n=total-padding
        self.call('data',X00=FILE,X01=source,X02=n,X03=INNER-0x70+0xc,X20=FILE,X24=source,X25=n)
        actual=n if actual is None else actual
        self.f.put(INNER-0x70+0xc,'I',actual);self.f.put(FILE+0x10,'QQ',actual,actual)
        self.ret('data',X00=0x4320080,X20=FILE)
        if padding:
            self.call('padding',X00=FILE,X01=0xa04564e98,X02=padding,X03=INNER-0x70+0xc,X20=FILE)
            self.f.put(INNER-0x70+0xc,'I',padding);self.f.put(FILE+0x10,'QQ',actual+padding,actual+padding)
            self.ret('padding',X00=0x4320080,X20=FILE)
        self.f.put(INNER+0x9c,'I',total)
        self.ret('segment',X00=0,X19=REC,X22=FILE)
    def end(self,result=0):
        self.call('final',X00=FILE,X01=100,X19=REC,X22=FILE)
        self.f.put(FILE+8,'IiQQQ',0,-1,0,100,0);self.f.put(FILE+0x70,'I',1)
        self.ret('final',X00=0x4320080,X19=REC,X22=FILE)
        self.call('cleanup',X00=FILE,X19=REC,X22=FILE)
        self.ret('cleanup',X00=0,X19=REC)
        self.ret('writer',X00=result,X20=REC,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        self.hit(regs(0x40ea365c,X08=WHOLE,X20=result,X24=CTX))
        self.f.put(WHOLE+0x2d0,'I',result)
        self.hit(regs(0x40ea3660,X08=WHOLE,X20=result,X24=CTX))


class Tests(unittest.TestCase):
    def test_disabled_performs_nothing(self):
        o=WriterObserver(None,None)
        self.assertEqual(o.feed([],t_ns=0,paused=False),{'add':[],'remove':[]})
        self.assertFalse(o.summary()['native_accepted'])
    def test_source_and_opcode_guards(self):
        f=Fake();o=WriterObserver(f.memory(),lambda p:b'xxxx')
        with self.assertRaises(ObservationError):o.enable(MAIN_SHA,paused=True,start_ns=0)
        with self.assertRaises(ObservationError):WriterObserver(f.memory(),lambda p:EXPECTED[p]).enable('x',paused=True,start_ns=0)
    def test_normal_padding_and_closed_interval(self):
        h=Harness();h.begin();h.segment(100,4);h.end()
        out=h.o.finish(reason='bounded interval ended')
        self.assertTrue(out['interval_closed']);self.assertFalse(out['native_accepted'])
        life=out['lifetimes'][0]
        self.assertEqual(life['actual_success_bytes'],100)
        self.assertEqual(life['write_calls'],2)
        self.assertEqual(life['file_id'],77)
        self.assertTrue(life['write_lengths_match']);self.assertTrue(life['result_store_observed'])
    def test_reused_pointer_is_separate_observed_call_not_frame_id(self):
        h=Harness()
        for _ in range(2):
            h.f.put(FILE+0x10,'QQ',0,0);h.begin();h.segment();h.end()
        out=h.o.finish(reason='end')
        self.assertEqual(len(out['lifetimes']),2)
        self.assertNotEqual(*[x['origin_observation'] for x in out['lifetimes']])
        self.assertTrue(all(x['frame_identity'] is None for x in out['lifetimes']))
    def test_short_success_write_is_not_full_completion(self):
        h=Harness();h.begin();h.segment(actual=7);h.end()
        life=h.o.finish(reason='end')['lifetimes'][0]
        self.assertEqual(life['actual_success_bytes'],7);self.assertFalse(life['write_lengths_match'])
    def test_missing_return_and_mid_exit_fail_closed(self):
        h=Harness();h.begin()
        out=h.o.finish(reason='host deadline')
        self.assertFalse(out['interval_closed']);self.assertIn('incomplete',out['gaps'][-1])
    def test_cpu_migration(self):
        h=Harness();h.begin()
        with self.assertRaises(ObservationError):h.hit(regs(SITES['final'].call,INNER,X00=FILE,X01=100,X19=REC,X22=FILE),cpu=1)
        self.assertFalse(h.o.summary()['interval_closed'])
    def test_sp_mismatch(self):
        h=Harness();h.begin()
        with self.assertRaises(ObservationError):h.hit(regs(SITES['final'].call,INNER+16,X00=FILE,X01=100,X19=REC,X22=FILE))
    def test_simultaneous_cpu_boundary(self):
        h=Harness();r=regs(SITES['writer'].call)
        with self.assertRaises(ObservationError):h.o.feed([(i,r if i<2 else regs(0x40160000)) for i in range(4)],t_ns=1,paused=True)
    def test_breakpoint_guard_natural_bl_rearms_call(self):
        h=Harness();r=regs(SITES['writer'].call,X00=0,X01=1,X02=REC,X20=REC,X21=1,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        p=h.hit(r);self.assertIn(SITES['writer'].target,p['add'])
        self.assertEqual(h.hit(r),{'add':[],'remove':[]})
        p=h.hit(dict(r,PC=SITES['writer'].target,X30=SITES['writer'].ret))
        self.assertIn(SITES['writer'].call,p['add'])
    def test_bad_callee_lr_is_gap(self):
        h=Harness();r=regs(SITES['writer'].call,X00=0,X01=1,X02=REC,X20=REC,X21=1,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        h.hit(r)
        with self.assertRaises(ObservationError):h.hit(dict(r,PC=SITES['writer'].target,X30=9))
    def test_invalid_path_length(self):
        h=Harness();h.f.put(FILE+0x94,'I',32)
        with self.assertRaises(ObservationError):h.begin()
    def test_file_id_change(self):
        h=Harness();h.begin();h.f.put(FILE+0x28,'I',78)
        with self.assertRaises(ObservationError):h.segment()
    def test_written_pointer_is_saved_not_return_x3(self):
        h=Harness();h.begin();h.segment();h.end()
        self.assertEqual(h.o.lifetimes[0]['actual_success_bytes'],100)
    def test_caps_and_time(self):
        h=Harness(max_events=1)
        with self.assertRaises(ObservationError):h.begin()
        h=Harness(max_duration_ns=1)
        with self.assertRaises(ObservationError):h.o.feed([(i,regs(0x40160000)) for i in range(4)],t_ns=2,paused=True)
    def test_paused_and_monotonic(self):
        h=Harness()
        with self.assertRaises(ObservationError):h.o.feed([],t_ns=0,paused=False)
        h=Harness();h.hit(regs(0x40160000))
        with self.assertRaises(ObservationError):h.o.feed([],t_ns=0,paused=True)
    def test_translation_parser(self):
        self.assertEqual(parse_gpa('gpa: 0x8427a6000\n'),0x8427a6000)
        for s in ('Unmapped\n','No cpu\n','hello gpa: 0xa00000000\n','gpa: 0x1\ngpa: 0x2\n'):
            with self.assertRaises(ObservationError):parse_gpa(s)
    def test_ram_full_span_and_gap(self):
        f=Fake();m=f.memory()
        with self.assertRaises(ObservationError):m.span(0,0x8adf8d900,0x400)
        with self.assertRaises(ObservationError):m.span(0,A+(512<<20)-8,16)
        with self.assertRaises(ObservationError):m.span(0,0xe0000000,16)
        f.bad_page=(BUF&~4095)+4096
        with self.assertRaises(ObservationError):m.span(0,BUF,8192)
    def test_short_read(self):
        f=Fake();f.short=True
        with self.assertRaises(ObservationError):f.memory().read(0,A,16)
    def test_alias_translation_retained(self):
        f=Fake();m=f.memory();r=m.span(0,REC,0x38)
        self.assertEqual(r['segments'][0]['pa'],0x8427a6108)
        self.assertEqual(r['va'],REC);self.assertEqual(r['cpu'],0)
    def test_no_inner_phase_can_bypass_unreturned_segment(self):
        h=Harness();h.begin();h.f.put(INNER+0x70,'QII',BUF,100,0)
        h.call('select',X00=1,X01=REC,X02=2,X03=INNER+0x70,X19=REC,X25=0)
        h.ret('select',X00=0,X19=REC)
        h.call('segment',X00=FILE,X01=77,X02=INNER+0x70,X03=INNER+0x9c,X19=REC,X22=FILE,X25=0)
        with self.assertRaises(ObservationError):h.call('final',X00=FILE,X01=100,X19=REC,X22=FILE)
    def test_open_failure_cleanup_is_observed_without_fake_success(self):
        h=Harness()
        h.call('writer',X00=0,X01=1,X02=REC,X20=REC,X21=1,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        h.call('lookup',X00=77,X19=REC);h.ret('lookup',X00=FILE,X19=REC)
        h.call('open',X00=FILE,X19=REC,X22=FILE);h.ret('open',X00=0x4320088,X22=FILE)
        h.call('cleanup_error',X00=FILE,X19=REC,X22=FILE)
        h.ret('cleanup_error',X00=99)
        h.ret('writer',X00=1,X20=REC,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        h.hit(regs(0x40ea365c,X08=WHOLE,X20=1));h.f.put(WHOLE+0x2d0,'I',1)
        h.hit(regs(0x40ea3660,X08=WHOLE,X20=1))
        out=h.o.finish(reason='abort route returned')
        self.assertTrue(out['interval_closed']);self.assertFalse(out['native_accepted'])
        self.assertEqual(out['lifetimes'][0]['writer_result'],1)
        self.assertEqual(out['total_write_calls'],0)
    def test_skipped_segment_is_not_counted_as_a_write(self):
        h=Harness();h.begin()
        h.call('select',X00=1,X01=REC,X02=2,X03=INNER+0x70,X19=REC,X25=0)
        h.ret('select',X00=1,X19=REC);h.end()
        self.assertEqual(h.o.lifetimes[0]['write_calls'],0)
        self.assertEqual(h.o.lifetimes[0]['selections'][0]['status'],1)
    def test_result_store_must_really_execute(self):
        h=Harness();h.begin();h.segment()
        h.call('final',X00=FILE,X01=100,X19=REC,X22=FILE)
        h.f.put(FILE+8,'IiQQQ',0,-1,0,100,0);h.f.put(FILE+0x70,'I',1)
        h.ret('final',X00=0x4320080)
        h.call('cleanup',X00=FILE,X19=REC,X22=FILE);h.ret('cleanup',X00=0)
        h.ret('writer',X00=0,X20=REC,X24=CTX,X25=OUTER,X26=CTX+0xc0)
        h.hit(regs(0x40ea365c,X08=WHOLE,X20=0));h.f.put(WHOLE+0x2d0,'I',3)
        with self.assertRaises(ObservationError):h.hit(regs(0x40ea3660,X08=WHOLE,X20=0))
    def test_memory_limits_and_unaligned_translation(self):
        f=Fake()
        with self.assertRaises(ObservationError):f.memory(max_pages=1).span(0,BUF,8192)
        with self.assertRaises(ObservationError):f.memory(max_read_bytes=1).read(0,A,2)
        m=Memory(f.read,lambda c,p:p+1,system_ram_bytes=512<<20)
        with self.assertRaises(ObservationError):m.read(0,A,8)
        with self.assertRaises(ObservationError):f.memory().span(0,(1<<64)-4,8)
    def test_transport_gap_cannot_be_overridden_by_normal_finish(self):
        h=Harness();h.o.invalidate('breakpoint install failed')
        self.assertFalse(h.o.finish(reason='end')['interval_closed'])
    def test_wrong_source_span_and_write_caps(self):
        h=Harness(max_requested_bytes=99);h.begin()
        with self.assertRaises(ObservationError):h.segment()
        h=Harness(max_lifetimes=1);h.begin();h.segment();h.end()
        with self.assertRaises(ObservationError):h.begin()
    def test_same_file_object_cannot_change_handle_mid_lifetime(self):
        h=Harness();h.begin();h.f.put(FILE+0xc,'I',4)
        with self.assertRaises(ObservationError):h.segment()
    def test_translation_adapter_only_uses_paused_selected_cpu(self):
        class Q:
            running=False
            calls=[]
            def command(self,name):
                self.calls.append(name);return {'running':self.running}
            def hmp(self,command,cpu):
                self.calls.append((command,cpu));return 'gpa: 0x8427a6000\n'
        q=Q();self.assertEqual(hmp_translator(q)(2,CTX),0x8427a6000)
        self.assertEqual(q.calls[-1],('gva2gpa 0xc427a6000',2))
        q.running=True
        with self.assertRaises(ObservationError):hmp_translator(q)(2,CTX)
    def test_zero_length_write_does_not_dereference_null_payload(self):
        h=Harness();h.begin();h.segment(total=0,source=0);h.end()
        life=h.o.finish(reason='end')['lifetimes'][0]
        self.assertEqual(life['write_calls'],1)
        self.assertIsNone(life['writes'][0]['source_span'])
        self.assertEqual(life['actual_success_bytes'],0)


if __name__=='__main__':unittest.main()
