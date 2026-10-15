# SPDX-License-Identifier: GPL-2.0-or-later
"""C3.11 still HEVC ABI observations. No execution, register or RAM writes.

Physical HMP byte reads only in the mandatory 512 MiB system RAM mapping.
Image planes are deliberately not followed: their allocation/ownership must
be validated separately. Limits include failed reads and span all contacts.
"""
import hashlib
import re
import struct
from z8_heif_receipts import Session

MAX_READ = 512
MAX_TOTAL = 64 * 1024
MAX_EVENTS = 48
MAX_SITE_HITS = 8
POINTS = {
    0x40c9700c: 'still_hevc_request',
    0x40c97808: 'still_hevc_constructed',
    0x40c97864: 'still_hevc_interface',
    0x40c978b4: 'still_hevc_submit',
    0x40c5cd4c: 'still_hevc_result_entry',
    0x40c99030: 'still_hevc_nal_consume',
}
# Original first instructions: SUB/STR change SP, MOV leaves it unchanged,
# BL sites are rearmed only after their original call returns. Never step or
# redirect a CPU. R/X19 or D/X0 plus the expected SP identify the same frame.
REARM = {pc+4:(pc,delta,anchor) for pc,delta,anchor in (
    (0x40c9700c,-0x80,'X00'),(0x40c97808,0,'X19'),(0x40c97864,0,'X19'),
    (0x40c978b4,0,'X19'),(0x40c5cd4c,-0x20,'X00'),(0x40c99030,-0x1f0,'X00'))}


