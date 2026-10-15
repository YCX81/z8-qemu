# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded host contact gate. Never writes RAM/MMIO, ACKs or injects events.

These pinned C3.11 branch inputs are necessary witnesses for the run14 path,
not a claim that a new photo will finish. Caller stops the owned VM for reads,
then continues ordinary service while waiting; host/virtual budgets still apply.
"""
import json
import z8_capture_recovery
NATIVE=0xa03bbb430;CONTEXT=0xa03bba97c
PERMISSION=0xa03bd042c;INHIBIT=0xa0321e80c
MODE_START=0xa03bba638;MODE_STOP=0xa03bba92c
WAIT_NS=10_000_000_000;POLL_NS=100_000_000
MAX_SAMPLES=202;MAX_RAM=16*1024;MAX_QOM=1024*1024;MAX_SINGLE_QOM=64*1024
LABELS=('shot2-release','shot2-full')
STOP_WAIT_NS=5_000_000_000;STOP_MAX_SAMPLES=64

def need(ok,message):
    if not ok:raise ValueError(message)

def pause_and_sample(peer,gate,label):
    gate.g.qmp.command('stop');peer.handle()
    now=peer.now();ready=gate.sample(label,now)
    # Ready: next caller queues the approved contact while still paused.
    # Waiting: ordinary peer/timer/device service must continue. Exceptions
    # intentionally leave the VM stopped and retain the failed gate receipt.
    if not ready:gate.g.qmp.command('cont')
    return ready,now

def contact_deadline(enabled,label,sampled_ns,queued_ns,gap_ns):
    need(all(type(v) is int and v>=0 for v in (sampled_ns,queued_ns,gap_ns)) and queued_ns>=sampled_ns,
         'Invalid contact clock/gap')
    return (queued_ns if enabled and label.startswith('shot2-') else sampled_ns)+gap_ns

class StopReleaseGate:
    """External MENU release waits for native notification and task entry.

    This does not mean all hardware teardown or the task body has finished.
    It only orders an external key release after observed native execution.
    """
    def __init__(self,guest,enabled=False,*,minimum_ns=400_000_000):
        self.g=guest;self.enabled=enabled;self.minimum_ns=minimum_ns
        self.rows=[];self.samples=0;self.last_ns=None
        if enabled:
            need(type(minimum_ns) is int and 0<=minimum_ns<STOP_WAIT_NS,'MENU hold budget')
            guest.data['capture_trace']['heif_menu_stop_gate']=self.rows
    def sample(self,label,t_ns):
        if not self.enabled:return True
        need(label=='shot2-menu-release','Unknown MENU stop gate label')
        need(type(t_ns) is int and t_ns>=0 and (self.last_ns is None or t_ns>=self.last_ns),'MENU monotonic clock required')
        need(self.samples<STOP_MAX_SAMPLES,'MENU stop sample budget exhausted')
        self.samples+=1;self.last_ns=t_ns
        events=self.g.data['capture_trace']['events']
        press=next((e for e in reversed(events) if e.get('kind')=='input_queued' and e.get('label')=='shot2-menu'),None)
        need(press is not None and type(press.get('t_ns')) is int and 0<=press['t_ns']<=t_ns,'Missing current MENU press')
        posts=[e for e in events if e.get('kind')=='recovery_menu_stop107' and press['t_ns']<e['t_ns']<=t_ns]
        post=posts[0] if posts else None;consume=None
        if post is not None:
            consume=next((e for e in events if e.get('kind')=='recovery_menu_stop20000' and
                post['t_ns']<=e['t_ns']<=t_ns and int(str(e.get('registers',{}).get('X19','0')),0)&0x20000),None)
        reasons=[]
        if t_ns-press['t_ns']<self.minimum_ns:reasons.append('minimum-hold')
        if post is None:reasons.append('stop107-not-observed')
        if consume is None:reasons.append('stop20000-not-entered')
        row=dict(label=label,t_ns=t_ns,press_t_ns=press['t_ns'],ready=not reasons,reasons=reasons,
                 status='waiting' if reasons else 'ready',guest_state_modified=False,
                 witness_ids=[e.get('event_id') for e in (post,consume) if e is not None],
                 witness_t_ns=[e['t_ns'] for e in (post,consume) if e is not None],
                 scope='Native stop107 notification then CSEQ task20000 entry; not whole teardown completion')
        self.rows.append(row)
        if reasons and t_ns-press['t_ns']>=STOP_WAIT_NS:
            row['status']='timeout'
            raise RuntimeError('MENU stop gate timeout before release: '+','.join(reasons))
        return row['ready']

class Gate:
    def __init__(self,guest,enabled=False,*,shared=None):
        self.g=guest;self.enabled=enabled;self.rows=[];self.starts={};self.shared=shared
        self.samples=0;self.ram_bytes=0;self.qom_bytes=0;self.last_ns=None
        if enabled:guest.data['capture_trace']['heif_native_rearm_gate']=self.rows
    def sample(self,label,t_ns):
        if not self.enabled:return True
        need(label in LABELS,'Unknown native rearm gate label')
        need(type(t_ns) is int and t_ns>=0 and (self.last_ns is None or t_ns>=self.last_ns),'Native rearm monotonic clock required')
        need(self.samples<MAX_SAMPLES,'Native rearm sample budget exhausted')
        self.samples+=1;self.last_ns=t_ns;self.starts.setdefault(label,t_ns)
        row=dict(label=label,t_ns=t_ns,ready=False,status='reading',guest_state_modified=False,
                 scope='Necessary native branch inputs and current scanout only; not photo completion or hardware readiness proof')
        self.rows.append(row)
        def read(a,n):
            need(self.ram_bytes+n<=MAX_RAM,'Native rearm RAM budget exhausted')
            self.ram_bytes+=n;b=(self.shared.read(a,n) if self.shared is not None else self.g.read_bytes(a,n))
            need(len(b)==n,'Short native rearm read');return b
        try:
            need(self.g.qmp.command('query-status').get('running') is False,'Native rearm requires stopped VM')
            native=read(NATIVE,16);context=read(CONTEXT,4)
            permission=read(PERMISSION,1)[0];inhibit=read(INHIBIT,1)[0]
            start=read(MODE_START,4);stop=read(MODE_STOP,4)
            raw=self.g.qmp.command('qom-get',path='/machine',property='engine1548-scanout-state')
            text=raw if isinstance(raw,str) else json.dumps(raw);size=len(text.encode())
            self.qom_bytes+=size
            need(size<=MAX_SINGLE_QOM and self.qom_bytes<=MAX_QOM,'Native rearm QOM budget exhausted')
            if self.shared is not None:
                self.shared.qom_bytes+=size
                need(self.shared.qom_bytes<=z8_capture_recovery.MAX_QOM_BYTES,'Native rearm shared QOM budget exhausted')
            lcd=json.loads(text);need(isinstance(lcd,dict),'Native rearm scanout object required')
            need(read(NATIVE,16)==native and read(CONTEXT,4)==context,'Native state changed during stopped snapshot')
            words=[int.from_bytes(native[i:i+4],'little') for i in range(0,16,4)]
            ctx=int.from_bytes(context,'little');reasons=[]
            if words[2]!=2:reasons.append('native-b438-not-2')
            if words[3]!=1:reasons.append('native-b43c-not-1')
            if ctx!=2:reasons.append('context-mode-not-2')
            if permission!=1:reasons.append('full-worker-permission')
            if inhibit!=0:reasons.append('key-dispatch-inhibit')
            if lcd.get('active') is not True or lcd.get('last_frame_has_video') is not True or lcd.get('video_frames',0)<=0:
                reasons.append('liveview-video')
            row.update(native_words=words,context_mode=ctx,full_worker_permission=permission,key_dispatch_inhibit=inhibit,
                resume_mode=int.from_bytes(start,'little'),stop_mode=int.from_bytes(stop,'little'),
                lcd={k:lcd.get(k) for k in ('active','last_frame_has_video','video_frames','frames')},reasons=reasons,
                ready=not reasons,status='ready' if not reasons else 'waiting')
            if reasons and t_ns-self.starts[label]>=WAIT_NS:
                row['status']='timeout'
                raise RuntimeError('Native rearm gate timeout before '+label+': '+','.join(reasons))
            return row['ready']
        except Exception as e:
            if row['status']!='timeout':row['status']='error'
            row['error']=repr(e);raise
        finally:row.update(ram_bytes_total=self.ram_bytes,qom_bytes_total=self.qom_bytes,samples=self.samples)
