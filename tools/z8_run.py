# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the original Z8 C3.11 with explicit laboratory peripherals on localhost."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import queue
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from PIL import Image
from boot import key_body
from service_z9_mcu import Peer
from service_serial8 import WritePeer
from validate_run import MenuAcceptance,verify_pixels
from z8_boot import boot,load_bindings
from z8_guest import Guest,ROOT
from z8_resources import attach
from z9_global1_boundary_input import Peer as BoundaryPeer


class Session:
    def __init__(self):
        self.commands=queue.Queue(maxsize=16)
        self.status=dict(phase='Starting',liveview_verified=False,menu_verified=False)
        self.png=b''
        self.stop=threading.Event()

    def progress(self,message):
        self.status=dict(self.status,phase=message)
        print(message,flush=True)


def state(g,name):
    return json.loads(g.qmp.command('qom-get',path='/machine',property=name))


def liveview_ready(raw,lcd):
    return bool(raw['frames']>=8 and lcd.get('active') and lcd.get('video_frames',0)>=8
                and lcd.get('last_frame_has_video'))


def menu_profile(guest):
    profile=json.loads((ROOT/'profiles/z8-c311-acceptance.json').read_text())
    if (profile.get('model')!='Nikon Z8' or profile.get('version')!='C3.11'
            or profile.get('main_sha256')!=guest.data['firmware_sha256']):
        raise ValueError('Menu acceptance requires the pinned Z8 C3.11 firmware')
    return profile


def worker(args,session):
    try:
        properties=json.loads((ROOT/'tools/model-properties.json').read_text())['properties']
        properties.append('z8-sd-empty-fixture=on')
        guest=Guest(args.firmware,args.qemu,args.raw,args.output,properties=properties)
        guest.cancel_event=session.stop
        with guest as g:
            boot(g,session.progress)
            peer=Peer(g,bindings=load_bindings(g),limit_frames=1024,config_reply=True,all_mode_replies=True)
            peer.record['next_rx_scope']='after_original_Z8_RX_state_clear_and_GPIO_ack'
            clock=BoundaryPeer(g,33,auxiliary_masked=True)
            spi8=WritePeer(g)
            spi9=WritePeer(g,channel=9,wire_bits=9,allow_dummy_rx=True)
            profile=menu_profile(g)
            acceptance=None
            if args.menu_test:
                acceptance=MenuAcceptance(g,peer,session.progress,virtual_clock=True,
                                          menu_rgb_sha256=profile['menu_rgb_sha256'])
                acceptance.record['profile']=profile
            deadline=time.monotonic()+args.seconds if args.seconds else float('inf')
            capture_at=0
            pending_keys=[]
            half=True
            explicit_stop=False
            g.qmp.command('cont')
            while not session.stop.is_set() and time.monotonic()<deadline:
                if 'Z9_UNKNOWN' in g.log():
                    raise RuntimeError('Unmodeled hardware access; see local stderr.log')
                if not g.qmp.command('query-status')['running']:
                    handled=peer.handle()
                    if not handled and not explicit_stop:
                        raise RuntimeError('Guest stopped outside an observed MCU boundary')
                    explicit_stop=False
                    g.qmp.command('cont')
                    continue
                clock.tick()
                now=time.monotonic()
                if now>=capture_at:
                    g.qmp.command('stop')
                    peer.handle()
                    raw,lcd=state(g,'raw-pipeline-state'),state(g,'engine1548-scanout-state')
                    target=g.out/'display.ppm'
                    g.qmp.command('screendump',filename=str(target))
                    with Image.open(target) as frame:
                        buffer=io.BytesIO();frame.save(buffer,format='PNG')
                        session.png=buffer.getvalue()
                        (g.out/'display.png').write_bytes(session.png)
                        digest=hashlib.sha256(frame.convert('RGB').tobytes()).hexdigest()
                        if digest==profile['menu_rgb_sha256']:
                            if not g.data['menu_verified']:(g.out/'z8-menu.png').write_bytes(session.png)
                            g.data['menu_verified']=True
                            g.data['menu_rgb_sha256']=digest
                    if liveview_ready(raw,lcd) and not g.data['liveview_verified']:
                        verify_pixels(g,'z8-liveview',lcd)
                        g.data['liveview_verified']=True
                        session.progress('Original Z8 video passed the independent pixel check')
                    if acceptance:
                        acceptance.tick(now,raw,lcd,target)
                        g.data['menu_verified'] = g.data['menu_verified'] or any(
                            row['label'].startswith('menu-') for row in acceptance.record['stages'])
                    session.status=dict(phase='Running' if liveview_ready(raw,lcd) else 'Waiting for original video',
                        liveview_verified=g.data['liveview_verified'],menu_verified=g.data['menu_verified'],
                        raw_frames=raw['frames'],lcd_frames=lcd['frames'],lcd_video_frames=lcd.get('video_frames',0),
                        lcd_active=lcd['active'],half_held=(half if acceptance is None else acceptance.stage=='video'))
                    g.data['latest_pipeline']=dict(raw=raw,lcd=lcd)
                    capture_at=now+.5
                    g.qmp.command('cont')
                    if acceptance and acceptance.done:break
                virtual=state(g,'runtime-clock-state')['virtual_ns']/1e9
                command=None
                if pending_keys and virtual>=pending_keys[0][0]:
                    command=pending_keys.pop(0)[1]
                elif not session.commands.empty():
                    command=session.commands.get_nowait()
                if command:
                    g.qmp.command('stop');peer.handle()
                    if command=='menu':
                        half=False
                        peer.enqueue_external(key_body(),2)
                        pending_keys=sorted(pending_keys+[(virtual+2,'menu-down'),(virtual+2.4,'menu-up')])
                    else:
                        if command.startswith('half-'):half=command=='half-down'
                        peer.enqueue_external(key_body(menu=command=='menu-down',half=half),2)
                    g.data.setdefault('keys',[]).append(dict(command=command,virtual_time=virtual))
                    g.qmp.command('cont')
                channels=state(g,'serial-state')
                if peer.clock_if_ready(channels):
                    explicit_stop=True
                    continue
                spi8.clock_if_ready(channels[8]);spi9.clock_if_ready(channels[9])
                time.sleep(.002)
            g.qmp.command('stop');peer.close()
            g.data['final_pipeline']=dict(raw=state(g,'raw-pipeline-state'),lcd=state(g,'engine1548-scanout-state'))
            if acceptance and not acceptance.done:
                raise AssertionError('Run ended before three original Z8 MENU round trips passed')
            if args.require_liveview and not g.data['liveview_verified']:
                raise AssertionError('Original Z8 liveview has not passed the independent pixel check')
        session.progress('Stopped')
    except InterruptedError:
        session.progress('Stopped during startup')
    except Exception as exc:
        traceback.print_exc()
        session.status=dict(session.status,phase='Error; see local run log',error=type(exc).__name__)
    finally:
        session.stop.set()


