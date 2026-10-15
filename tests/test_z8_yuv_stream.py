# SPDX-License-Identifier: GPL-2.0-or-later
"""Fourth native output must own actual packet RAM, beyond the three previews."""
import argparse,json,struct,tempfile,zlib,time
from pathlib import Path
from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_ring_stop import Lab,require
from test_z8_video_stream import video_setup
from test_z8_video_data import configure,PACKET,CAPACITY,OPTION

def recording_setup(m):
    def w(a,v):m.q(f'writel {a:#x} {v:#x}')
    w(0x15421104,0xa02)
    for off,v in ((0xc,0),(0x10,1),(0x38,64),(0x3c,64)):w(0x15422300+off,v)
    w(0x1542332c,64);w(0x15423330,32)
    for plane in range(2):
        ch=0x15649000+plane*0x400
        w(ch,1);w(ch+0xc,3)
        for bank in range(2):
            b=ch+0x200+bank*0x100
            for off,v in ((0x18,2048>>plane),(0x1c,64),(0x20,64),(0x28,4096),(0x2c,128),(0x30,128),(0x40,15),(0x44,(32>>plane)<<16|64)):
                w(b+off,v)
            for row in range(4):
                logical=0x200000000+plane*0x18000000+(bank*4+row)*0x3000000
                physical=0x840400000+(bank*4+row)*0x10000+plane*0x2000
                m.q(f'writeq {b+0x80+row*8:#x} {logical:#x}')
                m.q(f'writeq {b+0xa0+row*8:#x} {physical:#x}')
                off=0x240+bank*0x200+row*0x80+plane*8
                m.write(off,logical&0xffffffff);m.write(off+4,logical>>32)

def encode_stored_frame(m,output):
    saved_packet=bytes.fromhex(m.q('read 0x840400000 0x1000')[2:])
    def w(off,v):m.q(f'writel {0x16010000+off:#x} {v:#x}')
    def read(off):return int(m.q(f'readl {0x16010000+off:#x}'),0)
    def command(op,core=False):
        w(0x100,op);w(0x70,1);w(0x6c if core else 0x38,1)
        deadline=time.monotonic()+10
        while read(0x70):
            m.qmp.command('cont');m.q('clock_step 1000000');m.qmp.command('stop');time.sleep(.002)
            require(time.monotonic()<deadline,'Codec worker timeout')
        require(read(0x108)==1,'Actual command success')
    def ack():w(0x34,0xffffffff)
    def info():w(0x110,1<<16)
    def get_result():
        ack();w(0x104,2);info();command(0x4000);amount=read(0x14c);bus=read(0x114)
        require(0<amount<0x100000,'Actual bounded coded output')
        return bytes.fromhex(m.q(f'read {0x800000000+bus:#x} {amount:#x}')[2:])
    m.q('memset 0x840e00000 0x1000 0xa5')
    w(0x24,8);w(0x20,0x1fec0)
    w(0x110,0x40e00000);w(0x114,4096);w(0x11c,0x40e10000);w(0x120,4096);command(1,True)
    info();w(0x114,0x40e20000);w(0x118,0x10000);w(0x124,0);command(8);ack()
    info();w(0x104,0);w(0x11c,32<<16|64);w(0x124,8<<14)
    for off in (0x134,0x138,0x170,0x174,0x178):w(off,0)
    w(0x150,30);command(0x200);ack();info();w(0x104,2);command(0x4000);command(0x80)
    def output_params():info();w(0x118,0x40f00000);w(0x11c,0x100000);w(0x128,0)
    output_params();w(0x164,0x3c);command(0x100);stream=get_result()
    for p in range(2):
        base=0x15661000+p*0x400
        for off,v in ((0,1),(4,1),(0x31c,64),(0x320,64),(0x328,4096)):
            m.q(f'writel {base+off:#x} {v:#x}')
        m.q(f'writeq {base+0x380:#x} {0x20c000000+p*0x18000000:#x}')
        m.q(f'writeq {base+0x3a0:#x} {0x840400000+p*0x2000:#x}')
    output_params();w(0x144,0);w(0x148,0xc000000);w(0x14c,0x24000000);w(0x150,0xffffffff);w(0x154,64<<16|64);w(0x158,0x3e2);w(0x164,3)
    original=m.q('readl 0x840400028');m.q('writel 0x840400028 0x11223344')
    w(0x100,0x100);w(0x70,1);w(0x38,1)
    state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))
    require(state['owned']==1 and state['reads']==0 and state['rejects']==1,'Corrupt actual packet cannot consume the source owner')
    require('Z9_UNKNOWN' in m.log_path.read_text(),'Bad source must be rejected visibly')
    # Authored QTest only: restore the original packet CRC and resume a
    # diagnostic guest with no Nikon firmware execution. Keep the failure log.
    m.q(f'writel 0x840400028 {original}');m.qmp.command('cont');m.qmp.command('stop')
    restored=bytes.fromhex(m.q('read 0x840400000 0x1000')[2:])
    (output/'restored-packet.bin').write_bytes(restored)
    require(restored==saved_packet,'QTest must restore the actual original packet byte-for-byte')
    try:command(0x100)
    except Exception:
        (output/'yuv-failure.json').write_text(json.dumps(json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state')),indent=2))
        (output/'codec-failure.json').write_text(json.dumps(json.loads(m.qmp.command('qom-get',path='/machine',property='wave5-state')),indent=2))
        (output/'failure.log').write_text(m.log_path.read_text())
        raise
    stream+=get_result()
    state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))
    require(state['reads']==1 and state['owned']==0,'One successful RAM snapshot consumes exactly one source')
    codec=json.loads(m.qmp.command('qom-get',path='/machine',property='wave5-state'))
    require(codec['instances'][0]['pictures']==1 and codec['input_bytes']==3072,'Actual decoded packet becomes one picture')
    (output/'hevc.bin').write_bytes(stream);(output/'expected.yuv').write_bytes(bytes(2048)+bytes([128])*1024)
    return dict(yuv=state,codec=codec,independent_decode_verified=False)

