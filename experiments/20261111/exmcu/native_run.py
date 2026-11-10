# SPDX-License-Identifier: GPL-2.0-or-later
"""Z8 run with the original EX03.00 MCU (ported upstream native MCU bridge).
No host MCU messages: EX firmware owns serial ch0/1; inputs are external contacts.
Plans: boot (just run), standby (idle past auto-off, then wake with G0 contact).
Emulation only."""
import argparse,hashlib,json,os,subprocess,sys,time,threading
from pathlib import Path
BASE=Path.home()/'.local/share/nikon-z8-qemu'
SNAP=BASE/'live-regress/snap'; ROOT=SNAP/'project'
STOCK=Path.home()/'Nikon/z8-qemu/.local/firmware/z8-c311-main.bin'
EX=Path.home()/'fxfix/exmcu/EX.bi'
P=argparse.ArgumentParser()
P.add_argument('--name',required=True)
P.add_argument('--main',type=Path,default=STOCK)
P.add_argument('--qemu',type=Path,default=BASE/'exmcu/build/qemu-system-aarch64')
P.add_argument('--seconds',type=int,default=900,help='wall budget')
P.add_argument('--plan',default='boot')
P.add_argument('--card-from',type=Path)
P.add_argument('--supply',default='C1',help='mcu-main-supply-pin, empty = off')
P.add_argument('--shot-every',type=float,default=5,help='virtual seconds between screendumps')
P.add_argument('--actions',default='',help='extra actions "vt:prop:value;..."')
P.add_argument('--qemu-arg',action='append',default=[])
A=P.parse_args()
OUT=BASE/'exmcu/runs'/A.name
if OUT.exists():raise FileExistsError(OUT)
MIN_GAP=int(os.environ.get('LIVE_QEMU_MIN_GAP_S','90'))
for ent in Path('/proc').iterdir():
    if not ent.name.isdigit():continue
    try:
        cmd=(ent/'cmdline').read_bytes().split(b'\0')[0]
        if b'qemu-system' not in cmd:continue
        el=subprocess.check_output(['ps','-p',ent.name,'-o','etimes='],text=True).strip()
        if el and int(el)<MIN_GAP:raise RuntimeError(f'QEMU {ent.name} started <{MIN_GAP} s ago')
    except (FileNotFoundError,ProcessLookupError,PermissionError,subprocess.CalledProcessError):pass
(BASE/'exmcu/runs').mkdir(parents=True,exist_ok=True)
os.environ['TMPDIR']=str(BASE/'exmcu/runs');sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'tools'))
import guest_transport
from private_bounded_qmp import QMP
guest_transport.QMP=QMP
from z8_run import PreparedGuest,state
from z8_properties import load_model_profile,make_boundary_peer
from service_serial8 import WritePeer
from PIL import Image
import shutil
recipe=json.loads((SNAP/'bundle.json').read_text())['recipe']
g=PreparedGuest(STOCK,A.qemu,BASE/'runs/raw-precapture-20261012/input14.raw',OUT,properties=load_model_profile()['properties'].copy())
g.command[g.command.index('-kernel')+1]=str(A.main.resolve())
at=g.command.index('-icount')+1;g.command[at]=g.command[at].replace('sleep=on','sleep=off')
names={s.split('=')[0] for s in load_model_profile()['properties']}
names|={'pcie-link-diagnostic-mask','pcie-dbi-model','pcie-ram-dma-input','pcie-image-ram-dma-input','pcie-intx-model','sensor-frame-tag-model'}
extra=[s for s in recipe['properties'] if s.split('=')[0] not in names and ':' not in s]
extra+=['z8-serial5-config-model=on','engine153-alternate-bank-model=on','z8-jpeg-decode=on','wave5-upstream-model=on']
extra=[('ccomp-gpio-model=off' if s.startswith('ccomp-gpio-model=') else s) for s in extra]
M=g.command.index('-M')+1
g.command[M]=g.command[M].replace('ccomp-gpio-model=on','ccomp-gpio-model=off')
g.command[M]+=','+','.join(extra)
g.command[M]+=',pcie-link-diagnostic-mask=4,pcie-dbi-model=on,pcie-ram-dma-input=on,pcie-image-ram-dma-input=on,pcie-intx-model=on,sensor-frame-tag-model=on'
g.command[M]+=f',mcu-image={EX},mcu-pins=G1=1:G13=1:G14=1:G15=1'
if A.supply:g.command[M]+=f',mcu-main-supply-pin={A.supply}'
g.command[g.command.index('-smp')+1]='4,maxcpus=5'
card=OUT/'capture-card.raw'
if A.card_from:
    subprocess.run(['cp','--sparse=always',str(A.card_from),str(card)],check=True)
else:
    from virtual_recording_card import create
    g.data['private_virtual_card']=create(card,'exfat')
