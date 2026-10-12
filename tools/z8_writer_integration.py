# SPDX-License-Identifier: GPL-2.0-or-later
"""Optional integration: observer-owned passive breakpoints only; no launcher."""
import json
import re
from z8_writer_observer import Memory,WriterObserver,ObservationError,EXPECTED,MAIN_SHA,parse_gpa,verify_main
from z8_writer_code_reader import RuntimeCodeReader

BINARY_SHA='0ff25311649a43d8691d60e5da02160cf71ea68d0d2725df0ad7f06f49ddce27'
IMAGE_RANGES=((0x840000000,0x8adf8da00),(0x8adf8dc00,0x8fff47400))
RESERVED=frozenset(EXPECTED)
PREFIX='native_writer_'


def configure(guest,args,host_check):
    """Explicit prelaunch opt-in; default off performs no reads or guest work."""
    requested=getattr(args,'native_writer_observer',False)
    if type(requested) is not bool:raise ValueError('Native writer observer option must be Boolean')
    if not requested:return False
    if guest.data.get('firmware_sha256')!=MAIN_SHA or guest.data.get('qemu_sha256')!=BINARY_SHA:
        raise ObservationError('Native writer observation requires the pinned original MAIN and validated 0ff runtime')
    static_guard=verify_main(args.firmware)
    guest.data['native_writer_request']=dict(enabled=True,static_main_guard=static_guard,
        runtime_sha256=BINARY_SHA,activation='paused shutter_start only',native_accepted=False,
        scope='Bounded mode0/format1 lifecycle; no historical/frame/media acceptance')
    guest._native_writer_observer_enabled=True
    guest._writer_host_check=host_check
    return True


def without_reserved(points):return {pc:label for pc,label in points.items() if pc not in RESERVED}


def decoded(value):
    return json.loads(value) if isinstance(value,str) else value


