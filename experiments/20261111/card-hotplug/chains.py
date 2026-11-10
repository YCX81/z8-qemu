# SPDX-License-Identifier: GPL-2.0-or-later
"""Battery-pull chains on the boot-guard build. Emulation only."""
import json,os,subprocess,sys,time,fcntl,random,threading
from pathlib import Path
sys.path.insert(0,'/path/to/user/fxfix/src/port-z8')
import card_read
PY='/path/to/user/.local/share/nikon-z8-qemu/venv/bin/python'
RUNS=Path('/path/to/user/.local/share/nikon-z8-qemu/imenu-c64/runs')
MAIN=sys.argv[2] if len(sys.argv)>2 else '/path/to/user/fxfix/qemu/bg8.main'
CARD='/path/to/user/fxfix/qemu/cardD'
TICK=0x401d88c4;WRITE=0x402dd78c;CLOSE=0x402dd558
ARM_CREATE=0x401d819c;ARM_AFTER=0x401d81a0;INSPECT=0x401d78d8
CFG_CREATE=0x401d7f28;CLR_REMOVE=0x401d8444;CLR_AFTER=0x401d8448
def pc(**k):return ','.join(f'{a}={hex(b) if a in ("pc","gate","cmdpc") else b}' for a,b in k.items())
# chain: list of (stage, powercut-args, seconds)
CUT=dict(
 preinspect=pc(pc=INSPECT,n=1),
 open=pc(gate=ARM_CREATE,pc=WRITE,n=1),
 close=pc(gate=ARM_CREATE,pc=CLOSE,n=1),
 created=pc(pc=ARM_AFTER,n=1),
 created50ms=pc(pc=ARM_AFTER,n=1,delay_us=50000),
 armed2s=pc(pc=ARM_AFTER,n=1,delay_us=2000000),
 active30s=pc(pc=ARM_AFTER,n=1,delay_us=30000000),
 cfgwrite=pc(cmd=3,cmdpc=TICK,cmdn=400,gate=CFG_CREATE,pc=WRITE,n=1),
 cfgclose=pc(cmd=3,cmdpc=TICK,cmdn=400,gate=CFG_CREATE,pc=CLOSE,n=1),
)
LOCK='/path/to/user/fxfix/pull/start.lock'
def launch_slot():
    while True:
        with open(LOCK,'a+') as f:
            fcntl.flock(f,fcntl.LOCK_EX);f.seek(0);t=float(f.read() or 0)
            if time.time()-t>=100:
                f.seek(0);f.truncate();f.write(str(time.time()));return
        time.sleep(10)
def boot(name,cut,card_from=None,seconds=420):
    for attempt in range(20):
        launch_slot()
        subprocess.run(['rm','-rf',str(RUNS/name),str(RUNS/(name+'-properties.json'))])
        cmd=[PY,'-u','-B','/path/to/user/fxfix/pull/run_pull.py','--name',name,'--plan','idle','--main',MAIN,
             '--fenestra',CARD,'--seconds',str(seconds),'--idle','40']
        if card_from:cmd+=['--card-from',str(card_from)]
        cmd+=['--powercut',(cut+',' if cut else '')+'dumppc=0x401d88c4,dumpn=200']
        with open(RUNS/(name+'.log'),'w') as log:rc=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT).returncode
        txt=(RUNS/(name+'.log')).read_text(errors='replace')
        if 'started <' in txt:time.sleep(random.randint(20,60));continue
        break
    plog=RUNS/name/'powercut.log'
    card=RUNS/name/'capture-card.raw'
    files=[]
    if card.exists():
        for e in card_read.read(str(card)):
            b=e.pop('bytes',None);e['hex']=b.hex() if b is not None else None;files.append(e)
    return dict(name=name,rc=rc,powercut=plog.read_text() if plog.exists() else None,card=str(card),files=files)
def chain(key):
    out=dict(key=key,stages=[])
    r=boot(f'pc-{key}-1',CUT[key]);out['stages'].append(r)
    r2=boot(f'pc-{key}-2',None,r['card']);out['stages'].append(r2)
    last=[l for l in (r2['powercut'] or '').splitlines() if l.startswith(('TICK','EXIT'))]
    if last and 'st_phase=2 ' in last[-1]:   # still ABNORMAL at exit -> try Clear
        r3=boot(f'pc-{key}-3clr',pc(cmd=4,cmdpc=TICK,cmdn=300),r2['card']);out['stages'].append(r3)
        if key=='active30s':
            r4=boot(f'pc-{key}-4clrcut',pc(cmd=4,cmdpc=TICK,cmdn=300,gate=CLR_REMOVE,pc=CLR_AFTER,n=1),r2['card']);out['stages'].append(r4)
            r5=boot(f'pc-{key}-5',None,r4['card']);out['stages'].append(r5)
    Path(f'/path/to/user/fxfix/pull/result-{key}.json').write_text(json.dumps(out,indent=1))
    print('DONE',key,flush=True)
keys=sys.argv[1].split(',')
ts=[threading.Thread(target=chain,args=(k,)) for k in keys]
for t in ts:t.start();time.sleep(5)
for t in ts:t.join()
print('ALLDONE')