def run(binary,output,rebind=False,dma=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096));raw=root/'pixels.raw';raw.write_bytes(generate(64,32))
        props=tuple(n+'=on' for n in ('image-bank-model','engine150c-control-model','engine150c-stop-model','config150d-model','engine150d-control-model','statistics-model','engine1543-control-model','engine151-control-model','raw-pipeline-model','z8-video-interface-model','event1508-front-model','event1508-timing-model','event1508-channel-model','event1508-aux-model','event1508-global1-model',OPTION,'z8-yuv-data-model'))
        props+=('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000','wave5-vpu-model=on','wave5-host-codec-diagnostic=on','wave5-lossless=on')
        if dma:props+=('z8-yuv-dma-model=on',)
        with Lab(binary,root/'stream',kernel,accelerator='qtest',properties=props) as m:
            m.qmp.deadline=time.monotonic()+60
            def w(a,v):m.q(f'writel {a:#x} {v:#x}')
            def advance():m.qmp.command('cont');m.q('clock_step 70000000');m.qmp.command('stop')
            configure(m);w(0x15080010,1);w(0x15080028,1);advance();w(0x15080048,0x2000)
            packet=bytearray(bytes.fromhex(m.q(f'read {PACKET:#x} {CAPACITY:#x}')[2:]))
            packed=bytes(4096);encoded=zlib.compress(packed)
            struct.pack_into('<II',packet,48,len(encoded),zlib.crc32(packed));packet[64:64+len(encoded)]=encoded
            m.q(f'write {PACKET:#x} {CAPACITY:#x} 0x{packet.hex()}')
            w(0x152c001c,3);w(0x15300404,0);advance();w(0x1530001c,3)
            video_setup(m);recording_setup(m);advance()
            generations=[]
            for plane in range(2):
                packet=bytes.fromhex(m.q(f'read {0x840400000+plane*0x2000:#x} 0x1000')[2:])
                require(packet[:8]==b'WLYV0001','Fourth output needs actual bounded packet RAM')
                width,height,stride,p,epoch,n,amount,crc,reserved=struct.unpack_from('<IIIIQIIII',packet,8)
                pixels=zlib.decompress(packet[48:48+amount])
                require((width,height,stride,p,n,reserved)==(64,32>>plane,64,plane,2048>>plane,0),'Plane contract')
                require(len(pixels)==n and zlib.crc32(pixels)==crc,'Actual stored packet integrity')
                require(pixels==bytes([0 if plane==0 else 128])*n,'Uses actual decoded black packet, not host colored input')
                generations.append(epoch)
            require(generations[0]==generations[1]>0,'Both planes belong to one admitted RAW generation')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))
            require(state['frames']==1 and state['owned']==1,'Single source frame has explicit output ownership')
            require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected register')
            if dma:
                # Preserve actual producer header/frame identity, but relocate
                # a deliberately nonuniform valid RAM packet. DMA reads RAM,
                # including retained data outside the encoder-owner journal.
                packet=bytearray(bytes.fromhex(m.q('read 0x840400000 0x1000')[2:]))
                pixels=bytes((x*7+y*11)&255 for y in range(32) for x in range(64));encoded_pixels=zlib.compress(pixels)
                struct.pack_into('<II',packet,36,len(encoded_pixels),zlib.crc32(pixels));packet[48:48+len(encoded_pixels)]=encoded_pixels
                m.q(f'write 0x840600000 0x1000 0x{packet.hex()}')
                for off,value in ((0x1000,1),(0x1004,1),(0x1218,2048),(0x121c,64),(0x1220,64),(0x1228,4096),(0x1240,1)):
                    w(0x15660000+off,value)
                m.q('writeq 0x15661280 0x200000000');m.q('writeq 0x156612a0 0x840600000')
                m.q('memset 0x840700000 0xa0 0x55')
                for off,value in ((4,1),(0x14,0),(0x1c,64),(0x20,40),(0x24,32),(0x28,4),(0x30,136),(0x34,2),(0x38,0x40700000),(0x3c,8)):
                    w(0x15680000+off,value)
                w(0x15680010,1)
                expected=b''.join(pixels[y*64+8:y*64+40]+bytes([85])*8 for y in range(2,6))
                require(m.q('read 0x840700000 0xa0')=='0x'+expected.hex(),'DMA copies requested YUV rectangle and preserves destination padding')
                require(int(m.q('readl 0x15680008'),0)==1,'Actual copy owns DMA completion')
                copied=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))
                require(copied['dma_jobs']==1 and copied['dma_bytes']==128 and copied['owned']==1 and copied['reads']==0,'DMA does not consume encoder ownership')
                w(0x15680010,1)
                require(int(m.q('readl 0x15680008'),0)==1,'Unacknowledged DMA result is preserved')
                require(json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))['dma_jobs']==1,'No transfer may replace an unretired completion')
                w(0x15680008,1);m.qmp.command('cont');m.qmp.command('stop');w(0x15680010,1)
                cached=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))
                require(cached['dma_jobs']==2 and cached['dma_cache_hits']==1 and cached['dma_last_epoch']==1,'Cache reuse requires identical actual packet bytes')
                w(0x15680008,1);m.q('memset 0x840700000 0xa0 0x55');m.q('writel 0x840600028 0x11223344');w(0x15680010,1)
                require(int(m.q('readl 0x15680008'),0)==0,'Corruption cannot create DMA completion')
                require(m.q('read 0x840700000 0xa0')=='0x'+(bytes([85])*160).hex(),'Corruption cannot partially overwrite the destination')
                require('Z9_UNKNOWN' in m.log_path.read_text(),'Rejected DMA remains visible')
                encoded=dict(dma_actual_copy=True,corruption_rejected=True)
            elif rebind:
                saved=m.q('read 0x840400000 0x1000')
                m.q('writeq 0x156492a0 0x840400000')
                same=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))
                require(same['owned']==1 and not same['cancels'],'Same descriptor cannot silently release a packet')
                m.q('writeq 0x156492a0 0x840600000')
                cancelled=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))
                require(cancelled['owned']==1 and cancelled['cancels']==0 and cancelled['reads']==0,'Alias remap must preserve completed physical packet ownership')
                require(m.q('read 0x840400000 0x1000')==saved,'Remapping cannot replace packet contents')
                m.q('writel 0x15648010 1')
                cancelled=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-yuv-state'))
                require(cancelled['owned']==0 and cancelled['cancels']==1 and cancelled['reads']==0,'Explicit producer reset cancels without a codec result')
                source=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-data-state'))
                require(source['primary_pending']==24,'Remapping cannot forge upstream acknowledgements')
                encoded=dict(remap=cancelled)
            else:encoded=encode_stored_frame(m,output)
            result=dict(passed=True,state=state,encoded=encoded,qemu_sha256=sha256(binary),camera_recording=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--rebind-test',action='store_true')
    p.add_argument('--dma-test',action='store_true')
    a=p.parse_args();run(a.qemu,a.output,a.rebind_test,a.dma_test)
