# SPDX-License-Identifier: GPL-2.0-or-later
"""Video f04f uses ordinary hold/kick/ISR; M direct/internal retain their ABI."""
import argparse,hashlib,json,tempfile
from pathlib import Path
from make_cmos import generate
from test_z8_video_native_order import PROPS,program,front_program,front_start
from test_z8_video_ring_stop import Lab,require
from test_z8_d980_internal import BASE as MAIN_PROPERTIES,Lab as InternalLab
from test_z8_f04f_stop import setup as internal_setup,complete as internal_complete,atomic_state
from test_z8_producer_journal import Lab as PhotoLab,configure_d980,next_d980,run151,A

MASK=0xf04f
KICK=0x1f87d
CAUSE=0x07e8dc00

def qstate(g,key='engine150c-state'):
    return json.loads(g.qmp.command('qom-get',path='/machine',property=key))

def low_units(g):
    # Same authored parameter prerequisites as the existing internal-stop test,
    # but do not start the internal D980 supplier. Video owns the ff00 job.
    for name,base,bit in (('primary19d-config-state',0x150c0400,1),('primary194-config-state',0x150d4000,0x40)):
        for off in qstate(g,name)['fields']:g.q(f'writel {base+int(off,16):#x} 0')
        g.q(f'writel 0x150c0060 {bit:#x}')
    bank=qstate(g,'primary19bc-config-state')['banks'][0]
    for off in bank['fields']:g.q(f'writel {bank["base"]+int(off,16):#x} 0')
    for bit in (0x10,2,4,8):g.q(f'writel 0x150c0060 {bit:#x}')
    g.q('writel 0x150c0064 0x5f');g.q('writel 0x150c0008 0x5f')

def video(g,value=0xff00):
    controls=front_program(g);low_units(g);program(g)
    g.q('writel 0x15100008 1');g.q('writel 0x15200008 1')
    g.q('writel 0x151c0030 1');g.q('writel 0x15180030 1');front_start(g,controls)
    for off in (0x64,8):g.q(f'writel {0x150c0000+off:#x} {value:#x}')
    if value==0xff00:require(qstate(g)['active']==0xff5f,'Native aggregate/lower units did not latch')
    # Native teardown stops C1 before primary f04f. This is not ISP0 ownership.
    g.write(4,0x100,1)
    require(qstate(g,'z8-video-data-state')['isp_video_owner'],'C1 stop lost ISP0 owner')


