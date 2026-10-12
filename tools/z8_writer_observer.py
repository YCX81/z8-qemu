# SPDX-License-Identifier: GPL-2.0-or-later
"""Optional passive C3.11 writer observer; no QEMU launch or guest mutation.

Only the observed mode0/format1 route is supported. Returned plans manipulate
debug breakpoints only. A caller must apply each plan atomically while all CPUs
are paused, before continuation; transport failure invalidates the interval.
Observed call order is not a frame/job ID, allocation generation or media order.
"""
from dataclasses import dataclass
import copy
import hashlib
import re
import struct

MAIN_SHA='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
BASE=0x40160000
OK=0x04320080
STORE,STORED=0x40ea365c,0x40ea3660
MAX_CHUNK=0x05000000


class ObservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Site:
    call:int
    ret:int
    target:int
    return_word:int


SITES={
    'writer':Site(0x40ea3638,0x40ea363c,0x406cc134,0x2a0003f4),
    'lookup':Site(0x406cd458,0x406cd45c,0x4085a8e0,0xaa0003f6),
    'open':Site(0x406cd468,0x406cd46c,0x4085b320,0xb9400268),
    'select':Site(0x406cd5f4,0x406cd5f8,0x406cddf8,0x7100041f),
    'segment':Site(0x406cd62c,0x406cd630,0x406ce07c,0x2a0003f8),
    'data':Site(0x406ce108,0x406ce10c,0x4085b9a0,0x6b1a001f),
    'padding':Site(0x406ce17c,0x406ce180,0x4085b9a0,0x6b1a001f),
    'final':Site(0x406cd6a0,0x406cd6a4,0x4085b6d4,0x6b1a001f),
    'cleanup':Site(0x406cd6b4,0x406cd6b8,0x4085aa7c,0x35000557),
    'cleanup_error':Site(0x406cd4d8,0x406cd4dc,0x4085aa7c,0x11002348),
}
# Single-instruction operand guards, independently disassembled from MAIN.
TARGET_WORDS={0x406cc134:0xf81e0ff4,0x4085a8e0:0xa9bf7bf3,
    0x4085b320:0xa9be53f5,0x406cddf8:0xa9bf7bf3,0x406ce07c:0xd101c3ff,
    0x4085b9a0:0xaa0303e8,0x4085b6d4:0xa9be53f5,0x4085aa7c:0xa9bf7bf3}
