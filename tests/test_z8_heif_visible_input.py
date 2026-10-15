# SPDX-License-Identifier: GPL-2.0-or-later
"""QTest external-input copy, not native second-photo recovery evidence."""
import argparse,json,tempfile,hashlib
from pathlib import Path
from test_z8_producer_journal import Lab,PROFILE,SOURCE,next_d980,require

WIDTH,HEIGHT,BITS=128,64,14
STRIDE=224 # primary_d980_description requires align32(128*14/8), not 256.

def configure_input(g):
    c=dict(PROFILE);c.update({4:WIDTH<<16,8:HEIGHT<<16,0x100:STRIDE,0x118:0})
    for i,pa in enumerate((*SOURCE,0)):c[0x120+8*i]=pa&0xffffffff;c[0x124+8*i]=pa>>32
    # Same RAM-producer sequence as configure_d980 in producer_journal:
    # all words -> +60 submit -> +64 latch -> IRQ mask -> START -> timing.
    g.configure(c,0x4000)
    submitted=g.state('primary-d980-state');control=g.state('engine150c-state')
    require(submitted['submits']==1 and submitted['frames']==0 and
            all(submitted['latched'].get(hex(off))==value for off,value in c.items()),'Complete submitted/latched D980 fixture')
    require(control['configured']&control['latched']&0x4000 and not control['active']&0x4000,'150c latch before START')
    epoch=g.state('primary-d980-single-frame-state')['epoch']
    g.write(0x150c020c,0x4000000);g.enable(0x4000)
    require(g.state('primary-d980-single-frame-state')['epoch']==epoch+1,'START creates real input epoch')
    g.periodic()
    return dict(d980=submitted,control=control,epoch_before=epoch)

def visible_region(raw):
    require(len(raw)==STRIDE*HEIGHT,'Complete packed14 frame length')
    return b''.join(raw[y*STRIDE+112:y*STRIDE+224] for y in range(32,48))

def verify_marker(visible,frame,enabled):
    require(len(visible)==16*112,'Complete visible ROI')
    for y in range(16):
        row=int.from_bytes(visible[y*112:(y+1)*112],'little')
        for x in range(64):
            sample=(row>>(x*BITS))&16383
            bit=((frame>>(x//2))&1)^(y>=8)
            expected=(16383*7//8 if bit else 16383//8) if enabled else 0
            require(sample==expected,'Independent packed14 visible frame ID/complement')

def run(args,enabled):
    folder=args.output/('on' if enabled else 'off');folder.mkdir(parents=True,exist_ok=False)
    result=dict(enabled=enabled,firmware_executed=False,passed=False)
    with tempfile.TemporaryDirectory(prefix='z8-tag-copy-') as tmp:
        g=Lab(args.qemu,Path(tmp),False)
        if enabled:g.command[g.command.index('-M')+1]+=',sensor-frame-visible-tag-model=on'
        try:
            g.start();require(g.state('sensor-frame-tag-state')['visible_copy']==enabled,'Default-off property')
            try:g.qmp.command('qom-set',path='/machine',property='sensor-frame-visible-tag-model',value=not enabled)
            except RuntimeError as e:require('immutable' in str(e),'Immutable input fixture')
            else:raise AssertionError('Live fixture changed')
            result['setup']=configure_input(g);images=[];result['frames']=[]
            for frame in (1,2):
                d980=next_d980(g,frame);raw=g.read_ram(SOURCE[frame-1],STRIDE*HEIGHT)
                # Crop-independent sensor ROI: x64..127, y32..47, packed14.
                visible=visible_region(raw);images.append(visible);verify_marker(visible,frame,enabled)
                tag=g.state('sensor-frame-tag-state')
                require(tag['frame_id']==frame and tag['physical']==SOURCE[frame-1] and
                    (tag['width'],tag['height'],tag['stride'],tag['bits'])==(WIDTH,HEIGHT,STRIDE,BITS),'Real tagged input identity')
                require(tag['packed_sha256']==hashlib.sha256(raw).hexdigest(),'Full actual input RAM hash')
                require(d980['pixel_dma_bytes']==frame*STRIDE*HEIGHT and d980['acks']==frame-1,'Two real DMA generations and native W1C')
                result['frames'].append(dict(d980=d980,tag=tag,roi_sha256=hashlib.sha256(visible).hexdigest()))
            require((images[0]!=images[1])==enabled,'Visible copy distinguishes consecutive inputs only when enabled')
            g.write(0x150c0210,0x4000000)
            final=g.state('primary-d980-state');require(final['frames']==final['acks']==2 and final['pending']==0 and not g.irq(0x106),'Final native W1C retires both frames')
            result['final_d980']=final
            require(not g.qmp.command('query-status')['running'],'No firmware executed')
            result.update(passed=True,roi_hashes=[hashlib.sha256(v).hexdigest() for v in images])
        except Exception as e:result['error']=repr(e)
        finally:g.close();(folder/'stderr.txt').write_text(g.text());(folder/'transactions.json').write_text(json.dumps(g.transactions,indent=2))
    (folder/'result.json').write_text(json.dumps(result,indent=2));print(result);return result['passed']
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('qemu','output'):p.add_argument('--'+arg,type=Path,required=True)
    args=p.parse_args();rows=[run(args,v) for v in (False,True)]
    if not all(rows):raise SystemExit(1)
if __name__=='__main__':main()