class WriterSession:
    def __init__(self,peer,journal,host_check):
        self.peer,self.g,self.journal,self.host_check=peer,peer.g,journal,host_check
        self.observer=None;self.owned={};self.errors=[];self.geometry=None
        self.read_cpu=None;self.code_phase=None;self.code_reader=None
        self.snapshots=[];self.last_plan={'add':[],'remove':[]}
        self.enabled=False;self.closed=False;self.command_log=[];self.published=0
    def _error(self,error):
        self.errors.append(str(error))
        if self.observer:self.observer.invalidate(str(error))
        raise ObservationError(str(error)) from error if isinstance(error,Exception) else None
    def _paused(self):
        if self.g.qmp.command('query-status').get('running') is not False:
            raise ObservationError('Writer integration requires an already paused owned guest')
    def _ownership(self):
        for pc,label in self.owned.items():
            if self.peer.points.get(pc)!=label:raise ObservationError(f'Writer breakpoint ownership lost at {pc:#x}')
        for pc,label in self.peer.points.items():
            if pc in RESERVED and self.owned.get(pc)!=label:
                raise ObservationError(f'Reserved writer PC already belongs to {label}: {pc:#x}')
    def _translate(self,cpu,page):
        self.host_check();self._paused()
        value=parse_gpa(self.g.qmp.hmp(f'gva2gpa {page:#x}',cpu))
        self.host_check();return value
    def _physical_read(self,address,size):
        self.host_check();result=self.g.read_bytes(address,size);self.host_check();return result
    def _instruction(self,pc):
        if self.code_reader is None:raise ObservationError('Code reader has not been initialized')
        if self.code_phase=='activation':return self.code_reader.activation_word(pc)
        if self.code_phase=='hit' and type(self.read_cpu) is int:
            return self.code_reader.read(self.read_cpu,pc,phase='hit')
        raise ObservationError('Instruction guard lacks an actual observed CPU/phase')
    def _geometry(self):
        command=self.g.command
        if command.count('-m')!=1 or command.count('-M')!=1 or command.count('-smp')!=1:
            raise ObservationError('Unique launch memory/machine/SMP settings required')
        amount=command[command.index('-m')+1]
        if amount not in ('512M','1024M') or command[command.index('-smp')+1]!='4':
            raise ObservationError('Unsupported launch RAM/SMP geometry')
        expected=int(amount[:-1])<<20
        machine=command[command.index('-M')+1].split(',')
        if machine[0]!='nikon-z8-bringup' or [x for x in machine if x.startswith('image-bank-model=')]!=['image-bank-model=on']:
            raise ObservationError('Explicit Z8 image-bank launch required')
        info=decoded(self.g.qmp.command('query-memory-size-summary'))
        if type(info.get('base-memory')) is not int or info['base-memory']!=expected or info.get('plugged-memory',0)!=0:
            raise ObservationError('Actual RAM geometry differs from owned launch')
        bank=decoded(self.g.qmp.command('qom-get',path='/machine',property='image-bank-state'))
        ranges=tuple((int(x['base'],0),int(x['end'],0)) for x in bank['ranges'])
        if bank.get('enabled') is not True or ranges!=IMAGE_RANGES:
            raise ObservationError('Actual image banks or 512-byte gap differ')
        return dict(base_memory=expected,system_ram=[0xa00000000,0xa00000000+expected],
                    image_bank_state=bank,query_memory_size_summary=info,
                    source='owned launch plus paused runtime QMP/QOM',assumed_aliases=[])
    def _now(self):
        value=decoded(self.g.qmp.command('qom-get',path='/machine',property='runtime-clock-state'))['virtual_ns']
        if type(value) is not int or value<0:raise ObservationError('Invalid runtime virtual clock')
        return value
    def enable(self):
        try:
            self.host_check();self._paused();self._ownership()
            if self.enabled or self.closed or self.g.data.get('firmware_sha256')!=MAIN_SHA or self.g.data.get('qemu_sha256')!=BINARY_SHA:
                raise ObservationError('Fresh observer and immutable C3.11/runtime identity required')
            self.geometry=self._geometry()
            self.code_reader=RuntimeCodeReader(self.g,self.geometry,self.host_check)
            memory=Memory(self._physical_read,self._translate,system_ram_bytes=self.geometry['base_memory'])
            self.observer=WriterObserver(memory,self._instruction)
            self.code_phase='activation'
            try:plan=self.observer.enable(MAIN_SHA,paused=True,start_ns=self._now())
            finally:self.code_phase=None;self.read_cpu=None
            self.enabled=True
            self.snapshots=self._registers()
            self.apply_plan(plan)
            self.g.data['native_writer_observer']=dict(enabled=True,geometry=self.geometry,
                scope='Passive after-shutter-start interval; original baseline external inputs unchanged',
                firmware_patch=False,observer_guest_ram_writes=False,observer_guest_register_writes=False,
                observer_guest_event_injection=False,native_accepted=False)
        except Exception as error:self._error(error)
    def _registers(self):
        rows=[]
        for cpu in range(4):
            self.host_check()
            text=self.g.qmp.hmp('info registers',cpu)
            r={k:int(v,16) for k,v in re.findall(r'\b(X\d\d|PC|SP)=([0-9a-fA-F]+)',text)}
            if not {'PC','SP',*(f'X{i:02}' for i in range(31))}<=r.keys():
                raise ObservationError('Incomplete raw CPU register snapshot')
            rows.append((cpu,r))
        return rows
    def apply_plan(self,plan):
        try:
            self.host_check();self._paused();self._ownership()
            if set(plan)!={'add','remove'}:raise ObservationError('Invalid breakpoint plan shape')
            add,remove=plan['add'],plan['remove']
            if (len(add)!=len(set(add)) or len(remove)!=len(set(remove)) or set(add)&set(remove) or
                any(pc not in RESERVED for pc in add+remove)):
                raise ObservationError('Plan escapes reserved breakpoint set')
            # Validate the WHOLE plan before sending any packet.
            for pc in remove:
                if pc not in self.owned or self.peer.points.get(pc)!=self.owned[pc]:
                    raise ObservationError('Removal is not owned by this observer')
            for pc in add:
                if pc in self.peer.points:raise ObservationError('Breakpoint label collision')
                if any(r['PC']==pc for _,r in self.snapshots):
                    raise ObservationError('New breakpoint overlaps another already stopped boundary')
            for pc in remove:
                self.host_check();self.g.rsp(f'z1,{pc:x},4')
                self.command_log.append(dict(operation='remove',pc=pc,verified=True))
                del self.peer.points[pc];del self.owned[pc]
            for pc in add:
                self.host_check();label=PREFIX+f'{pc:x}'
                # Retain attempted ownership on an unknown transport outcome;
                # final cleanup may try only this PC, never a foreign label.
                self.owned[pc]=label;self.peer.points[pc]=label
                self.g.rsp(f'Z1,{pc:x},4')
                self.command_log.append(dict(operation='add',pc=pc,verified=True))
            self.last_plan=plan
        except Exception as error:self._error(error)
    def handle(self):
        if not self.enabled or self.closed:return False
        try:
            self.host_check();self._paused();self._ownership()
            self.snapshots=self._registers()
            hits=[(c,r) for c,r in self.snapshots if r['PC'] in self.observer.armed]
            self.read_cpu=hits[0][0] if len(hits)==1 else None
            before=len(self.observer.events)
            self.code_phase='hit'
            try:plan=self.observer.feed(self.snapshots,t_ns=self._now(),paused=True)
            finally:self.code_phase=None;self.read_cpu=None
            self.apply_plan(plan)
            for row in self.observer.events[self.published:]:
                self.journal.add('native_writer_'+row['kind'],row['t_ns'],
                    writer_observation=row['observation'],pc=hex(row['pc']),cpu=row['cpu'],sp=hex(row['sp']),
                    native_accepted=False)
                if row['kind']=='writer_return':
                    # Preserve the public idle predicate using the actual same
                    # return, without arming a competing one-shot breakpoint.
                    self.journal.add('file_writer_return',row['t_ns'],
                        registers={'X00':hex(row['status_raw'])},writer_observation=row['observation'],
                        scope='Actual passive writer return; no file acceptance')
            self.published=len(self.observer.events)
            return len(self.observer.events)>before
        except Exception as error:self._error(error)
    def close(self,*,reason,failure=None):
        if self.closed:return self.g.data.get('native_writer_observer',{})
        cleanup=[]
        if failure is not None and self.observer:self.observer.invalidate('Run exited with failure: '+repr(failure))
        # Cleanup intentionally bypasses the expired work budget; QMP/RSP retain
        # their bounded socket timeouts. It never resumes the guest.
        try:self._paused()
        except Exception as e:cleanup.append('Cannot verify paused cleanup: '+repr(e))
        if not cleanup:
            for pc,label in list(self.owned.items()):
                if self.peer.points.get(pc)!=label:
                    cleanup.append(f'Foreign/lost label at {pc:#x}; not disarmed');continue
                try:
                    self.g.rsp(f'z1,{pc:x},4');del self.peer.points[pc];del self.owned[pc]
                except Exception as e:cleanup.append(f'Owned breakpoint removal failed at {pc:#x}: {e!r}')
        if cleanup and self.observer:self.observer.invalidate('; '.join(cleanup))
        report=self.observer.finish(reason=reason) if self.observer else dict(enabled=False,
            interval_closed=False,events=[],lifetimes=[],gaps=list(self.errors),native_accepted=False)
        report.update(geometry=self.geometry,cleanup_errors=cleanup,transport_errors=list(self.errors),
            remaining_owned_points={hex(p):v for p,v in self.owned.items()},
            observed_writer_lifetimes=bool(report['lifetimes']),breakpoint_commands=self.command_log,
            observer_guest_ram_writes=False,observer_guest_register_writes=False,observer_guest_event_injection=False,
            runtime_validation='Live observations only when executed; this integration itself manufactures none')
        if self.code_reader is not None:
            self.code_reader.persist()
            code=self.code_reader.receipt
            report['code_guard']=dict(receipt='native-code-guards.json',reads=len(code['reads']),
                verified=sum(r['verified'] for r in code['reads']),first_failure=code['first_failure'],
                mapping_reused=False,actual_cpu_at_every_hit=True)
        self.closed=True;self.g.data['native_writer_observer']=report
        (self.g.out/'native-writer-observations.json').write_text(json.dumps(report,indent=2)+'\n')
        return report