class Observer:
    def __init__(self, guest):
        self.g = guest
        self.bytes_requested = 0
        self.events = 0
        self.hits = {pc:0 for pc in POINTS}
        self.pending = {}
        self.receipts = Session(guest,export_c1=getattr(guest,'_still_hevc_export_c1',False)) if getattr(guest,'_still_hevc_observer_enabled',False) else None

    def can_arm(self,pc):
        return pc in POINTS and self.hits[pc]<MAX_SITE_HITS and pc not in self.pending

    def transition(self,cpu_registers,armed):
        hits={}
        for cpu,regs in cpu_registers:
            pc=regs.get('PC')
            if pc in armed and (pc in POINTS or pc in REARM):hits.setdefault(pc,[]).append((cpu,regs))
        result=[]
        for pc,rows in hits.items():
            if len(rows)!=1:raise ValueError('Ambiguous simultaneous HEVC observer frames')
            cpu,regs=rows[0]
            if pc in POINTS:
                if not self.can_arm(pc):raise ValueError('HEVC site exhausted or already awaiting natural transition')
                if self.hits[pc]+1<MAX_SITE_HITS:
                    _,delta,anchor=REARM[pc+4]
                    self.pending[pc]=(regs['SP']+delta,anchor,regs[anchor])
                    result.append(pc+4)
            else:
                original,_,_=REARM[pc];expected=self.pending.get(original)
                if expected is None or regs.get('SP')!=expected[0] or regs.get(expected[1])!=expected[2]:
                    raise ValueError('HEVC rearm lost native stack/request identity')
                del self.pending[original]
                if self.can_arm(original):result.append(original)
        return result

    def read(self, address, size):
        if (type(address) is not int or type(size) is not int or
                not 0 < size <= MAX_READ or not 0xa00000000 <= address or
                address + size > 0xa20000000):
            raise ValueError('Read must fit mandatory system RAM and 1..512 byte bound')
        if self.bytes_requested + size > MAX_TOTAL:
            raise ValueError('Still HEVC total 64 KiB read budget exhausted')
        self.bytes_requested += size
        reply = self.g.qmp.hmp(f'xp /{size}bx {address:#x}')
        data = bytearray()
        for line in reply.splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r'\s*(?:0x)?([0-9a-fA-F]+):\s*(.*?)\s*', line)
            if not match or int(match[1], 16) != address + len(data):
                raise ValueError('Noncontiguous or invalid HMP physical memory response')
            tokens = match[2].split()
            if not tokens or any(not re.fullmatch(r'0x[0-9a-fA-F]{2}', x) for x in tokens):
                raise ValueError('Expected HMP byte tokens')
            data.extend(int(x, 16) for x in tokens)
            if len(data) > size:
                raise ValueError('Oversized HMP response')
        if len(data) != size:
            raise ValueError('Short HMP response')
        return bytes(data)

    def observe(self, pc, cpu, regs):
        if pc not in POINTS or self.events >= MAX_EVENTS or self.hits.get(pc,0)>=MAX_SITE_HITS:
            raise ValueError('Unknown point or still HEVC 48-event bound exhausted')
        self.events += 1
        self.hits[pc] += 1
        row = dict(point=POINTS[pc], pc=hex(pc), cpu=cpu,
                   registers={k: hex(v) for k, v in regs.items() if type(v) is int},
                   memory={}, errors={}, cpu_state_substituted=False,
                   site_hit=self.hits[pc],scope='Up to eight hits per site per run; 64 KiB ABI budget; optional image exports separate')

        def save(name, address, size):
            try:
                data = self.read(address, size)
                row['memory'][name] = dict(address=hex(address), size=size,
                    hex=data.hex(), sha256=hashlib.sha256(data).hexdigest())
                return data
            except Exception as error:
                row['errors'][name] = repr(error)
                return None

        save('S', 0xa02d41bb8, 0x160)
        r = None
        if pc in (0x40c9700c, 0x40c99030):
            r = save('R', regs.get('X00', 0), 0x150)
        elif pc in (0x40c97808, 0x40c97864, 0x40c978b4):
            r = save('R', regs.get('X19', 0), 0x150)
            sp = regs.get('SP', 0)
            save('C', sp + 0x78, 0x78)
            save('B', sp + 0x28, 0x50)
            if pc != 0x40c97808:
                save('F', sp + 0xf8, 8)
        if pc == 0x40c5cd4c:
            save('D_before_result', regs.get('X00', 0), 0x20)
        if pc == 0x40c99030:
            save('D', regs.get('X01', 0), 0x20)
            if r:
                save('O_before_consume', struct.unpack_from('<Q', r, 0x98)[0], 0x1e0)
                base = struct.unpack_from('<Q', r, 0x138)[0]
                capacity = struct.unpack_from('<I', r, 0x134)[0]
                lanes = struct.unpack_from('<I', r, 0x11c)[0]
                if lanes in (1, 4) and 0x1000 <= capacity <= 0x10000 and not capacity & (capacity-1):
                    for lane in range(lanes):
                        save(f'T{lane}_first4', base + lane * capacity, 64)
                else:
                    row['errors']['T'] = 'Invalid record capacity/lane count; no pointer walk'
        row['bytes_requested_total'] = self.bytes_requested
        if self.receipts is not None:
            if pc==0x40c978b4:row['c1_export']=self.receipts.capture_source()
            if pc==0x40c99030:row['job_receipt']=self.receipts.capture_job()
        return row

    def fault(self):
        """Current driver copies at a strict stop, even after PC hit caps retire.

        S can contain an older accepted C/B if stopped before the new copy;
        preserve its state and R independently instead of declaring a match.
        """
        row=dict(memory={},errors={},scope='Current fixed C3.11 globals; copied C/B/F may precede rejected programming')
        for name,address,size in (('R',0xa03b671a0,0x150),('S',0xa02d41bb8,0x160)):
            try:
                data=self.read(address,size)
                row['memory'][name]=dict(address=hex(address),size=size,hex=data.hex(),sha256=hashlib.sha256(data).hexdigest())
                if name=='S':
                    for label,offset,length in (('C_copy',0x10,0x78),('B_copy',0xa8,0x50),('F_copy',8,8)):
                        value=data[offset:offset+length]
                        row['memory'][label]=dict(address=hex(address+offset),size=length,hex=value.hex(),sha256=hashlib.sha256(value).hexdigest())
            except Exception as error:row['errors'][name]=repr(error)
        row['bytes_requested_total']=self.bytes_requested
        return row