class PreparedGuest(Guest):
    def __enter__(self):
        try:
            attach(self,native_sampling=True,battery_limits=True)
        except BaseException as exc:
            self.__exit__(type(exc),exc,None)
            raise
        return super().__enter__()


Guest=PreparedGuest


def handler(session):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self,*args):pass

        def allowed_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',
                                                f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.allowed_host():self.send_error(403);return
            route=self.path.split('?',1)[0]
            if route=='/':data=(ROOT/'tools/z8-interactive.html').read_bytes();kind='text/html; charset=utf-8'
            elif route=='/app.js':data=(ROOT/'tools/z8-interactive.js').read_bytes();kind='text/javascript'
            elif route=='/status':data=json.dumps(session.status).encode();kind='application/json'
            elif route=='/frame':data=session.png;kind='image/png'
            else:self.send_error(404);return
            self.send_response(200)
            for name,value in {'Content-Type':kind,'Content-Length':str(len(data)),'Cache-Control':'no-store',
                               'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
                               'Content-Security-Policy':"default-src 'self'; img-src 'self' blob:; style-src 'unsafe-inline'; frame-ancestors 'none'"}.items():
                self.send_header(name,value)
            self.end_headers();self.wfile.write(data)

        def do_POST(self):
            port=self.server.server_port
            if (not self.allowed_host() or self.headers.get('Origin') not in
                    (f'http://127.0.0.1:{port}',f'http://localhost:{port}')):
                self.send_error(403);return
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length','0')!='0':
                self.send_error(400);return
            if self.path=='/stop':
                session.stop.set()
                self.send_response(204);self.end_headers();return
            command=self.path.removeprefix('/key/')
            if self.path!='/key/'+command or command not in ('menu','half-down','half-up'):
                self.send_error(400);return
            if session.stop.is_set():self.send_error(409);return
            try:session.commands.put_nowait(command)
            except queue.Full:self.send_error(429);return
            self.send_response(204);self.end_headers()
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware',type=Path,required=True)
    parser.add_argument('--qemu',type=Path,required=True)
    parser.add_argument('--raw',type=Path,default=ROOT/'.local/input.raw')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8788)
    parser.add_argument('--seconds',type=float,default=0)
    parser.add_argument('--headless',action='store_true')
    parser.add_argument('--require-liveview',action='store_true')
    parser.add_argument('--menu-test',action='store_true')
    args=parser.parse_args()
    if not 1<=args.port<=65535 or not 0<=args.seconds<=3600:
        parser.error('Invalid port or duration')
    session=Session()
    if args.headless:
        worker(args,session)
    else:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(session))
        server.timeout=.25
        # Keep the process alive until the Guest owner has closed its QEMU.
        # Native boot observers check cancel_event at their polling boundaries.
        task=threading.Thread(target=worker,args=(args,session),daemon=False)
        task.start()
        print(f'http://127.0.0.1:{server.server_port}/',flush=True)
        try:
            while not session.stop.is_set():server.handle_request()
        except KeyboardInterrupt:pass
        finally:
            session.stop.set();server.server_close();task.join()
    if 'error' in session.status:raise SystemExit(1)


if __name__=='__main__':main()
