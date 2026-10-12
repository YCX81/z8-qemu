# SPDX-License-Identifier: GPL-2.0-or-later
"""Local UI boundaries and acceptance semantics; no firmware required."""
import http.client
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_run import Session,handler,liveview_ready,worker,menu_profile
from validate_run import MenuAcceptance,MENU_RGB_SHA256
from z8_properties import load_model_profile


class RuntimeContract(unittest.TestCase):
    def test_menu_profile_is_bound_to_z8_main(self):
        from z8_resources import MAIN_SHA
        profile=menu_profile(SimpleNamespace(data={'firmware_sha256':MAIN_SHA}))
        self.assertEqual(profile['model'],'Nikon Z8')
        self.assertEqual((profile['width'],profile['height']),(1024,682))
        with self.assertRaises(ValueError):menu_profile(SimpleNamespace(data={'firmware_sha256':'0'*64}))
    def test_menu_fingerprints_are_per_session_and_preserve_z9_default(self):
        z8=MenuAcceptance(SimpleNamespace(data={}),None,lambda message:None,menu_rgb_sha256='a'*64)
        z9=MenuAcceptance(SimpleNamespace(data={}),None,lambda message:None)
        self.assertEqual(z8.menu_digest,'a'*64)
        self.assertEqual(z9.menu_digest,MENU_RGB_SHA256)
        with self.assertRaises(ValueError):
            MenuAcceptance(SimpleNamespace(data={}),None,lambda message:None,menu_rgb_sha256='invalid')

    def test_stop_during_boot_exits_guest_owner_before_worker_finishes(self):
        session=Session()
        entered=threading.Event()
        closed=threading.Event()
        supplied = []
        class Owner:
            def __init__(self,*args,**kwargs):supplied.extend(kwargs['properties'])
            def __enter__(self):return self
            def __exit__(self,*args):closed.set()
        def pending_boot(guest,progress):
            entered.set()
            self.assertTrue(guest.cancel_event.wait(timeout=2))
            raise InterruptedError('test cancellation during native boot wait')
        args=SimpleNamespace(firmware='unused',qemu='unused',raw='unused',output='unused')
        with patch('z8_run.Guest',Owner),patch('z8_run.boot',pending_boot):
            task=threading.Thread(target=worker,args=(args,session))
            task.start()
            self.assertTrue(entered.wait(timeout=1))
            session.stop.set();task.join(timeout=2)
            self.assertFalse(task.is_alive())
            self.assertTrue(closed.is_set())
            self.assertNotIn('error',session.status)
            self.assertEqual(supplied, load_model_profile()['properties'])

    def test_raw_or_black_lcd_does_not_establish_liveview(self):
        raw={'frames':10}
        lcd={'frames':100,'video_frames':0,'last_frame_has_video':False,'active':True}
        self.assertFalse(liveview_ready(raw,lcd))
        lcd.update(video_frames=10,last_frame_has_video=True)
        self.assertTrue(liveview_ready(raw,lcd))
        lcd['active']=False
        self.assertFalse(liveview_ready(raw,lcd))

    def test_http_enforces_local_host_origin_and_command_allowlist(self):
        session=Session()
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(session))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        port=server.server_port
        def request(method,path,**headers):
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
            try:
                conn.request(method,path,headers=headers)
                response=conn.getresponse();response.read()
                return response.status
            finally:conn.close()
        try:
            self.assertEqual(request('GET','/status',Host='evil.test'),403)
            self.assertEqual(request('GET','/status'),200)
            self.assertEqual(request('POST','/key/menu'),403)
            self.assertEqual(request('POST','/key/menu',Origin='https://evil.test'),403)
            origin=f'http://127.0.0.1:{port}'
            self.assertEqual(request('POST','/key/arbitrary',Origin=origin),400)
            self.assertTrue(session.commands.empty())
            self.assertEqual(request('POST','/key/menu',Origin=origin),204)
            self.assertEqual(session.commands.get_nowait(),'menu')
            self.assertEqual(request('POST','/stop',Origin='https://evil.test'),403)
            self.assertFalse(session.stop.is_set())
            self.assertEqual(request('POST','/stop',Origin=origin),204)
            self.assertTrue(session.stop.is_set())
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)


if __name__=='__main__':unittest.main()