import z8_nvme_evidence
g.command+=['-drive',f'file={card},if=none,id=card,format=raw','-device',z8_nvme_evidence.CARD_DEVICE]
selector=OUT/'z8-capture-selector2.bin';selector.write_bytes(bytes((1,)))
g.command+=['-device',f'loader,file={selector},addr=0x47f5af42,force-raw=on']
for ch in range(1,7):g.command+=['-chardev',f'file,id=z8-ex-usart{ch},path={OUT}/ex-usart{ch}-tx.log']
g.command+=A.qemu_arg
g.data['native_ex']=dict(main=str(A.main),main_sha256=hashlib.sha256(A.main.read_bytes()).hexdigest(),ex_sha256=hashlib.sha256(EX.read_bytes()).hexdigest(),plan=A.plan)
print('CMD',' '.join(g.command),flush=True)
# action schedule in virtual seconds: (vt, prop, value, label)
acts=[]
def tap(vt,label,prop,press,release,hold=.4):acts.append((vt,prop,press,label));acts.append((vt+hold,prop,release,label+'-up'))
def half(vt,label,hold=1):tap(vt,label,'mcu-pins','G1=0','G1=1',hold)
def key(vt,label,c):tap(vt,label,'mcu-contacts',c+'=0',c+'=1')
if A.plan=='standby':
    # 30 s standby timer cuts MAIN at ~36 s; wake by half press (G1) like a user would
    half(60,'wake-half',1.5)
    half(90,'h2');key(96,'menu1','23.6');key(104,'menu2','23.6')
elif A.plan=='standby-live':
    half(60,'wake-half',1.5)
    for n,t in enumerate(range(75,180,15)):half(t,f'keep{n}',1)
elif A.plan=='standby-g0':
    tap(60,'wake-g0','mcu-pins','G0=0','G0=1',.5)
    half(90,'h2');key(96,'menu1','23.6');key(104,'menu2','23.6')
elif A.plan=='keys':
    half(40,'h1');key(48,'menu1','23.6');key(56,'down','23.13');key(62,'menu2','23.6');key(70,'i','23.7');key(78,'i2','23.7')
for spec in filter(None,A.actions.split(';')):
    vt,prop,val=spec.split(':',2);acts.append((float(vt),prop,val,f'x{vt}'))
acts.sort(key=lambda x:x[0])
log=[]
def vtime():return json.loads(g.qmp.command('qom-get',path='/machine',property='runtime-clock-state'))['virtual_ns']/1e9
def shot(label):
    p=g.out/f'{label}.ppm';g.qmp.command('screendump',filename=str(p));Image.open(p).save(p.with_suffix('.png'));p.unlink()
OFF=json.load(open('/path/to/user/.local/share/nikon-z8-qemu/live-regress/rerun/runtime-offsets-18776.json'))
def fx():
    try:
        ok=lambda a:0xa00000000<=a<0xc00000000
        def rd(a,n=8):
            if not ok(a):raise ValueError(hex(a))
            return int.from_bytes(g.read_bytes(a,n),'little')
        root=rd(0xa032b50c8+16)
        if not ok(root):return dict(root=hex(root))
        core=rd(root+663*96+80)
        if not ok(core):return dict(core=hex(core))
        out=dict(core=hex(core),activation=rd(core+72,4),phase=rd(core+76,4),startup_phase=rd(core+0xb828+0xcc,4),startup_error=rd(core+0xb828+0xd4,4))
        reg=rd(core+2528);ui=rd(reg+8) if ok(reg) else 0;eng=rd(ui+8) if ok(ui) else 0
        out['engine']=hex(eng)
        if ok(eng):
            for k in ('requested','task_started','task_error','capture.phase','capture.reason','capture.irq_seq','capture.copied','capture.dropped','stream.error','stream.encoded','stream.width'):
                out[k]=rd(eng+OFF[k],4)
        return out
    except Exception as e:return dict(err=repr(e))
def mstate():
    try:return json.loads(g.qmp.command('qom-get',path='/machine',property='native-mcu-io-state'))
    except Exception as e:return dict(err=repr(e))
with g:
    g.rsp('z1,40160000,4')
    boundary=make_boundary_peer(g)
    spi8=WritePeer(g);spi9=WritePeer(g,channel=9,wire_bits=9,allow_dummy_rx=True)
    g.qmp.command('cont')
    deadline=time.monotonic()+A.seconds;nshot=0;ai=0;last_sup=None
    try:
        while time.monotonic()<deadline and not (g.out/'stop.request').exists():
            if 'Z9_UNKNOWN' in g.log():log.append(dict(event='unknown'));break
            if not g.qmp.command('query-status')['running']:
                log.append(dict(event='stopped',vt=vtime()));break
            boundary.tick()
            ch=json.loads(g.qmp.command('qom-get',path='/machine',property='serial-state'))
            spi8.clock_if_ready(ch[8]);spi9.clock_if_ready(ch[9])
            vt=vtime()
            while ai<len(acts) and vt>=acts[ai][0]:
                _,prop,val,label=acts[ai];g.qmp.command('qom-set',path='/machine',property=prop,value=val)
                log.append(dict(event='input',vt=vt,prop=prop,value=val,label=label));print('INPUT',label,round(vt,2),flush=True)
                if not label.endswith('-up'):shot(f'a{ai:03}-{label}-{vt:.0f}')
                ai+=1
            if vt>=nshot*A.shot_every:
                nshot=max(nshot,int(vt//A.shot_every))
                shot(f'p{nshot:04}-{vt:.0f}');nshot+=1
                m=mstate();sup=m.get('main_supply');pw=m.get('power')
                f=fx() if vt>15 else {};print('FX',round(vt,1),json.dumps(f),flush=True)
                rec=dict(event='sample',vt=vt,fx=f,supply=sup,power=pw,switch=json.loads(g.qmp.command('qom-get',path='/machine',property='native-mcu-state')))
                log.append(rec)
                if sup!=last_sup:print('SUPPLY',round(vt,1),sup,pw,flush=True);last_sup=sup
            time.sleep(.005)
    finally:
        (g.out/'native-log.json').write_text(json.dumps(log,indent=1))
        g.data['native_log_entries']=len(log)
print('Finished',OUT,flush=True)
