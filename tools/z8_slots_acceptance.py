# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned C32/C64 full-QEMU experiment. No camera or update package access."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time

from z8_guest import ROOT
from z8_properties import load_model_profile
from z8_run import PreparedGuest,menu_profile
from z8_boot import boot
from z8_c10_acceptance import Session as BaseSession,expected_flexible_extra
from z8_native_call import invoke
from z8_task_context import wait_for_task_context,read_current_context
from z8_slots_layout import Layout
from z8_slots_image import build_experiment
from z8_slots_mapping import prepare_code_mapping
from z8_picture_control import make_flexible_np3
from z8_c10_gui import menu_key_body,ppm_dimensions


def digest(data):return hashlib.sha256(data).hexdigest()


class Session(BaseSession):
    def __init__(self,g,layout):
        super().__init__(g);self.layout=layout
    def call(self,address,*args,timeout=15,observers=None):
        if self.call_cpu is None or not read_current_context(self.g,self.call_cpu)['callable']:
            self.call_cpu,_=wait_for_task_context(self)
        return invoke(self.g,address,args,cpu=self.call_cpu,
            stack_top=self.layout.address('stack')+self.layout.regions['stack'][1]-16,
            timeout=timeout,isolated=True,interrupt_policy='preserve',observers=observers)['result_x0']


def write_owned(s,address,data):
    L=s.layout;g=s.g
    if not L.data_base<=address<=address+len(data)<=L.data_base+L.reserved_bytes:raise ValueError('Outside owned input RAM')
    for offset in range(0,len(data),4096):
        part=data[offset:offset+4096];g.qt_command(f'write {address+offset:#x} {len(part):#x} 0x{part.hex()}')
        if g.read_bytes(address+offset,len(part))!=part:raise RuntimeError('Guest input write mismatch')


def record(s,slot,bank):return s.g.read_bytes(s.layout.record(slot,bank),0x400)


def snapshot(s):
    """Read original current objects directly; pending SAVE header is not an oracle."""
    L=s.layout;g=s.g;ptr=L.address('calls');selectors=[];slots=[];sources=[];main=[];extra=[]
    for target,getter in ((0,0x410A7B58),(1,0x410A7D10)):
        if s.call(getter,ptr,ptr+16)!=1:raise RuntimeError('Original current getter failed')
        sel=struct.unpack('<I',g.read_bytes(ptr,4))[0];params=g.read_bytes(ptr+16,0x158)
        decoded=L.decode(sel)
        if decoded is None:raise RuntimeError(f'Expected custom current, got {sel:#x}')
        slot,bank=decoded
        if target==0 and bank!=0:raise RuntimeError('Photo cannot use the movie source bank')
        raw=record(s,slot,bank)
        g.check(f'current {target} primary matches source {bank}:{slot}',params[:0x114]==raw[0x10:0x124])
        if struct.unpack_from('<H',params,0x110)[0]==32:
            g.check(f'current {target} Flexible Color44 matches source',params[0x12C:]==raw[0x370:0x39C])
        selectors.append(sel);slots.append(slot);sources.append(bank)
        main.append(digest(params[:0x114]));extra.append(digest(params[0x12C:]))
    return dict(schema=3,slots=slots,sources=sources,selectors=selectors,primary_sha256=main,extra_sha256=extra)


def settled(s,expected,name):
    s.run(2)
    actual=snapshot(s);s.g.check('scheduled current stability: '+name,actual==expected)
    s.g.data.setdefault('slots_settled',[]).append(dict(phase=name,state=actual,wall_pacing_seconds=2))


def set_integer(s,key,value):
    s.g.check(f'original integer write {key:#x}',s.call(0x41015D6C,key,value,s.layout.address('calls')+0x300)==1)
    s.g.check(f'original integer read {key:#x}',s.call(0x410DF224,key)==value)


def high_select(s,slot,bank):
    selector=s.layout.selector(slot,bank)
    s.g.check(f'original high selection {bank}:{slot}',s.call(0x403F96A8,selector)==1)
    s.g.check(f'original selected property {bank}',s.call(0x410DF224,0x07000633 if bank==0 else 0x0700122B)==s.layout.code(slot))


def verify_loaded(s,experiment):
    g=s.g;L=s.layout
    for p in experiment['patches']:
        g.check(f'loaded guarded patch {p["address"]:#x}',g.read_bytes(p['address'],len(p['replacement']))==p['replacement'])
    for r in experiment['regions']:
        if r['permissions']=='rx':g.check(f'loaded executable {r["address"]:#x}',g.read_bytes(r['address'],len(r['data']))==r['data'])
    heaps=g.read_bytes(0xA03203DC8,64)
    handle,size,base=struct.unpack_from('<Q8xQ',heaps)[0],struct.unpack_from('<I',heaps,8)[0],struct.unpack_from('<Q',heaps,16)[0]
    g.check('native allocator excludes the explicit 1 MiB reservation',bool(handle) and size==L.general_bytes and base==L.general_base)
    g.check('adjacent native heap unchanged',struct.unpack_from('<IQ',heaps,40)[0]==0x400000 and struct.unpack_from('<Q',heaps,48)[0]==0xA1172B000)
    g.data['slots_native_heap_verified']=True


