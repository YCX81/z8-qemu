# SPDX-License-Identifier: GPL-2.0-or-later
"""i-menu Picture Control / NP3 card import repro. Emulation only, never flashed.
Plans:
  imenu : wake, wait for slotcard injection, i, OK (Picture Control cell), up x6, OK, i.
  np3   : MENU, PHOTO tab, seek Manage Picture Control > Load/save > Copy to camera, then list.
Optional --unlock writes the single byte 0xa032e78a0=0 right before each key (QEMU never
delivers the readiness event that clears it on hardware); logged as a harness override.
Optional --garbage FILE preloads guest RAM 0xa1162b000 (1 MiB slot reservation) before reset.
"""
import argparse,hashlib,json,os,shutil,struct,subprocess,sys,time,threading
from pathlib import Path
from types import SimpleNamespace
HERE=Path('/path/to/user/.local/share/nikon-z8-qemu/imenu-c64')
PULL=Path('/path/to/user/fxfix/pull')
NIKON=Path.home()/'Nikon'
SNAP=Path.home()/'.local/share/nikon-z8-qemu/live-regress/snap'
ROOT=SNAP/'project'
BASE=Path.home()/'.local/share/nikon-z8-qemu'
RUNROOT=BASE/'imenu-c64/runs'
STOCK=NIKON/'z8-qemu/.local/firmware/z8-c311-main.bin'
LIVE_DRIVER=BASE/'live-audio/regress/rerun-w5/retry-driver'
P=argparse.ArgumentParser()
P.add_argument('--name',required=True)
P.add_argument('--main',type=Path,required=True)
P.add_argument('--plan',choices=['imenu','np3','keyprobe','idle','cardswap'],required=True)
P.add_argument('--keys',default='',help='keyprobe: off:sw0:sw1 list separated by ;  or imenu enter key spec')
P.add_argument('--fenestra',type=Path,required=True,help='dir copied to card FENESTRA/')
P.add_argument('--store',type=Path,help='dir of sc*.rec added to FENESTRA/')
P.add_argument('--np3',type=Path,action='append',default=[],help='NP3 files put in card root')
P.add_argument('--garbage',type=Path)
P.add_argument('--unlock',action='store_true')
P.add_argument('--seconds',type=int,default=900)
P.add_argument('--lefts',type=int,default=6)
P.add_argument('--gap',type=float,default=2)
P.add_argument('--memwatch',default='',help='memwatch.so args, comma separated (watch=,poke=a:v,trigpc=,trign=)')
P.add_argument('--video',action='store_true',help='hold the photo/video selector at video (MCU body 0x57)')
P.add_argument('--okcov',default='',help='okcov.so args')
P.add_argument('--idle',type=float,default=120)
P.add_argument('--points',default='',help='kind:addr,... passive PC observer sites')
P.add_argument('--card-from',type=Path,help='reuse this card image (sparse copy), no FENESTRA write')
P.add_argument('--irqmap',default='')
P.add_argument('--mmiolog',default='')
P.add_argument('--pull-gap',type=float,default=30)
P.add_argument('--out-gap',type=float,default=30)
P.add_argument('--after-gap',type=float,default=60)
P.add_argument('--qemu-arg',action='append',default=[])
P.add_argument('--powercut',default='',help='powercut.so args, comma separated')
P.add_argument('--qemu',type=Path,default=BASE/'merged/work/build/qemu-system-aarch64')
A=P.parse_args()
MIN_GAP=int(os.environ.get('LIVE_QEMU_MIN_GAP_S','90'))
for ent in Path('/proc').iterdir():
    if not ent.name.isdigit():continue
    try:
        cmd=(ent/'cmdline').read_bytes().split(b'\0')[0]
        if b'qemu' not in Path(os.fsdecode(cmd)).name.encode():continue
        el=subprocess.check_output(['ps','-p',ent.name,'-o','etimes='],text=True).strip()
        if el and int(el)<MIN_GAP:raise RuntimeError(f'QEMU {ent.name} started <{MIN_GAP} seconds ago')
    except (FileNotFoundError,ProcessLookupError,PermissionError):pass
