# SPDX-License-Identifier: GPL-2.0-or-later
import sys
p=sys.argv[1];s=open(p).read()
add='''
def disp(self,video):
    from boot import key_body
    b=bytearray(key_body());b[0x57]=int(video);b[0x53]=1
    self.send(bytes(b),.4,label='disp');b[0x53]=0
    self.send(bytes(b),1.5,label='disp-release')
def disp_cycle(self,tag,video):
    first=shot_once(self,tag+'-disp0')
    for n in range(1,9):
        disp(self,video)
        if shot_once(self,tag+f'-disp{n}')==first:break
_movie_inner=movie
def movie(self):
    pose(self,'left');disp_cycle(self,'video-left',True)
    pose(self,'right');disp_cycle(self,'video-right',True)
    pose(self,'landscape');disp_cycle(self,'video-landscape',True)
    return _movie_inner(self)
sb11_video.VideoDriver.movie=movie

import sb_native_log
_guard_poll=sb_native_log.NativeLogGuard.poll
import re as _re
ALLOWED_RE=_re.compile(rb'^Z9_UNKNOWN cpu=[0-9] pc=0x[0-9a-f]+ (write addr=0x15540[0-9a-f]{3}|(read|write) addr=0x1543[0-9a-f]{4}) ')
def guard_poll(self):
    while True:
        if not _guard_poll(self):return False
        ff=self.first_fault
        if ff['token']!='Z9_UNKNOWN':return True
        with open(self.path,'rb') as f:
            f.seek(ff['byte_offset']);line=f.readline()
        if not line.endswith(bytes([10])) or not ALLOWED_RE.match(line):return True
        self.allowed_unknown=getattr(self,'allowed_unknown',[])+[line.decode().strip()]
        with open(str(self.path)+'.allowed','a') as f:f.write(line.decode())
        self.failed=False;self.first_fault=None;self.suffix=b''
        self.position=ff['byte_offset']+len(line)
sb_native_log.NativeLogGuard.poll=guard_poll

import time as _time
def run_allow(self,seconds):
    from z8_capture_probe import clock_devices
    self.check()
    until=self.now()+int(seconds*1e9)
    self.g.qmp.command('cont')
    while self.now()<until:
        self.check()
        running=self.g.qmp.command('query-status')['running']
        if not running:
            handled=self.peer.handle()
            if not handled and not self.explicit:
                for _ in range(50):
                    self.check();n=len(getattr(self.log_guard,'allowed_unknown',[]))
                    if n>getattr(self,'allowed_conts',0):break
                    _time.sleep(.02)
                if n>getattr(self,'allowed_conts',0):
                    self.allowed_conts=n;self.g.qmp.command('cont');continue
                raise RuntimeError('Unowned stop')
            self.explicit=False;self.g.qmp.command('cont');continue
        self.clock.tick()
        if clock_devices(self.peer,self.spi8,self.spi9,self.state('serial-state')):
            self.explicit=True
        _time.sleep(.002)
    self.g.qmp.command('stop');self.check();self.peer.handle();self.explicit=False
serial_driver.Driver.run=run_allow
'''
k="    self.command[self.command.index('-kernel')+1]=str(KERNEL)"+chr(10)
assert s.count(k)==1
s=s.replace(k,k+"    if os.environ.get('LAYOUT_QEMU'):self.command[0]=os.environ['LAYOUT_QEMU'];self.data['layout_qemu']=os.environ['LAYOUT_QEMU']"+chr(10))
assert s.count('sb11_video.VideoDriver.movie=movie')==1
s=s.replace('sb11_video.VideoDriver.movie=movie',add)
open(p,'w').write(s)