def import_all(s,entry):
    L=s.layout;g=s.g
    for bank in (0,1):
        for slot in range(1,L.slots+1):
            seed=slot+bank*64;name=f'C{slot} LAB M{bank}'
            blob=make_flexible_np3(name,seed);write_owned(s,L.address('input'),blob)
            before=g.read_bytes(L.address('records'),L.record_bytes)
            g.check(f'original NP3 import {bank}:{slot}',s.call(entry,L.address('input'),len(blob),slot,bank)==1)
            after=g.read_bytes(L.address('records'),L.record_bytes);at=L.record(slot,bank)-L.address('records')
            raw=after[at:at+0x400]
            g.check(f'import preserves every other record {bank}:{slot}',before[:at]==after[:at] and before[at+0x400:]==after[at+0x400:])
            g.check(f'import name/keys/type {bank}:{slot}',struct.unpack_from('<4I',raw)==(1,*L.keys(slot,bank)) and
                raw[0x10:0x24].split(b'\0')[0]==name.encode() and struct.unpack_from('<H',raw,0x120)[0]==32)
            g.check(f'import nonzero Flexible Color44 {bank}:{slot}',raw[0x370:0x39C]==expected_flexible_extra(seed))
            if slot%8==0:print(f'Imported bank {bank} through C{slot}',flush=True)


def publish(s,entry):
    L=s.layout
    for bank in (0,1):
        ptr=s.call(entry,bank);codes=struct.unpack('<'+'I'*L.slots,s.g.read_bytes(ptr+0x7C,L.slots*4))
        s.g.check(f'complete custom list bank {bank}',codes==tuple(L.code(slot) for slot in range(1,L.slots+1)))
        s.g.check(f'list guard bank {bank}',s.g.read_bytes(ptr+0x3F0,16)==b'SLOT-LIST-GUARD!')


def saved_state(s,entry):
    s.g.check('guest NAND complete commit',s.call(entry,timeout=30)==1)
    state=snapshot(s)
    header=struct.unpack('<4I',s.g.read_bytes(s.layout.address('records')+48,16))
    s.g.check('guest SAVE synchronizes actual selectors into header',header==tuple(state['slots']+state['sources']))
    settled(s,state,'after_guest_SAVE')
    return state