RUNROOT.mkdir(parents=True,exist_ok=True)
OUT=RUNROOT/A.name
if OUT.exists():raise FileExistsError(OUT)
os.environ['TMPDIR']=str(RUNROOT)
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'tools'));sys.path.insert(0,str(LIVE_DRIVER))
import z8_capture_probe as probe
import pipe_transport
_init=pipe_transport.PipeIO.__init__
def bounded_init(self,base,timeout=2):_init(self,base,timeout=timeout)
pipe_transport.PipeIO.__init__=bounded_init
pipe_transport.install()
from PIL import Image
if A.video:
    def _vid_body(f):
        def g(*a,**k):
            b=bytearray(f(*a,**k));b[0x57]=1;return bytes(b)
        return g
    def _vid_exp(e):
        e=bytearray(e);w=struct.unpack_from('<I',e,0x24)[0]|0x10000;struct.pack_into('<I',e,0x24,w);return bytes(e)
    _kb=probe.key_body;probe.key_body=_vid_body(_kb)
    import boot,z8_run;boot.key_body=_vid_body(boot.key_body);z8_run.key_body=_vid_body(z8_run.key_body)
    _ek=probe.expected_keys;probe.expected_keys=lambda *a,**k:_vid_exp(_ek(*a,**k))
    _nc=probe.navigation_contact
    def _nav(action):
        b,e=_nc(action);return b,_vid_exp(e)
    probe.navigation_contact=_nav