def run(binary,output):
    output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(prefix='z8-stop-select-') as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        raw=root/'pixels.raw';raw.write_bytes(generate(64,32))
        names=list(dict.fromkeys([*PROPS,*[n for n in MAIN_PROPERTIES if n!='bootstrap-fixtures'],'z8-video-data-model','z8-video-ring-stop-model']))
        props=tuple(n+'=on' for n in names)+('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000')
        def case(name,body,reject=False,extra=()):
            with Lab(binary,root/name,kernel,accelerator='qtest',properties=props+extra) as g:
                g.observe_irq()
                try:
                    body(g);log=g.log_path.read_text()
                    require(('Z9_UNKNOWN' in log)==reject,'Wrong strict boundary: '+name)
                    if reject:require('Z9_150C_STOP_REJECT' in log and 'video=0' in log,'Missing classifier rejection')
                    cases.append(dict(name=name,passed=True))
                finally:(output/(name+'.log')).write_text(g.log_path.read_text())
        def complete(g):
            video(g);before=qstate(g);g.q('writel 0x150c0218 0x7e8dc00')
            g.q('writel 0x150c000c 0xf04f');s=qstate(g)
            require(s['active']==before['active'] and s['stop_requested']==MASK,'Request drained primary units early')
            require(s['stop_done']==s['stop_pending']==s['direct_stop_pending1']==0,'Request fabricated completion or took direct route')
            require(qstate(g,'z8-primary-f04f-stop-state')['owned_mask']==0,'Video acquired internal f04f owner')
            require('STOP_SELECT video=1 internal=0 direct=0' in g.log_path.read_text(),'No video classification')
            g.q('writel 0x150c0018 0xf04f');g.q('writel 0x150c0034 0x1f87d');s=qstate(g)
            require(s['stop_requested']==0 and s['stop_done']==MASK and s['stop_pending']==CAUSE,'O stop kick/cause mapping differs')
            require(s['active']==0xf10 and s['direct_stop_pending1']==0,'Normal kick took a direct cause route')
            g.q('readl 0x150c0220');require(g.irqs.get(0x106-32,False),'No group2 IRQ after kick')
            # ISR40c1f8b8 geometry causes, independent W1C/reset/ACK from lower units.
            for mask,cause in ((0xc000,0x06000000),(0x304f,0x01e8dc00)):
                g.q(f'writel 0x150c021c {cause:#x}')
                g.q(f'writel 0x150c0000 {mask:#x}');g.q(f'writel 0x150c0004 {mask:#x}')
                hold=int(g.q('readl 0x150c0018'),0);g.q(f'writel 0x150c0018 {hold&~mask:#x}')
                g.q(f'writel 0x150c0014 {mask:#x}')
            s=qstate(g);require(s['stop_done']==s['stop_pending']==0 and not(s['latched']&MASK),'ISR retirement incomplete')
            g.q('readl 0x150c0220');require(not g.irqs.get(0x106-32,False),'Group2 IRQ remained after ACK')
        case('video-ordinary-stop-and-split-isr',complete)
        # Merely enabling the internal capability must not take ownership.
        case('video-with-internal-capabilities',complete,extra=('z8-d980-internal-start-model=on','z8-primary-f04f-stop-model=on'))
        def assembled_legacy(g):
            video(g,0x4000)
            for off in (0x64,8):g.q(f'writel {0x150c0000+off:#x} 0xbf00')
            require(qstate(g)['active']==0xff5f,'Legacy units did not assemble the comparison mask')
        for name,prepare,value in (
            ('wrong-stop-set',lambda g:video(g),0xe04f),
            ('legacy4000-is-not-ff00-job',assembled_legacy,MASK),
            ('drained-isp-owner',lambda g:(video(g),g.q('writel 0x15100010 1')),MASK)):
            def bad(g,prepare=prepare,value=value):
                prepare(g);before=qstate(g);g.q(f'writel 0x150c000c {value:#x}');require(qstate(g)==before,'Rejected stop mutated primary state')
            case(name,bad,reject=True)
        def repeated(g):
            video(g);g.q('writel 0x150c000c 0xf04f');before=qstate(g);g.q('writel 0x150c000c 0xf04f')
            require(qstate(g)==before,'Repeated stop changed transaction')
        case('repeated-video-request',repeated,reject=True)
        def early_kick(g):
            video(g);before=qstate(g);g.q('writel 0x150c0034 0x1f87d');require(qstate(g)==before,'Kick before request changed state')
        case('video-kick-before-request',early_kick,reject=True)
    # M finite-photo D980 direct stop must stay in group1, even with video flags.
    with tempfile.TemporaryDirectory(prefix='z8-photo-stop-') as tmp:
        g=PhotoLab(binary,Path(tmp),False)
        g.command[g.command.index('-M')+1]+=',z8-video-interface-model=on,z8-video-data-model=on'
        try:
            g.start();configure_d980(g);next_d980(g,1);run151(g,A,0,True)
            g.write(0x150c000c,0x4000);s=g.state('engine150c-state')
            require(s['stop_requested']==s['stop_done']==0 and s['direct_stop_done']&0x4000,'Photo stop became ordinary video stop')
            require(s['direct_stop_pending1']&0x04000000,'Photo D980 direct cause missing')
            g.write(0x150c0210,0x04000000);require(not(g.state('engine150c-state')['direct_stop_done']&0x4000),'Photo direct ACK failed')
            require('Z9_UNKNOWN' not in g.text(),'Photo stop rejected');cases.append(dict(name='finite-photo-direct4000',passed=True))
        finally:g.close();(output/'finite-photo-direct4000.log').write_text(g.text())
    # Existing actual MMIO internal fixture is retained, including its no-pixel
    # and hold/kick/independent stop10 checks. No direct assignment of state.
    for enabled in (False,True):
        with tempfile.TemporaryDirectory(prefix='z8-internal-stop-') as tmp:
            g=InternalLab(binary,Path(tmp))
            g.command[g.command.index('-M')+1]+=',z8-video-interface-model=on,z8-video-data-model=on'
            if enabled:g.command[g.command.index('-M')+1]+=',z8-primary-f04f-stop-model=on'
            try:
                g.start()
                if enabled:
                    internal_complete(g)
                    require(g.state('z8-primary-f04f-stop-state')['requests']==1,'Internal stop lost its transaction owner')
                else:
                    internal_setup(g);before=atomic_state(g);g.write(0x150c000c,MASK)
                    require(atomic_state(g)==before,'Disabled internal f04f became video stop')
                require(('Z9_UNKNOWN' in g.text())== (not enabled),'Internal strict boundary changed')
                cases.append(dict(name='internal-f04f-'+str(enabled),passed=True))
            finally:g.close();(output/('internal-f04f-'+str(enabled)+'.log')).write_text(g.text())
    result=dict(passed=True,cases=cases,qemu_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),firmware_execution=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
