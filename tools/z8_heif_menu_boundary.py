# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in external MENU timing at a native CSEQ task800 branch boundary.

No task flag, register, instruction, result or IRQ is substituted. The owner
keeps the stopped VM at AFTER until the external MENU contact is queued.
"""
from z8_capture_recovery import MAX_EVENTS,need
ENTER=0x40f43abc;AFTER=0x40f43ae8
POINTS={ENTER:'menu_task800_enter',AFTER:'menu_task800_after'}
WAIT_NS=2_000_000_000

def prepare(peer):
    """Arm once after retirement; a ready boundary must stay stopped for MENU."""
    m=peer.menu_boundary
    if not m.enabled:return True
    if m.phase=='idle':
        peer.g.qmp.command('stop');peer.handle()
        pc=m.arm(peer.now());peer.arm(pc,'capture_'+POINTS[pc])
        peer.g.qmp.command('cont');return False
    if m.phase.startswith('waiting'):
        m.check(peer.now());return False
    need(m.phase=='ready','MENU boundary unexpected phase')
    return True

class MenuBoundary:
    def __init__(self,g,enabled=False,shared=None):
        self.g=g;self.enabled=enabled;self.shared=shared;self.phase='idle';self.identity=None
        self.row=dict(status='idle',points=[],guest_state_modified=False,
            scope='External MENU after native task800 bit11 branch; not hardware completion or full CSEQ task return')
        if enabled:
            need(shared is not None,'MENU boundary requires bounded recovery reader')
            g.data['capture_trace']['heif_menu_boundary']=self.row
    def arm(self,t_ns):
        if not self.enabled:return None
        need(self.phase=='idle' and type(t_ns) is int and t_ns>=0,'MENU boundary already armed or bad clock')
        self.phase='waiting-enter';self.row.update(status=self.phase,armed_t_ns=t_ns)
        return ENTER
    def check(self,t_ns):
        if self.phase.startswith('waiting') and t_ns-self.row['armed_t_ns']>=WAIT_NS:
            self.phase='failed';self.row['status']='failed';self.row['error']='MENU task800 boundary timeout'
            raise RuntimeError(self.row['error'])
    def observe(self,pc,cpu,regs,t_ns):
        try:
            self.check(t_ns)
            need(self.enabled and type(t_ns) is int and t_ns>=self.row['armed_t_ns'],'Unarmed/stale MENU boundary')
            need(self.shared.events<MAX_EVENTS,'Recovery event budget exhausted')
            need(cpu in range(4) and regs.get('X19')==0x800 and
                 type(regs.get('SP')) is int and not regs['SP']&15 and
                 0xa00000000<=regs['SP']<0xa20000000,'MENU boundary task identity/mask')
            identity=(cpu,regs['SP'])
            if pc==ENTER:
                need(self.phase=='waiting-enter','Duplicate MENU boundary entry')
                self.identity=identity;self.phase='waiting-after';next_pc=AFTER
            else:
                need(pc==AFTER and self.phase=='waiting-after' and identity==self.identity,
                     'MENU boundary exit identity/order')
                raw=self.shared.read(0xa03bbb430,16)
                words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,16,4)]
                mode=int.from_bytes(self.shared.read(0xa03bba97c,4),'little')
                permission=self.shared.read(0xa03bd042c,1)[0]
                self.row.update(native_words=words,context_mode=mode,full_worker_permission=permission,
                                application=self.shared.application())
                need(words[2:]==[2,1] and mode==2 and permission==1,'MENU boundary native readiness changed')
                self.phase='ready';next_pc=None
            self.shared.events+=1
            self.row['points'].append(dict(pc=hex(pc),cpu=cpu,sp=hex(regs['SP']),mask=hex(regs['X19']),t_ns=t_ns))
            self.row.update(status=self.phase,ram_bytes_total=self.shared.bytes_requested)
            return next_pc
        except Exception as e:
            self.phase='failed';self.row.update(status='failed',error=repr(e));raise
    def holds(self,label):return self.enabled and self.phase=='ready' and label=='shot2-menu'
    def sent(self,t_ns):
        need(self.phase=='ready' and t_ns>=self.row['points'][-1]['t_ns'],'MENU send without native boundary')
        self.phase='sent';self.row.update(status='sent',menu_sent_t_ns=t_ns)
