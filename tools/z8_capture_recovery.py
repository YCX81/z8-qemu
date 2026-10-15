# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in C3.11 recovery branch observations; RAM/QOM reads only.

Caller verifies pinned MAIN and owns existing stopped-CPU observer boundaries.
First-hit-per-contact branches are NOT a complete task/event history.
"""
import hashlib,json
from pathlib import Path
from z8_capture_readiness import SCALARS

MAX_READ=512;MAX_BYTES=64*1024;MAX_EVENTS=64
MAX_QOM_SINGLE=128*1024;MAX_QOM_BYTES=2*1024*1024
POINTS={
    0x40f44a10:'recovery_mode_dispatch',0x40f44aa4:'recovery_native_state_gate',
    0x40f44b3c:'recovery_poll800_return',0x40f44b54:'recovery_ac69_bit_gate',
    0x40f44d18:'recovery_alt_poll800_return',0x40f44d30:'recovery_alt_ac69_gate',
    0x40f44da0:'recovery_post2_gate',0x40f44f14:'recovery_event2_enter',
    0x40f45028:'recovery_wait1000_return',0x40f451cc:'recovery_wait200000_return',
    0x40f4523c:'recovery_event4_enter',0x40f454f8:'recovery_wait8_return',
    0x40f5d8a0:'recovery_policy_wait_gate',0x40f5d8b8:'recovery_policy_wait_return',
}
# P10: dedicated call sites, rather than a global OS set/wait breakpoint.
# Still first hit per external contact; absence is not a complete event history.
FLAG800_POINTS={
    0x40f44b38:'recovery_poll800_before',
    0x40f44d84:'recovery_after_start_choice',
    0x40f44544:'recovery_set800_task4000_before',
    0x40f44548:'recovery_set800_task4000_after',
    0x40f44730:'recovery_set800_task10000_before',
    0x40f44734:'recovery_set800_task10000_after',
    0x40f4b534:'recovery_set800_callback_path',
    0x40f4d35c:'recovery_set800_lifecycle_before',
    0x40f4d360:'recovery_set800_lifecycle_after',
    0x40f46440:'recovery_clear800_before',
    0x40f46444:'recovery_after_clear800_choice',
    0x40f465dc:'recovery_event100_enter',
    0x40f46c9c:'recovery_event100_post2',
    0x40f4b3e8:'recovery_callback_419018b',
}
POINTS.update(FLAG800_POINTS)
# Initial 800 can predate shutter_start. At most one pre-shutter hit at
# each specific setter boundary; no generic OS breakpoint or menu RAM poll.
EARLY_POINTS={pc:FLAG800_POINTS[pc] for pc in
              (0x40f44544,0x40f44548,0x40f44730,0x40f44734,0x40f4d35c,0x40f4d360)}
POLICY_POINTS={
    0x40f463bc:'recovery_request13_before',0x40f463c0:'recovery_request13_return',
    0x40f4c730:'recovery_end119_command_gate',0x40f4cc38:'recovery_end119_post200000',
    0x40f555dc:'recovery_producer_bb95_gate',0x40f55604:'recovery_producer_callback_return',
    0x40f591d8:'recovery_producer_bb95_armed',0x40f58f74:'recovery_native_event201000b',
    0x40f59024:'recovery_producer_state3_gate',0x40f5905c:'recovery_producer_mode_gate',
}
POINTS.update(POLICY_POINTS)
MENU_CYCLE_POINTS={**EARLY_POINTS,**{pc:POINTS[pc] for pc in
    (0x40f44b38,0x40f44b3c,0x40f46c9c,0x40f4b3e8,0x40f4b534,0x40f46440,0x40f46444)}}
REARM_POINTS={0x40f44748:'rearm_stop_mode_loaded',0x40f447a8:'rearm_state1_mode0_stored',
              0x40f447f8:'rearm_state1_mode1_stored',0x40f43b24:'rearm_resume_mode_dispatch',
              0x40f4b3e4:'rearm_callback189_post4000'}
FLAG_REGIONS={'cseq_context':(0xa03bba948,0x38),
              'capture_flags':(0xa03bbac60,0x70),
              'native_states':(0xa03bbb430,0x10)}
POLICY_REGIONS={**FLAG_REGIONS,'producer_policy':(0xa03bbb930,0xb0),
                'producer_latches':(0xa03bbbb90,16),'producer_callback':(0xa03bbb588,8),
                'pipeline_stage':(0xa03bbb63c,4),'native_request_queue':(0xa03b934b8,48)}
REGIONS={'cseq_context':(0xa03bba948,0x174),'capture_flags':(0xa03bbac60,0x70),
         'native_states':(0xa03bbb430,0x10),'timing':(0xa03bba810,0x100),
         'parameters':(0xa03bbb1f4,0x78),'lifecycle':(0xa03bbb960,0x70),
         'pipeline_stage':(0xa03bbb63c,4)}
STATES=('primary-d980-state','primary-d980-single-frame-state','primary-d980-prepared-start-state',
 'engine150c-state','engine151-control-state','z8-capture-hlg-pixels-state','z8-capture-hlg-pipeline-state',
 'z8-capture-hlg-thumbnail-state','engine15e2-jpeg-state','z8-still-hevc-state','z8-still-hevc-codec-state',
 'capture-gpio-state','ccomp-gpio-state','engine1548-scanout-state')
CONTACTS={'release','half','full','full-release','half-release',
          'shot2-release','shot2-half','shot2-full','shot2-full-release','shot2-half-release'}
def need(value,message):
    if not value:raise ValueError(message)

class Observer:
    def __init__(self,guest):
        self.g=guest;self.active=False;self.events=0;self.bytes_requested=0;self.qom_bytes=0
        self.early_enabled=False;self.early_seen=set()
        self.points=MENU_CYCLE_POINTS if getattr(guest,'_heif_intershot_menu_cycle',False) else POINTS
        self.intershot_started=False;self.rearm_seen=set()
        if getattr(guest,'_heif_native_rearm_gate',False):self.points={**self.points,**REARM_POINTS}
        self.early_points={pc:name for pc,name in EARLY_POINTS.items() if pc in self.points}
    def enable_early(self):
        self.early_enabled=True
    def can_arm(self,pc):
        if pc in REARM_POINTS:
            return pc in self.points and self.active and self.intershot_started and pc not in self.rearm_seen
        return pc in self.points and (self.active or
            (self.early_enabled and pc in self.early_points and pc not in self.early_seen))
    def read(self,pa,size):
        need(type(pa) is int and type(size) is int and 0<size<=MAX_READ and
             0xa00000000<=pa<=0xa20000000-size,'Recovery RAM read bound')
        need(self.bytes_requested+size<=MAX_BYTES,'Recovery 64 KiB RAM budget exhausted')
        self.bytes_requested+=size
        data=self.g.read_bytes(pa,size);need(len(data)==size,'Short recovery RAM read');return data
    def contact(self,label,t_ns):
        if not self.active or label not in CONTACTS:return None
        return self.record(label,t_ns,contact=True)
    def point(self,pc,cpu,regs,t_ns):
        if pc in REARM_POINTS:
            need(self.can_arm(pc),'Unarmed or duplicate rearm point');self.rearm_seen.add(pc)
        if not self.active:
            if not self.early_enabled or pc not in self.early_points:return None
            need(pc not in self.early_seen,'Duplicate early recovery point')
            self.early_seen.add(pc)
        need(pc in self.points,'Unknown recovery point')
        return self.record(self.points[pc],t_ns,pc=hex(pc),cpu=cpu,
            observation_phase=('intershot-once-per-pc' if pc in REARM_POINTS else
                ('shutter-first-hit-per-contact' if self.active else 'early-first-hit')),
            registers={k:hex(v) for k,v in regs.items() if type(v) is int})
    def event_flag(self):
        # Pinned MAIN: 4017c094..124 resolves handle-1 through this table;
        # 4017b1ec reads pattern+18, attributes through object+10.
        def word(pa,n=4):return int.from_bytes(self.read(pa,n),'little')
        handle=word(0xa03bba4f8);count=word(0xa02aaaad4)
        need(1<=handle<=min(count,4096),'Recovery event handle/count bound')
        slot=0xa049ae038+8*(handle-1);obj=word(slot,8)
        need(not obj&7,'Recovery event object alignment')
        raw=self.read(obj,32);attr_ptr=int.from_bytes(raw[16:24],'little')
        need(not attr_ptr&3,'Recovery event attribute alignment')
        attr=word(attr_ptr)
        need(word(0xa03bba4f8)==handle and word(slot,8)==obj,
             'Recovery event identity changed during read')
        return dict(handle=handle,count=count,object=hex(obj),object_hex=raw.hex(),
                    pattern=int.from_bytes(raw[24:28],'little'),attribute=attr,
                    attribute_address=hex(attr_ptr),identity_stable=True)
    def record(self,label,t_ns,contact=False,**fields):
        need(self.events<MAX_EVENTS,'Recovery 64-event budget exhausted');self.events+=1
        row=dict(label=label,t_ns=t_ns,collection_complete=False,guest_state_modified=False,
                 scope='First-hit branch or pre-consumer contact snapshot; not complete task/event history',**fields)
        try:
            row['memory']={}
            pc=int(fields.get('pc','0'),16);policy=pc in POLICY_POINTS
            compact=pc in FLAG800_POINTS or pc in REARM_POINTS or policy
            row['snapshot_layout']='producer-policy-v1' if policy else ('flag800-compact-v1' if compact else 'recovery-v1')
            regions=POLICY_REGIONS if policy else (FLAG_REGIONS if compact else REGIONS)
            for name,(pa,size) in regions.items():
                data=self.read(pa,size);row['memory'][name]=dict(address=hex(pa),bytes=size,hex=data.hex(),sha256=hashlib.sha256(data).hexdigest())
            if pc in REARM_POINTS:
                for name,pa in (('resume_mode',0xa03bba638),('stop_mode',0xa03bba92c)):
                    data=self.read(pa,4);row['memory'][name]=dict(address=hex(pa),bytes=4,hex=data.hex(),sha256=hashlib.sha256(data).hexdigest())
            if compact:row['event_flag']=self.event_flag()
            if pc==0x40f463bc:
                pa=int(fields['registers']['X00'],16);data=self.read(pa,12)
                row['request13_argument']=dict(address=hex(pa),bytes=12,hex=data.hex(),sha256=hashlib.sha256(data).hexdigest())
            if fields.get('pc') in ('0x40f5d8a0','0x40f5d8b8'):
                sp=int(fields['registers']['SP'],16);need(not sp&15,'Recovery helper stack alignment')
                caller=int.from_bytes(self.read(sp+8,8),'little')
                row['saved_caller_lr']=hex(caller)
                row['caller_in_full_start_path']=caller in (0x40f44a3c,0x40f44d88)
            if contact:
                row['contact_phase']='After optional GPIO levels, before original MCU/CPU consumption'
                row['readiness']={k:dict(address=hex(pa),bytes=n,value=int.from_bytes(self.read(pa,n),'little')) for k,(pa,n) in SCALARS.items()}
                row['key_latch_hex']=self.read(0xa0320169c,44).hex();row['device_states']={}
                for name in STATES:
                    need(self.qom_bytes<MAX_QOM_BYTES,'Recovery QOM budget exhausted')
                    raw=self.g.qmp.command('qom-get',path='/machine',property=name)
                    text=raw if isinstance(raw,str) else json.dumps(raw);size=len(text.encode())
                    self.qom_bytes+=size
                    need(size<=MAX_QOM_SINGLE and self.qom_bytes<=MAX_QOM_BYTES,'Recovery QOM size bound')
                    value=json.loads(text);need(isinstance(value,dict),'Recovery QOM object required')
                    row['device_states'][name]=value
            row['collection_complete']=True
        except Exception as e:
            row['error']=repr(e);raise
        finally:
            row.update(bytes_requested_total=self.bytes_requested,qom_bytes_total=self.qom_bytes)
            folder=Path(self.g.out)/'capture-recovery';folder.mkdir(exist_ok=True)
            path=folder/f'{self.events:03}-{label}.json'
            with path.open('x') as f:json.dump(row,f,indent=2);f.write('\n')
        return dict(path=path.relative_to(self.g.out).as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    collection_complete=True,bytes_requested_total=self.bytes_requested)
