# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in bound OS wait return observation. Only reads original guest state.

4115fbbc -> 40d2be54 -> OS -> 40d2be80 (raw W0, before bool conversion).
An unrelated task at the shared wrapper is stepped past its MOV before rearm.
No task flags, clock, return value, interrupt or CPU register is written.
"""
import json
from pathlib import Path
ENTRY=0x4115fbbc;RETURN=0x40d2be80;STEP=0x40d2be84
NAMES={RETURN:'stop_raw_os_return',STEP:'stop_raw_other_task_step'}
MAX_SKIPS=32

def need(ok,message):
 if not ok:raise ValueError(message)

class Observer:
 def __init__(self,g,recovery,enabled=False):
  self.g=g;self.recovery=recovery;self.enabled=bool(enabled);self.identity=None;self.step=None
  self.row=dict(enabled=self.enabled,matched=False,skips=0,guest_state_modified=False)
 def transition(self,cpus,armed,t_ns):
  if not self.enabled or self.row['matched']:return []
  hits=[(cpu,r) for cpu,r in cpus if r['PC'] in armed]
  entries=[(cpu,r) for cpu,r in hits if r['PC']==ENTRY]
  if entries:
   need(len(entries)==1 and self.identity is None,'Ambiguous/repeated raw stop wait entry')
   cpu,r=entries[0];sp=r['SP'];mask=r['X01']&0xffffffff
   need(r['X00']==0xa0496efe0 and mask in (0x40,0x40040),'Raw stop wait object/mask')
   need(not sp&15 and 0xa00000020<=sp<0xa20000000,'Raw stop wait stack range')
   self.identity=(sp-0x20,mask)
   self.row.update(entry_cpu=cpu,entry_sp=hex(sp),entry_t_ns=t_ns,wait_mask=hex(mask))
   return [RETURN]
  returns=[(cpu,r) for cpu,r in hits if r['PC']==RETURN]
  if returns:
   need(self.identity is not None and self.step is None,'Unbound raw stop wait return')
   matches=[(cpu,r) for cpu,r in returns if r['SP']==self.identity[0]]
   need(len(matches)<=1,'Ambiguous raw stop wait return CPU')
   if matches:
    cpu,r=matches[0];raw=self.recovery.read(r['SP']+0x18,8)
    need(int.from_bytes(raw,'little')==ENTRY+4,'Raw stop wait saved caller identity changed')
    value=r['X00']&0xffffffff
    self.row.update(matched=True,return_cpu=cpu,return_pc=hex(RETURN),return_sp=hex(r['SP']),return_t_ns=t_ns,
      raw_os_w0=value,raw_os_signed=value-(1<<32) if value>>31 else value,
      caller_lr=hex(ENTRY+4),original_boolean=value==0,
      registers={k:hex(v) for k,v in r.items() if type(v) is int},shared_ram_bytes=self.recovery.bytes_requested,
      scope='Original OS return before mov W19,W0 and bool conversion; bound caller stack; not injected')
    path=Path(self.g.out)/'stop-wait-raw.json'
    with path.open('x') as f:json.dump(self.row,f,indent=2);f.write('\n')
    return []
   need(len(returns)==1 and self.row['skips']<MAX_SKIPS,'Raw stop wait unrelated-task budget/ambiguity')
   cpu,r=returns[0];self.step=(cpu,r['SP']);self.row['skips']+=1
   return [STEP]
  steps=[(cpu,r) for cpu,r in hits if r['PC']==STEP]
  if steps:
   need(len(steps)==1 and self.step is not None,'Unbound raw stop wait step')
   cpu,r=steps[0];need((cpu,r['SP'])==self.step,'Raw stop wait step identity changed')
   self.step=None;return [RETURN]
  return []