def gui(s,scenario):
    """Original MCU input and unchanged QMP captures. Glyph review stays separate."""
    g=s.g;L=s.layout;directory=g.out/'slot-menu';directory.mkdir(exist_ok=False)
    result=dict(status='running',scenario=scenario,frames=[],captures=[],states=[],
        host_pixel_edits=False,glyphs_visually_reviewed=False)
    g.data['slots_gui']=result;deadline=time.monotonic()+360
    def healthy():
        if time.monotonic()>deadline:raise TimeoutError('GUI wall-clock deadline')
        if g.qmp.command('query-status')['running'] or 'Z9_UNKNOWN' in g.log():raise RuntimeError('GUI requires healthy paused guest')
    def send(body,seconds):
        healthy();first=len(s.peer.record['rx']);s.peer.enqueue_external(body,2);s.run(seconds)
        matches=[r for r in s.peer.record['rx'][first:] if r.get('family')==2 and r.get('body_hex')==body.hex() and r.get('complete') is True]
        if len(matches)!=1:raise RuntimeError('MCU frame not completed exactly once')
        result['frames'].append(dict(body=body.hex(),wall_seconds=seconds))
    def press(key,release=3):
        try:send(menu_key_body(key),0.5)
        finally:
            if 'Z9_UNKNOWN' not in g.log():send(menu_key_body(),release)
    def capture(name):
        healthy();path=directory/(name+'.ppm');g.qmp.command('screendump',filename=str(path))
        raw=path.read_bytes();width,height=ppm_dimensions(raw);rgb=raw[-width*height*3:]
        row=dict(path=str(path),sha256=digest(raw),rgb_sha256=digest(rgb),width=width,height=height)
        result['captures'].append(row);print('GUI_CAPTURE',name,flush=True);return row
    def verify(slot,bank,phase):
        state=snapshot(s)
        wanted=[L.selector(L.slots,0),L.selector(slot,bank)] if bank else [L.selector(slot,0)]*2
        g.check('MCU current objects '+phase,state['selectors']==wanted)
        code=s.call(0x410DF224,0x0700122B if bank else 0x07000633)
        g.check('MCU property code '+phase,code==L.code(slot))
        if bank:g.check('MCU video disables inheritance',s.call(0x410DF224,0x0300122C)==0)
        settled(s,state,phase);result['states'].append(dict(phase=phase,state=state,property=code))
    try:
        send(menu_key_body(),2);press('menu',release=10)
        g.check('original MENU baseline before fixed navigation',capture('00-menu')['rgb_sha256']==menu_profile(g)['menu_rgb_sha256'])
        video=scenario.startswith('video')
        if video:
            for key in ('left','down','right'):press(key)
        for _ in range(12 if video else 13):press('down')
        press('right');capture('01-initial')
        if scenario=='video-cold':
            verify(L.slots,1,'cold_video');press('left');capture('02-summary');press('right');capture('03-reopened')
        else:
            if video:
                # Same-as-photo is initially selected; wrap up to the final row.
                press('up');capture('02-highest-focused');press('ok');verify(L.slots,1,'video_independent');capture('03-independent-summary');press('right')
            press('up');capture('04-previous-focused');press('ok');verify(L.slots-1,int(video),'previous_slot');capture('05-previous-summary')
            press('right');press('down');capture('06-highest-focused');press('ok');verify(L.slots,int(video),'highest_slot');capture('07-highest-summary')
            press('right');capture('08-highest-reopened')
        result['status']='native_selection_passed_visual_review_required'
    except BaseException as exc:
        result.update(status='failed',error=repr(exc));raise
    finally:(directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def run(args):
    L=Layout(args.slots)
    props=load_model_profile()['properties'].copy()
    guest=PreparedGuest(args.firmware,args.qemu,args.raw,args.output,properties=props,
        nand_image=args.media,create_nand_image=args.phase=='create')
    experiment=build_experiment(args.firmware.read_bytes(),guest.out/'simulation-image',L)
    guest.command[guest.command.index('-kernel')+1]=str(experiment['derived_main'])
    for region in experiment['loader_regions']:
        guest.command+=['-device',f'loader,file={region["path"]},addr={region["address"]:#x},force-raw=on']
    guest.data['slots_image']=experiment['metadata'];guest.data['slots_phase']=args.phase
    with guest as g:
        prepare_code_mapping(g,L);boot(g,lambda message:print(message,flush=True));s=Session(g,L)
        try:
            s.run(20);verify_loaded(s,experiment);entry=experiment['plans']['core']['entries']
            if args.phase=='create':
                g.check('new media has no saved slot image',s.call(entry['load'],timeout=30)==0)
                import_all(s,entry['import_np3']);publish(s,entry['list'])
                set_integer(s,0x0300122C,0)
                high_select(s,L.slots,0);high_select(s,L.slots,1)
                independent=snapshot(s);settled(s,independent,'independent_banks')
                set_integer(s,0x0300122C,1);high_select(s,L.slots-1,0);high_select(s,L.slots,0)
                state=saved_state(s,entry['save'])
                expected={f'{b}:{slot}':digest(record(s,slot,b)) for b in (0,1) for slot in range(1,L.slots+1)}
            else:
                if args.expected is None:raise ValueError('Restore requires independent prior hashes')
                expected=json.loads(args.expected.read_text());reference=json.loads((args.expected.parent/'expected-selection.json').read_text())
                g.check('new process guest NAND LOAD',s.call(entry['load'],timeout=30)==1)
                for bank in (0,1):
                    for slot in range(1,L.slots+1):g.check(f'cold record {bank}:{slot}',digest(record(s,slot,bank))==expected[f'{bank}:{slot}'])
                state=snapshot(s);g.check('guest restored exact selected source and parameters',state==reference)
                # Use actual guest-loaded metadata, never expected host values.
                set_integer(s,0x0300122C,int(state['sources'][1]==0))
                high_select(s,state['slots'][0],0)
                if state['sources'][1]==1:high_select(s,state['slots'][1],1)
                settled(s,state,'cold_native_application');publish(s,entry['list'])
                g.data['slots_cold_restored']=True
            g.data['slots_service_passed']=True;print('SLOT_SERVICE_PASSED',args.phase,flush=True)
            if args.gui!='none':gui(s,args.gui)
            if args.gui!='video-cold':state=saved_state(s,entry['save'])
            else:state=snapshot(s)
            for bank in (0,1):
                for slot in range(1,L.slots+1):g.check(f'final record unchanged {bank}:{slot}',digest(record(s,slot,bank))==expected[f'{bank}:{slot}'])
            (g.out/'expected-records.json').write_text(json.dumps(expected,indent=2)+'\n')
            (g.out/'expected-selection.json').write_text(json.dumps(state,indent=2)+'\n')
            g.data['slots_final_state']=state
            g.data['slots_media_state']=json.loads(g.qmp.command('qom-get',path='/machine',property='z8-nand-image-state'))
            g.data['slots_complete']=True;print('SLOT_ACCEPTANCE_PASSED',args.phase,args.gui,flush=True)
        finally:g.qmp.command('stop');s.peer.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--slots',type=int,choices=(32,64),default=32)
    p.add_argument('--phase',choices=('create','restore'),required=True)
    p.add_argument('--gui',choices=('none','photo','video','video-cold'),default='photo')
    for name in ('firmware','qemu','output','media'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--raw',type=Path,default=ROOT/'.local/input.raw')
    p.add_argument('--expected',type=Path)
    run(p.parse_args())