import virtual_recording_card as vrc,exfat_put
_create=vrc.create
def put_root(path,name,data):
    with open(path,'r+b') as f:
        f.seek(0);part=struct.unpack_from('<I',f.read(512),0x1c6)[0]*512
        f.seek(part);vbr=f.read(512)
        _,_,heap,_,root=struct.unpack_from('<IIIII',vbr,80)
        ss=1<<vbr[108];sc=ss<<vbr[109];co=lambda c:part+heap*ss+(c-2)*sc
        f.seek(co(root));rd=bytearray(f.read(sc));i=0;bm=None
        while rd[i]:
            if rd[i]==0x81:bm=struct.unpack_from('<IQ',rd,i+20)
            i+=32
        fc,blen=bm;f.seek(co(fc));bitmap=bytearray(f.read(blen))
        n=max(1,(len(data)+sc-1)//sc);c=2
        while not all(not bitmap[(c+k-2)//8]&(1<<((c+k-2)%8)) for k in range(n)):c+=1
        for k in range(n):bitmap[(c+k-2)//8]|=1<<((c+k-2)%8)
        f.seek(co(c));f.write(data)
        e=exfat_put._entry_set(name,0x20,c,len(data))
        rd[i:i+len(e)]=e;f.seek(co(root));f.write(rd);f.seek(co(fc));f.write(bitmap)
    return dict(name=name,bytes=len(data),cluster=c)
def card_create(path,fs='blank'):
    if A.card_from:
        subprocess.run(['cp','--sparse=always',str(A.card_from),str(path)],check=True)
        print('CARD-FROM',A.card_from,flush=True);return dict(card_from=str(A.card_from))
    result=_create(path,fs)
    if fs=='exfat':
        import vrc_extents_helper;result['recopied_extents']=vrc_extents_helper.recopy(path,result)
    files={p.name:p.read_bytes() for p in sorted(A.fenestra.iterdir()) if p.is_file()}
    if A.store:files.update({p.name.lower():p.read_bytes() for p in sorted(A.store.glob('sc*.rec'))})
    result['fenestra_card']=exfat_put.put(path,'FENESTRA',files)
    result['fenestra_files']={k:len(v) for k,v in files.items()}
    np3s=[q for p in A.np3 for q in (sorted(p.glob('*.NP3')) if p.is_dir() else [p])]
    result['root_np3']=[put_root(path,p.name,p.read_bytes()) for p in np3s]
    print('CARD',json.dumps({k:v for k,v in result.items() if k!='recopied_extents'},default=str),flush=True)
    return result
vrc.create=card_create
def _extents_separate_fd(stream,length):
    fd=os.open(f'/proc/self/fd/{stream.fileno()}',os.O_RDONLY);position=0
    try:
        while position<length:
            try:start=os.lseek(fd,position,os.SEEK_DATA)
            except OSError:return
            end=min(length,os.lseek(fd,start,os.SEEK_HOLE));yield start,end;position=end
    finally:os.close(fd)
vrc.extents=_extents_separate_fd

LOCK=0xa032e78a0
TEXT_IDS={}
WANT=['Manage Picture Control','Load/save','Copy to camera','PHOTO SHOOTING MENU','Set Picture Control']
def text_ids(g):
    if TEXT_IDS:return TEXT_IDS
    rd=lambda a,n:int.from_bytes(g.read_bytes(a,n),'little')
    count=rd(0xa0281cd5c,4)
    if count==65535:count=3136
    if not 1000<=count<=8192:return {}
    poolroot=rd(0xa02f2ac38,8);offsetroot=rd(0xa02f2ac48,8)
    if not poolroot or not offsetroot:return {}
    pools=struct.unpack('<36Q',g.read_bytes(poolroot,288));arrays=struct.unpack('<36Q',g.read_bytes(offsetroot,288))
    main=A.main.read_bytes()
    for lang,pool in enumerate(pools):
        if not 0x40160000<=pool<=0x432a0258 or 0x432a0258-pool>1024*1024:continue
        offs=struct.unpack('<'+str(count+1)+'I',g.read_bytes(arrays[lang],4*(count+1)))
        if 0x432a0258-pool not in offs:continue
        found={}
        for s in WANT:
            pat=s.encode('utf-16le');i=main.find(pat)
            while i>=0:
                o=i+0x40160000-pool
                if o in offs:found.setdefault(s,offs.index(o))
                i=main.find(pat,i+1)
        TEXT_IDS.update(found);print('TEXT_IDS',json.dumps(found),flush=True);return TEXT_IDS
    return {}
def direction(menu,target):
    selected,count=menu.get('selected',0),menu.get('count',0)
    if count<=0 or not 0<=target<count:return None
    if selected==target:return 'ok'
    return 'down' if (target-selected)%count<=(selected-target)%count else 'up'
def menu_state(g):
    rd=lambda a,n:int.from_bytes(g.read_bytes(a,n),'little')
    valid=lambda a,n:0xa00000000<=a<=0xa40000000-n
    stack=rd(0xa032b5160,8)
    if not valid(stack,24):return {'page':0}
    stride,count,data=rd(stack,4),rd(stack+8,4),rd(stack+16,8)
    if not 8<=stride<=256 or not 1<=count<=64 or not valid(data,count*stride):return {'page':0}
    page=rd(data+(count-1)*stride,8)
    if not valid(page,96):return {'page':0}
    identity=rd(page,4);descriptor=rd(page+80,8)
    result=dict(page=hex(page),page_id=identity>>16,depth=count,selected=rd(page+50,2)+rd(page+88,2))
    if valid(descriptor,64):
        rows=rd(descriptor+16,8);size=rd(descriptor+32,2);index=result['selected']
        result['count']=size
        if identity>>16==25 and result['selected']>0:result['selected']-=1
        if 0<size<=256 and (valid(rows,size*44) or 0x40160000<=rows<=0x45000000-size*44):
            data=g.read_bytes(rows,size*44)
            result['rows']=[dict(index=i,text_id=int.from_bytes(data[i*44:i*44+4],'little'),row_id=int.from_bytes(data[i*44+24:i*44+28],'little')) for i in range(size)]
            if index<size:result.update({k:v for k,v in result['rows'][index].items() if k!='index'})
        header=rd(descriptor+8,8)
        if 0x40160000<=header<0x45000000 or valid(header,72):result['title_id']=rd(header+68,4)
    return result
STATUS='magic state load head used bad skipped injected inject_failed applied apply_failed commits commit_status sel0 sel1 other limit next_limit'.split()
def live_diag(g):
    rd=lambda a,n=8:int.from_bytes(g.read_bytes(a,n),'little')
    root=rd(0xa032b50c8+16);core=rd(root+663*96+80);reg=rd(core+2528);ui=rd(reg+8);eng=rd(ui+8)
    out=dict(engine=hex(eng),phase=rd(eng+1620,4),copied=rd(eng+1632),irq_seq=rd(eng+1688),poll=rd(eng+1752))
    import livediag_fields as F
    out.update(F.decode(g.read_bytes(eng+F.DIAG_OFFSET,F.DIAG_SIZE_V2)));return out
def slot_state(g):
    rd=lambda a,n=4:int.from_bytes(g.read_bytes(a,n),'little')
    out=dict(params=g.read_bytes(0xa116c0000,16).hex(),
             records={f'b{b}c{s}':rd(0xa1166b000+(b*64+s-1)*0x400) for b in (0,1) for s in (1,9,10,11,12,40,64,33)})
    try:
        root=rd(0xa032b50c8+16,8)
        if root:
            st=rd(root+663*96+80,8);out['fx_descriptor']=hex(st)
            if 0xa00000000<=st<0xa40000000 and st!=0xa028ca040:
                out.update(fx_magic=hex(rd(st+64)),fx_installed=rd(st+68),fx_activation=rd(st+72),fx_phase=rd(st+76),fx_error=rd(st+80))
    except Exception as e:out['fx_error_read']=repr(e)
    try:
        from z8_slots_layout import Layout
        st=struct.unpack('<18I',g.read_bytes(Layout(64).address('status'),72));out['status']=dict(zip(STATUS,st))
    except Exception as e:out['status_error']=repr(e)
    return out
CONTACTS=[];SEEK=dict(targets=[],ti=0)
def adaptive(g,label,info):
    item=next((x for x in CONTACTS if label.endswith('-'+x['label'])),None)
    if not item or not item['label'].startswith('seek-') or item['label'].endswith('-release'):return
    menu=info['menu'];key=None;page=menu.get('page_id')
    if SEEK['ti']<len(SEEK['targets']) and page and menu.get('count'):
        kind,val=SEEK['targets'][SEEK['ti']].split(':');val=int(val,0);target=None
        if kind=='row':
            hit=[r['index'] for r in menu.get('rows',[]) if r['row_id']==val]
            target=hit[0] if hit else None
            if target is None:key='down'
        elif page!=SEEK.get('page'):target=val
        if target is not None:
            key=direction(menu,target)
            if key=='ok':key='right';SEEK['ti']+=1;SEEK['page']=page
    if key:body,expected=probe.navigation_contact(key)
    else:body,expected=probe.key_body(),probe.expected_keys()
    item['body'],item['expected']=body,expected
    info['adaptive_key']=key or 'idle';info['seek_index']=SEEK['ti']
class Guest(probe.PreparedGuest):
    def __init__(self,*args,**kw):
        super().__init__(*args,**kw)
        self.command[self.command.index('-kernel')+1]=str(A.main.resolve())
        at=self.command.index('-icount')+1
        self.command[at]=self.command[at].replace('sleep=on','sleep=off')
        if A.points:
            opts=','.join('point='+x for x in A.points.split(','))
            self.command+=['-plugin',f'{HERE}/pc_observer.so,out={self.out}/pc-observer.dat,{opts}']
        if A.okcov:self.command+=['-plugin',f'{HERE}/okcov.so,out={self.out}/okcov.log,{A.okcov}']
        if A.irqmap:self.command+=['-plugin',f'{PULL}/irqmap.so,out={self.out}/irqmap.log,{A.irqmap}']
        if A.mmiolog:self.command+=['-plugin',f'{PULL}/mmiolog.so,out={self.out}/mmiolog.log,{A.mmiolog}']
        self.command+=A.qemu_arg
        if A.powercut:self.command+=['-plugin',f'{PULL}/powercut.so,out={self.out}/powercut.log,{A.powercut}']
        if A.memwatch:self.command+=['-plugin',f'{HERE}/memwatch.so,out={self.out}/memwatch.log,{A.memwatch}']
        if A.garbage:self.command+=['-device',f'loader,file={A.garbage.resolve()},addr=0xa1162b000,force-raw=on']
        self.data['imenu']=dict(plan=A.plan,main=str(A.main),main_sha256=hashlib.sha256(A.main.read_bytes()).hexdigest(),
            unlock=A.unlock,garbage=str(A.garbage) if A.garbage else None,flashable=False)
        print('EXECUTED',json.dumps(self.data['imenu']),flush=True)
    def __enter__(self):
        g=super().__enter__();original=g.qmp.command
        def watchdog():
            deadline=time.monotonic()+A.seconds+30
            while g.proc.poll() is None:
                if (g.out/'stop.request').exists() or time.monotonic()>deadline:g.proc.terminate();return
                time.sleep(1)
        threading.Thread(target=watchdog,daemon=True).start()
        def command(name,**kw):
            result=original(name,**kw)
            if name=='screendump' and 'cardpull' in Path(kw['filename']).stem and not Path(kw['filename']).stem.endswith('-release'):
                (None if os.environ.get('NOSWAP') else original('qom-set',path='/machine',property='cfexpress-card',value=False));print('CARDSWAP pulled',flush=True)
            if name=='screendump' and 'cardinsert' in Path(kw['filename']).stem and not Path(kw['filename']).stem.endswith('-release'):
                (None if os.environ.get('NOSWAP') else original('qom-set',path='/machine',property='cfexpress-card',value=True));print('CARDSWAP inserted',flush=True)
            if name=='screendump' and A.plan=='cardswap':
                try:print('DBISTATE',Path(kw['filename']).stem,original('qom-get',path='/machine',property='pcie-dbi-state'),flush=True)
                except Exception as e:print('DBISTATE-ERR',repr(e),flush=True)
            if name=='screendump':
                path=Path(kw['filename']);Image.open(path).save(path.with_suffix('.png'));path.unlink()
                info=dict(label=path.stem,router=[hex(int.from_bytes(g.read_bytes(0xa032e7090+8*k,8),'little')) for k in range(6)],menu=menu_state(g),lock=g.read_bytes(LOCK,1)[0],photo_movie=int.from_bytes(g.read_bytes(0xa03bd062c,4),'little'),pcsel=[hex(int.from_bytes(g.read_bytes(a,4),'little')) for a in (0xa02983cf8,0xa02983cfc,0xa03d48384,0xa03d5c0a8,0xa03d5c0ac,0xa116d3410,0xa116d341c)])
                try:info['livediag']=live_diag(g)
                except Exception as e:info['livediag_error']=repr(e)
                try:info['slots']=slot_state(g)
                except Exception as e:info['slots_error']=repr(e)
                try:
                    from imenu_diagnostics import snapshot;info['imenu']=snapshot(g)
                except Exception as e:info['imenu_error']=repr(e)
                try:
                    import re
                    txt=original('human-monitor-command',**{'command-line':'info registers -a'})
                    info['pcs']=re.findall(r'PC=([0-9a-f]+)',txt);info['lrs']=re.findall(r'X30=([0-9a-f]+)',txt)
                except Exception as e:info['pcs_error']=repr(e)
                adaptive(g,path.stem,info)
                if A.unlock and path.stem.startswith('step-') and not ('wake' in path.stem or 'wait' in path.stem) and info['lock']&1:
                    g.qt_command(f'write {LOCK:#x} 0x1 0x00');info['unlock_write']=g.read_bytes(LOCK,1)[0]
                with (g.out/'imenu-checkpoints.jsonl').open('a') as f:f.write(json.dumps(info)+'\n')
                print('CHECKPOINT',json.dumps(info),flush=True)
            return result
        g.qmp.command=command
        return g
probe.PreparedGuest=Guest
class Peer(probe.CapturePeer):
    def __init__(self,*args,**kw):super().__init__(*args,**kw);self.trace_points={}
probe.CapturePeer=Peer
probe.MENU_CONTACTS['ok']=(0x2a,0x8000,0)
probe.MENU_CONTACTS['i']=(0x48,0,0x400)
for n,spec in enumerate(filter(None,A.keys.split(';'))):
    probe.MENU_CONTACTS[f'c{n}']=tuple(int(x,0) for x in spec.split(':'))
def contacts(args):
    global CONTACTS
    rows=[]
    def shutter(label,half=False,full=False,gap=1):
        rows.append(dict(label=label,body=probe.shutter_body(half,full),expected=probe.expected_keys(half,full),gap=int(gap*1e9),half=half,full=full))
    def key(label,k,gap=1.5):
        body,expected=probe.navigation_contact(k)
        rows.append(dict(label=label,body=body,expected=expected,gap=400000000,half=False,full=False))
        rows.append(dict(label=label+'-release',body=probe.key_body(),expected=probe.expected_keys(),gap=int(gap*1e9),half=False,full=False))
    shutter('wake-half',True);shutter('wake-release')
    # Standby timer is 30 s: keep metering on with half presses while the module injects.
    for n in range(5):shutter(f'wait-inject-{n}-half',True,gap=.5);shutter(f'wait-inject-{n}',gap=6)
    if A.plan=='keyprobe':
        key('k00-i','i',4)
        for n in range(len([k for k in probe.MENU_CONTACTS if k.startswith('c') and k[1:].isdigit()])):
            key(f'p{n:02}-c{n}',f'c{n}',3)
        shutter('wait-end',gap=3)
    elif A.plan=='cardswap':
        shutter('settle',gap=A.pull_gap)
        shutter('cardpull',gap=2)
        for n in range(int(A.out_gap//8)):shutter(f'out{n}-half',True,gap=1);shutter(f'out{n}',gap=7)
        shutter('cardinsert',gap=2)
        for n in range(int(A.after_gap//8)):shutter(f'in{n}-half',True,gap=1);shutter(f'in{n}',gap=7)
        key('k00-menu','menu',6);key('k01-menu','menu',6);shutter('wait-end-half',True,gap=1);shutter('wait-end',gap=4)
    elif A.plan=='idle':
        key('k00-i','i',4);key('k01-i-close','i',4)
        shutter('idle',gap=A.idle)
        shutter('wake2-half',True,gap=2);shutter('wake2',gap=4)
        key('k02-i','i',4);key('k03-i-close','i',4);key('k04-menu','menu',6)
        shutter('wake3-half',True,gap=2);shutter('wait-end',gap=4)
    elif A.plan=='imenu':
        enter='c0' if A.keys else 'ok'
        key('k00-i','i',4);key('k01-ok',enter,4)
        for n in range(A.lefts):key(f'k{2+n:02}-left','left',A.gap)
        key('k98-ok',enter,4);shutter('rewake-half',True,gap=.5);shutter('rewake',gap=2);key('k09-i','i',4);key('k10-ok',enter,4)
        shutter('wait-end',gap=3)
    else:
        SEEK['targets']=['row:0x2ad6','idx:3','idx:0']
        key('k00-menu','menu',10);key('k01-left','left');key('k02-right','right')
        for n in range(40):key(f'seek-{n:02}','down',1.5)
        shutter('wait-list',gap=5)
        for n in range(A.lefts if A.lefts!=6 else 0):key(f'list-{n:02}-down','down',1)
    CONTACTS=rows
    return rows,len(rows)
probe.contact_sequence=contacts
recipe=json.loads((SNAP/'bundle.json').read_text())['recipe']
names={s.split('=')[0] for s in probe.load_model_profile()['properties']}
names|={'pcie-link-diagnostic-mask','pcie-dbi-model','pcie-ram-dma-input','pcie-image-ram-dma-input','pcie-intx-model','sensor-frame-tag-model'}
extra=[s for s in recipe['properties'] if s.split('=')[0] not in names and ':' not in s]
extra+=['z8-serial5-config-model=on','engine153-alternate-bank-model=on','z8-jpeg-decode=on']
profile=RUNROOT/(A.name+'-properties.json')
profile.write_text(json.dumps(dict(model='Nikon Z8',main_sha256=hashlib.sha256(STOCK.read_bytes()).hexdigest(),properties=extra),indent=2)+'\n')
args=SimpleNamespace(firmware=STOCK,qemu=A.qemu,raw=BASE/'runs/raw-precapture-20261012/input14.raw',output=OUT,seconds=A.seconds,
    settle=1,half_seconds=2,capture_properties=profile,card=True,nvme_trace=False,gpio=True,rtc_status=True,
    tagged_input=True,capture_selector=True,capture_timing_input=True,hsfc_processing_input=False,menu_actions='',menu_only=True,
    wait_liveview=False,navigation_press_ms=400,navigation_release_ms=500,capture_pixels=False,wait_capture_work=False,
    skip_cseq_repeat=True,native_writer_observer=False)
try:probe.run(args)
finally:
    if OUT.exists():
        for p in OUT.glob('*.ppm'):Image.open(p).save(p.with_suffix('.png'));p.unlink()
        for p in OUT.rglob('*'):
            if p.is_file() and p.suffix.lower() in ('.raw','.bin') and p.stat().st_size>1024*1024 and p.name!='capture-card.raw':p.unlink()
        dat=OUT/'pc-observer.dat'
        if dat.exists():
            b=dat.read_bytes();hdr=struct.unpack_from('<12Q',b,0);n=min(hdr[4],hdr[3]);rows=[]
            for i in range(n):
                seq,ns,pc,x0,x1,x2,x3,lr,cpu,kind=struct.unpack_from('<8Q2I',b,96+i*72);rows.append(dict(seq=seq,ns=ns,pc=hex(pc),x0=hex(x0),x1=hex(x1),x2=hex(x2),x3=hex(x3),lr=hex(lr),cpu=cpu,kind=kind))
            (OUT/'pc-observer.json').write_text(json.dumps(dict(head=hdr[4],rows=rows),indent=0))
        print('Finished',OUT,flush=True)
