# SPDX-License-Identifier: GPL-2.0-or-later
import sys
p=sys.argv[1];s=open(p).read()
add='''
import os as _os, json as _json, time as _t
def interactive(self):
    folder=self.g.out/'layout';folder.mkdir(exist_ok=True)
    cmdf=self.g.out/'cmd';ackf=self.g.out/'ack'
    n=0
    self.deadline=_t.monotonic()+3*3600
    print('INTERACTIVE_READY',flush=True)
    while True:
        self.check()
        if not cmdf.exists():_t.sleep(.3);continue
        line=cmdf.read_text().strip();cmdf.unlink()
        n+=1;parts=line.split();res='ok'
        try:
            self.deadline=max(self.deadline,_t.monotonic()+3600)
            if parts[0]=='done':ackf.write_text('done');return
            elif parts[0]=='key':
                self.menu_count=0;self.phase=f'int-{n:03d}'
                self.menus(parts[1:])
            elif parts[0]=='disp':disp(self,True)
            elif parts[0]=='pose':pose(self,parts[1])
            elif parts[0]=='wait':self.run(float(parts[1]))
            elif parts[0]=='hmp':
                out=self.g.qmp.hmp(line.split(' ',1)[1])
                f=folder/f'hmp{n:03d}.txt';f.write_text(out);ackf.write_text('ok '+str(f));continue
            elif parts[0]=='pmem':
                f=self.g.out/(parts[3]+'.bin')
                self.g.qmp.command('pmemsave',val=int(parts[1],16),size=int(parts[2],16),filename=str(f))
                ackf.write_text('ok '+str(f));continue
            name=f'i{n:03d}'
            shot_once(self,name);res='ok '+str(folder/(name+'.png'))
        except Exception as e:
            res='error '+repr(e)
        ackf.write_text(res)
_movie_inner2=movie
def movie(self):
    pose(self,'left')
    interactive(self)
    return _movie_inner2(self)
sb11_video.VideoDriver.movie=movie
'''
assert s.count("sb_native_log.NativeLogGuard.poll=guard_poll\n")==1
s=s.replace("sb_native_log.NativeLogGuard.poll=guard_poll\n","sb_native_log.NativeLogGuard.poll=guard_poll\n"+add,1)
open(p,'w').write(s)