EXPECTED={p:struct.pack('<I',w) for p,w in TARGET_WORDS.items()}
for _s in SITES.values():
    EXPECTED[_s.call]=struct.pack('<I',0x94000000|(((_s.target-_s.call)//4)&0x3ffffff))
    EXPECTED[_s.ret]=struct.pack('<I',_s.return_word)
EXPECTED.update({STORE:struct.pack('<I',0xb902d114),STORED:struct.pack('<I',0x34000074)})
CALLS={s.call:name for name,s in SITES.items()}
RETURNS={s.ret:name for name,s in SITES.items()}


def verify_main(path):
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=MAIN_SHA:
        raise ObservationError('Original MAIN SHA mismatch')
    for pc,word in EXPECTED.items():
        if data[pc-BASE:pc-BASE+4]!=word:
            raise ObservationError(f'Original opcode mismatch at {pc:#x}')
    return dict(sha256=MAIN_SHA,points=len(EXPECTED),firmware_executed=False)


def parse_gpa(reply):
    match=re.fullmatch(r'gpa: (0x[0-9a-fA-F]+|0)\r?\n?',reply) if isinstance(reply,str) else None
    if not match:raise ObservationError('Unmapped, absent CPU or ambiguous gva2gpa response')
    value=int(match[1],0)
    if not 0<=value<1<<64:raise ObservationError('GPA outside uint64')
    return value


def hmp_translator(qmp):
    """Read-only adapter for locally verified QEMU hmp_gva2gpa(cpu-index)."""
    def translate(cpu,page):
        if qmp.command('query-status').get('running') is not False:
            raise ObservationError('Translation requires an already paused owned guest')
        return parse_gpa(qmp.hmp(f'gva2gpa {page:#x}',cpu))
    return translate


class Memory:
    """CPU-VA reads through fresh page translations, then physical RAM only.

    No VA aliases are inferred. RAM size/image-bank enablement must come from
    the owned runtime's verified launch/QOM receipt, not a guessed broad range.
    Each span is translated page by page without a cache across guest resumes.
    """
    def __init__(self,read_physical,translate_page,*,system_ram_bytes,image_banks=True,
                 max_pages=262144,max_read_bytes=1<<20):
        if system_ram_bytes not in (512<<20,1024<<20) or type(image_banks) is not bool:
            raise ObservationError('Explicit supported runtime RAM geometry required')
        if not 1<=max_pages<=262144 or not 1<=max_read_bytes<=1<<20:
            raise ObservationError('Invalid memory budget')
        self.reader,self.translator=read_physical,translate_page
        self.ranges=[(0xa00000000,0xa00000000+system_ram_bytes)]
        if image_banks:self.ranges += [(0x840000000,0x8adf8da00),(0x8adf8dc00,0x8fff47400)]
        self.pages=self.bytes_read=0
        self.max_pages,self.max_read_bytes=max_pages,max_read_bytes
        self.receipts=[]
    def span(self,cpu,address,size):
        if (type(cpu) is not int or not 0<=cpu<4 or type(address) is not int or
            type(size) is not int or not 0<size<=MAX_CHUNK or not 0<=address<(1<<64)-size+1):
            raise ObservationError('Invalid complete virtual span')
        cursor,end=address,address+size;segments=[];pages=0
        while cursor<end:
            if self.pages>=self.max_pages:raise ObservationError('Translation page budget exhausted')
            va_page=cursor&~4095
            pa_page=self.translator(cpu,va_page)
            self.pages+=1;pages+=1
            if type(pa_page) is not int or pa_page<0 or pa_page>=1<<64 or pa_page&4095:
                raise ObservationError('Unaligned or invalid translated physical page')
            n=min(end-cursor,4096-(cursor&4095));pa=pa_page+(cursor&4095)
            if not any(lo<=pa and pa+n<=hi for lo,hi in self.ranges):
                raise ObservationError('Translated span crosses RAM end, image gap or MMIO')
            if segments and segments[-1]['pa']+segments[-1]['size']==pa:
                segments[-1]['size']+=n
            else:segments.append(dict(va=cursor,pa=pa,size=n))
            cursor+=n
        receipt=dict(cpu=cpu,va=address,size=size,pages_checked=pages,segments=segments)
        self.receipts.append(receipt)
        return receipt
    def read(self,cpu,address,size):
        if type(size) is not int or not 0<size<=0x1000 or self.bytes_read+size>self.max_read_bytes:
            raise ObservationError('Bounded RAM read budget exceeded')
        span=self.span(cpu,address,size);data=[]
        for s in span['segments']:
            b=self.reader(s['pa'],s['size'])
            if not isinstance(b,bytes) or len(b)!=s['size']:raise ObservationError('Short physical RAM read')
            data.append(b)
        self.bytes_read+=size
        return b''.join(data)
    def number(self,cpu,address,fmt):
        return struct.unpack('<'+fmt,self.read(cpu,address,struct.calcsize('<'+fmt)))[0]
    def same(self,cpu,a,b,size=8):
        if a==b:
            self.span(cpu,a,size);return True
        lhs=self.span(cpu,a,size);rhs=self.span(cpu,b,size)
        return [(x['pa'],x['size']) for x in lhs['segments']]==[(x['pa'],x['size']) for x in rhs['segments']]


class WriterObserver:
    def __init__(self,memory,read_instruction,*,max_events=4096,max_writes=512,
                 max_lifetimes=64,max_requested_bytes=1<<30,max_duration_ns=120_000_000_000):
        if any(type(n) is not int or n<1 or n>cap for n,cap in
               ((max_events,4096),(max_writes,512),(max_lifetimes,64),
                (max_requested_bytes,1<<30),(max_duration_ns,120_000_000_000))):
            raise ObservationError('Invalid observer limits')
        self.memory,self.code=memory,read_instruction
        self.max_events,self.max_writes,self.max_lifetimes=max_events,max_writes,max_lifetimes
        self.max_requested_bytes,self.max_duration_ns=max_requested_bytes,max_duration_ns
        self.enabled=False;self.finished=False;self.failed=False
        self.events=[];self.lifetimes=[];self.gaps=[];self.armed=set()
        self.active=None;self.stack=[];self.guard=None;self.identity=None
        self.total_writes=self.total_requested=0;self.start_ns=self.last_ns=0
        self.first_file_failure=None
    def fail(self,message):
        self.failed=True;self.gaps.append(str(message));raise ObservationError(message)
    def enable(self,source_sha256,*,paused,start_ns):
        if self.enabled or self.finished or source_sha256!=MAIN_SHA or paused is not True:
            self.fail('Pinned MAIN, fresh observer and paused activation required')
        if type(start_ns) is not int or start_ns<0:self.fail('Invalid interval start clock')
        try:
            for pc,word in EXPECTED.items():
                if self.code(pc)!=word:self.fail(f'Runtime opcode mismatch {pc:#x}')
        except Exception as e:
            if isinstance(e,ObservationError):raise
            self.fail(f'Runtime code-read error: {e}')
        self.start_ns=self.last_ns=start_ns;self.enabled=True
        self.armed={SITES['writer'].call}
        return {'add':sorted(self.armed),'remove':[]}
    def _row(self,kind,cpu,r,t,**fields):
        if len(self.events)>=self.max_events:self.fail('Observation event cap')
        row=dict(observation=len(self.events),kind=kind,cpu=cpu,pc=r['PC'],sp=r['SP'],
                 t_ns=t,native_accepted=False,**fields)
        self.events.append(row);return row
    def _same(self,cpu,a,b,size=8):
        if not self.memory.same(cpu,a,b,size):self.fail('Pointer identity/alias translation changed')
    def _file(self,cpu,address,*,phase='ordinary',final_detail=None,status=None):
        b=self.memory.read(cpu,address,0xa0)
        span=copy.deepcopy(self.memory.receipts[-1])
        physical=[dict(pa=s['pa'],size=s['size']) for s in span['segments']]
        snapshot=dict(cpu=cpu,va=address,phase=phase,raw_hex=b.hex(),translation=span,
            physical_span=physical,previous_valid=copy.deepcopy(self.active.get('last_file_snapshot')),
            previous_raw_hex=self.active.get('last_file_raw_hex'),decoded=None,validated=False)
        # Preserve actual new bytes BEFORE interpreting length, ID, handle or
        # identity. A failure must not erase the value which caused it.
        self.events[-1].setdefault('file_snapshots',[]).append(snapshot)
        def reject(message):
            snapshot['error']=message
            if self.first_file_failure is None:self.first_file_failure=copy.deepcopy(snapshot)
            self.fail(message)
        get=lambda fmt,off:struct.unpack_from('<'+fmt,b,off)[0]
        n=get('I',0x94)
        row=dict(address=address,path_pointer=get('Q',0),mode=get('I',8),handle=get('i',0xc),
            position=get('Q',0x10),length=get('Q',0x18),propagated_length=get('Q',0x20),
            file_id=get('I',0x28),state=get('I',0x70),path_length=n,
            path_bytes_hex=b[0x74:0x94].hex(),encoding_verified=False,
            name_level=get('I',0x98),reference_bytes_hex=b[0x9c:0x9e].hex())
        snapshot['decoded']=copy.deepcopy(row)
        if n>31 or b[0x74+n]!=0:reject('Unbounded or unterminated original path')
        if row['file_id']!=self.active['file_id']:reject('Original file ID changed')
        if 'object_physical_span' in self.active and physical!=self.active['object_physical_span']:
            reject('File object physical identity changed inside observed lifetime')
        binding=self.active.get('open_file_binding')
        if binding and any(row[k]!=binding[k] for k in binding):
            reject('Opened file object/path identity changed inside observed lifetime')
        if phase=='final_return':
            # This exception is local to the paired 406cd6a4 return. The
            # original opened handle is retained, never globally replaced.
            before=final_detail['before'] if final_detail else None
            if (status!=OK or not before or not binding or before['mode']!=0x301 or
                before['state']!=3 or before['handle']!=self.active.get('opened_handle') or
                before['handle']<0 or self.active.get('final_reset_observed')):
                reject('Unsupported paired native finalize/reset transition')
            expected=dict(mode=0,handle=-1,position=0,length=final_detail['requested_final_length'],
                          propagated_length=0,state=1)
            if any(row[k]!=value for k,value in expected.items()):
                reject('Native finalize did not produce its exact state3 reset tuple')
        elif phase=='cleanup_before' and self.active.get('final_reset_observed'):
            after=self.active['final_reset']['after']
            if any(row[k]!=after[k] for k in ('mode','handle','position','length','propagated_length','state')):
                reject('Native final reset tuple changed before reference cleanup')
        elif 'opened_handle' in self.active and row['handle']!=self.active['opened_handle']:
            reject('File handle reused/changed inside observed lifetime')
        self.active.setdefault('object_physical_span',copy.deepcopy(physical))
        self.active['last_file_snapshot']=copy.deepcopy(row);self.active['last_file_raw_hex']=b.hex()
        snapshot['validated']=True
        return row
    def _writer(self,cpu,r,row):
        if self.active or self.stack:self.fail('Overlapping native writer lifetimes')
        if len(self.lifetimes)>=self.max_lifetimes:self.fail('Writer lifetime cap')
        if r['X00']!=0 or r['X01']!=1:self.fail('Only original mode0/format1 writer route is bound')
        identity=(cpu,r['SP'])
        if r['SP']&15 or (self.identity is not None and self.identity!=identity):
            self.fail('Writer task CPU/SP changed; no migration or TCB identity inferred')
        self.memory.span(cpu,r['SP']-0x180,0x180)
        self.identity=identity
        ctx,outer,record=r['X24'],r['X25'],r['X02']
        self._same(cpu,r['X20'],record,0x38)
        self._same(cpu,r['X26'],ctx+0xc0)
        count=self.memory.number(cpu,ctx+0xbc,'I')
        if not 1<=count<=64:self.fail('Unsupported original remaining-record count')
        ordinal=count-1;whole=ctx+0xc0+ordinal*0x2d8
        self.memory.span(cpu,whole,0x2d8)
        self._same(cpu,record,whole+0x48,0x38)
        self._same(cpu,self.memory.number(cpu,ctx+0x18,'Q'),outer,0x798)
        b=self.memory.read(cpu,record,0x38)
        fid=struct.unpack_from('<I',b)[0];metadata=struct.unpack_from('<Q',b,8)[0]
        self.memory.span(cpu,metadata,0x2574)
        outer_metadata=self.memory.number(cpu,outer+0x790,'Q')
        self.memory.span(cpu,outer_metadata,0x2574)
        life=dict(origin_observation=row['observation'],context=ctx,outer=outer,writer_record=record,
            whole_output_record=whole,remaining_count=count,record_index_raw=ordinal,
            file_id=fid,metadata=metadata,outer_metadata=outer_metadata,
            metadata_addresses_equal=self.memory.same(cpu,metadata,outer_metadata,0x2574),
            declared_length=struct.unpack_from('<Q',b,0x30)[0],frame_identity=None,
            allocation_generation_verified=False,thread_identity_verified=False,native_accepted=False,
            write_calls=0,actual_success_bytes=0,write_lengths_match=True,
            result_store_observed=False,open_status=None,final_status=None,cleanup_returned=False,
            selections=[],writes=[])
        self.active=life;self.lifetimes.append(life);row['identity']=copy.deepcopy(life)
    def _call(self,name,cpu,r,row):
        if name=='writer':self._writer(cpu,r,row);return {}
        life=self.active
        if life is None or life.get('writer_result') is not None:self.fail('Call outside active writer')
        if life.get('final_reset_observed') and name!='cleanup':
            self.fail('Operation after native final reset before reference cleanup')
        parent='segment' if name in ('data','padding') else 'writer'
        if not self.stack or self.stack[-1]['name']!=parent:
            self.fail('Unexpected nesting: previous native call has not returned')
        expected_sp=self.identity[1]-0x110-(0x70 if name in ('data','padding') else 0)
        if cpu!=self.identity[0] or r['SP']!=expected_sp:self.fail('Writer CPU migration or nested SP mismatch')
        if name not in ('data','padding'):
            self._same(cpu,r['X19'],life['writer_record'],0x38)
        if name=='lookup':
            if r['X00']!=life['file_id'] or 'file_object' in life:self.fail('Lookup ID or call order mismatch')
            return {}
        if 'file_object' not in life:self.fail('File object not observed')
        obj=life['file_object']
        if name=='select':
            if life['open_status']!=OK or r['X00']!=1:self.fail('Segment selection before successful open')
            self._same(cpu,r['X01'],life['writer_record'],0x38)
            self._same(cpu,r['X03'],r['SP']+0x70,16)
            if not 0<=r['X25']<9:self.fail('Format1 segment loop index outside9')
            if life.get('selection_pending'):self.fail('Selected segment was never consumed')
            return dict(selector=r['X02']&0xffffffff,loop_index=r['X25'],descriptor=r['X03'])
        self._same(cpu,r['X00'],obj,0xa0)
        before=self._file(cpu,obj,phase='cleanup_before' if name=='cleanup' else 'ordinary');row['file_before']=before
        if name=='open':
            if life['open_status'] is not None:self.fail('Repeated open')
            return {'before':before}
        if name=='segment':
            if life['open_status']!=OK or r['X01']!=life['file_id'] or not life.get('selection_pending'):
                self.fail('Segment call without matching successful selection')
            b=self.memory.read(cpu,r['X02'],16);source,total,padding=struct.unpack('<QII',b)
            if padding>total or padding>512:self.fail('Unsupported/broken bounded segment padding')
            self._same(cpu,r['X03'],r['SP']+0x9c,4)
            selected=life['selections'][-1]
            if selected['descriptor_hex']!=b.hex() or selected['loop_index']!=r['X25']:
                self.fail('Segment descriptor/index changed since selection')
            life['selection_pending']=False
            return dict(descriptor=r['X02'],source=source,total=total,padding=padding,
                        accumulated_pointer=r['X03'],data_requested=0,padding_requested=0,data_calls=0)
        if name in ('data','padding'):
            if not self.stack or self.stack[-1]['name']!='segment':self.fail('Write outside original segment call')
            segment=self.stack[-1]['detail'];requested=r['X02']
            if not 0<=requested<=MAX_CHUNK:self.fail('Write count outside native chunk bound')
            if r['X03']!=r['SP']+0xc:self.fail('Written-count output is not original SP+c')
            self._same(cpu,r['X20'],obj,0xa0)
            if name=='data':
                if segment['padding_requested'] or requested>segment['total']-segment['padding']-segment['data_requested']:
                    self.fail('Data chunk exceeds original remaining segment')
                if requested:self._same(cpu,r['X01'],segment['source']+segment['data_requested'],requested)
                segment['data_requested']+=requested;segment['data_calls']+=1
            else:
                if segment['padding_requested'] or requested!=segment['padding'] or r['X01']!=0xa04564e98:
                    self.fail('Padding source/count mismatch')
                segment['padding_requested']=requested
            if self.total_writes>=self.max_writes or self.total_requested+requested>self.max_requested_bytes:
                self.fail('Write count/byte observation cap')
            self.total_writes+=1;self.total_requested+=requested;life['write_calls']+=1
            span=self.memory.span(cpu,r['X01'],requested) if requested else None
            return dict(requested=requested,source=r['X01'],source_span=span,
                        written_pointer=r['X03'],before=before)
        if name=='final':
            if life['open_status']!=OK or life['final_status'] is not None:self.fail('Final-length call order')
            if r['X01'] not in (0,life['declared_length']):self.fail('Unexpected original final length')
            return dict(requested_final_length=r['X01'],before=before)
        if name.startswith('cleanup'):
            if life['cleanup_returned']:self.fail('Repeated cleanup')
            if name=='cleanup' and life['final_status'] is None:self.fail('Cleanup before final-length return')
            if name=='cleanup_error' and life['open_status'] in (None,OK):self.fail('Error cleanup without failed open')
            return {'before':before}
        self.fail('Unbound writer call')
    def _return(self,name,cpu,r,row,detail):
        life=self.active;status=r['X00']&0xffffffff;row['status_raw']=status
        if name=='writer':
            for key,reg in (('context','X24'),('outer','X25')):self._same(cpu,r[reg],life[key])
            self._same(cpu,r['X20'],life['writer_record'],0x38)
            life['writer_result']=status
            if status not in (0,1,3):self.fail('Unrecognized writer result')
            if status==0 and not (life['open_status']==OK and life['final_status']==OK and life['cleanup_returned']):
                self.fail('Successful writer result without observed inner lifecycle')
            return
        if name=='lookup':
            life['lookup_result']=r['X00']
            if r['X00']:
                life['file_object']=r['X00'];row['file_after']=self._file(cpu,r['X00'])
            return
        if name=='open':
            self._same(cpu,r['X22'],life['file_object'],0xa0)
            row['file_after']=self._file(cpu,life['file_object']);life['open_status']=status
            if status==OK:
                after=row['file_after']
                if after['handle']<0 or after['path_pointer']!=life['file_object']+0x74:
                    self.fail('Successful native open lacks supported handle/inline path')
                life['opened_handle']=after['handle']
                life['open_file_binding']={k:after[k] for k in ('address','file_id','path_pointer','path_length','path_bytes_hex')}
            return
        if name=='select':
            detail.update(status=status)
            if status==0:
                detail['descriptor_hex']=self.memory.read(cpu,detail['descriptor'],16).hex()
                life['selection_pending']=True
            elif status not in (1,2):self.fail('Unrecognized segment selection result')
            life['selections'].append(copy.deepcopy(detail));row['selection']=copy.deepcopy(detail);return
        if name=='segment':
            row['accumulated_requested_count']=self.memory.number(cpu,detail['accumulated_pointer'],'I')
            if status==0 and (detail['data_requested']+detail['padding_requested']!=detail['total'] or not detail['data_calls']):
                self.fail('Segment returned success with missing write observations')
            return
        if name in ('data','padding'):
            self._same(cpu,r['X20'],life['file_object'],0xa0)
            actual=self.memory.number(cpu,detail['written_pointer'],'I')
            after=self._file(cpu,life['file_object'])
            success=status==OK
            delta=after['position']-detail['before']['position']
            full=success and actual==detail['requested']
            if success and actual>detail['requested']:self.fail('Actual count exceeds requested write')
            if success and after['mode']!=2 and delta!=actual:full=False
            evidence=dict(kind=name,status_raw=status,requested=detail['requested'],
                count_word_read=actual,actual_written_if_success=actual if success else None,
                written_pointer=detail['written_pointer'],source=detail['source'],
                source_span=detail['source_span'],position_before=detail['before']['position'],
                position_after=after['position'],position_delta=delta,full_write_observed=full,
                native_accepted=False)
            life['writes'].append(evidence);row['write']=copy.deepcopy(evidence)
            life['actual_success_bytes']+=actual if success else 0
            life['write_lengths_match'] &= full
            return
        if name=='final':
            life['final_status']=status;life['requested_final_length']=detail['requested_final_length']
            row['file_after']=self._file(cpu,life['file_object'],phase='final_return',final_detail=detail,status=status)
            life['final_reset_observed']=True
            life['final_reset']=dict(before=copy.deepcopy(detail['before']),after=copy.deepcopy(row['file_after']),
                status_scope='4085b6d4 translated result of 402db308; 4085b430 return is ignored',
                internal_close_success_verified=False,media_durability_verified=False)
            life['final_length_observed']=row['file_after']['length']
            life['final_length_matches']=status==OK and life['final_length_observed']==detail['requested_final_length']
            return
        if name.startswith('cleanup'):
            # The object may be released here. Never dereference it after cleanup.
            life['cleanup_returned']=True;row['cleanup_semantics']='reference cleanup; flush/fsync unproved'
    def feed(self,cpu_registers,*,t_ns,paused):
        empty={'add':[],'remove':[]}
        if not self.enabled:return empty
        if self.failed or self.finished:self.fail('Observer cannot continue after failure/finish')
        if paused is not True or type(t_ns) is not int or t_ns<self.last_ns or t_ns-self.start_ns>self.max_duration_ns:
            self.fail('Unpaused, backwards or expired observation interval')
        self.last_ns=t_ns
        if len(cpu_registers)!=4 or {c for c,_ in cpu_registers}!={0,1,2,3}:
            self.fail('All four paused CPU snapshots required before de-duplication')
        hits=[(c,r) for c,r in cpu_registers if r.get('PC') in self.armed]
        if len(hits)>1:self.fail('Simultaneous managed boundaries: order/thread ambiguous')
        if not hits:
            # A different device may stop QEMU before the original BL executes.
            if self.guard:
                for c,r in cpu_registers:
                    if r.get('PC')==self.guard['call_pc'] and (c,r)!=self.guard['snapshot']:
                        self.fail('Another thread/CPU entered a disarmed call boundary')
            return empty
        cpu,r=hits[0];pc=r['PC'];before=set(self.armed);event_count_before=len(self.events)
        self.memory.receipts=[]
        try:
            if self.code(pc)!=EXPECTED[pc]:self.fail('Observed opcode changed')
            if self.identity and cpu!=self.identity[0]:self.fail('CPU migration unsupported')
            if type(r.get('SP')) is not int or r['SP']&15:self.fail('Invalid aligned native SP')
            if self.guard and pc==self.guard['target']:
                g=self.guard
                if (cpu,r['SP'])!=(g['cpu'],g['sp']):self.fail('Natural rearm CPU/SP changed')
                if g['name']=='store':
                    if r['X08']!=g['whole'] or (r['X20']&0xffffffff)!=g['result']:
                        self.fail('Original result store register identity changed')
                    value=self.memory.number(cpu,g['whole']+0x2d0,'I')
                    if value!=g['result']:self.fail('Original result store did not match return')
                    self.active['result_store_observed']=True;self.active['stored_result']=value
                    self.active=None
                elif r['X30']!=g['ret'] or any(r[k]!=v for k,v in g['args'].items()):
                    self.fail('Original BL did not reach expected callee/arguments')
                self._row('natural_rearm_'+g['name'],cpu,r,t_ns)
                self.armed.remove(pc)
                if g['name']!='store':self.armed.add(g['call_pc'])
                self.guard=None
            elif pc in CALLS:
                if self.guard:self.fail('Nested event before preceding BL rearm')
                name=CALLS[pc];site=SITES[name]
                row=self._row(name+'_call',cpu,r,t_ns)
                detail=self._call(name,cpu,r,row)
                self.stack.append(dict(name=name,cpu=cpu,sp=r['SP'],detail=detail))
                self.guard=dict(name=name,target=site.target,call_pc=pc,cpu=cpu,sp=r['SP'],ret=site.ret,
                    args={k:r[k] for k in ('X00','X01','X02','X03')},snapshot=(cpu,dict(r)))
                self.armed.remove(pc);self.armed.update((site.target,site.ret))
                if name=='writer':self.armed.update(s.call for n,s in SITES.items() if n!='writer')
                row['call_detail']=copy.deepcopy(detail)
            elif pc in RETURNS:
                name=RETURNS[pc]
                if self.guard or not self.stack or self.stack[-1]['name']!=name:
                    self.fail('Unpaired/out-of-order return or missing natural rearm')
                p=self.stack[-1]
                if (cpu,r['SP'])!=(p['cpu'],p['sp']):self.fail('Return CPU/SP mismatch')
                row=self._row(name+'_return',cpu,r,t_ns)
                self._return(name,cpu,r,row,p['detail']);self.stack.pop();self.armed.remove(pc)
                if name=='writer':
                    self.armed={SITES['writer'].call,STORE}
            elif pc==STORE:
                life=self.active
                if self.guard or not life or 'writer_result' not in life or r['SP']!=self.identity[1]:
                    self.fail('Unpaired output result store')
                self._same(cpu,r['X08'],life['whole_output_record'],0x2d8)
                if (r['X20']&0xffffffff)!=life['writer_result']:self.fail('Record-store status differs from return')
                self._row('result_store_before',cpu,r,t_ns,result=r['X20']&0xffffffff)
                self.guard=dict(name='store',target=STORED,call_pc=pc,cpu=cpu,sp=r['SP'],
                    whole=r['X08'],result=r['X20']&0xffffffff,snapshot=(cpu,dict(r)))
                self.armed.remove(pc);self.armed.add(STORED)
            else:self.fail('Unknown owned breakpoint')
            if self.events:self.events[-1]['translations']=copy.deepcopy(self.memory.receipts)
        except Exception as e:
            if len(self.events)>event_count_before:
                self.events[-1]['translations']=copy.deepcopy(self.memory.receipts)
            if not self.failed:self.failed=True;self.gaps.append(f'ABI/read failure: {e}')
            if isinstance(e,ObservationError):raise
            raise ObservationError(str(e)) from e
        return {'add':sorted(self.armed-before),'remove':sorted(before-self.armed)}
    def invalidate(self,reason):
        self.failed=True;self.gaps.append(str(reason))
    def finish(self,*,reason):
        if not isinstance(reason,str) or not reason:raise ObservationError('Explicit interval end reason required')
        if self.active or self.stack or self.guard:self.invalidate('incomplete native lifetime at interval exit: '+reason)
        self.finished=True
        result=self.summary();result['end_reason']=reason
        result['remove_breakpoints']=sorted(self.armed)
        return result
    def summary(self):
        return dict(enabled=self.enabled,interval_closed=self.enabled and self.finished and not self.failed,
            events=copy.deepcopy(self.events),lifetimes=copy.deepcopy(self.lifetimes),gaps=list(self.gaps),
            first_file_failure=copy.deepcopy(self.first_file_failure),
            total_write_calls=self.total_writes,total_requested_bytes=self.total_requested,
            limits=dict(events=self.max_events,writes=self.max_writes,lifetimes=self.max_lifetimes,
                        requested_bytes=self.max_requested_bytes,virtual_duration_ns=self.max_duration_ns),
            native_accepted=False,raw_precapture_verified=False,historical_trace_complete=False,
            frame_binding_verified=False,media_durability_verified=False,thread_identity_verified=False,
            scope='Observed original mode0/format1 call interval; fixed CPU/SP, no TCB/generation/media identity claim')
