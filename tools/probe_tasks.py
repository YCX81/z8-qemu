# SPDX-License-Identifier: GPL-2.0-or-later
import time

def _check_cancelled(g):
    event=getattr(g,'cancel_event',None)
    if event is not None and event.is_set():
        raise InterruptedError('Guest run cancelled')

def settle(g,seconds):
    _check_cancelled(g)
    g.qmp.command('cont')
    deadline=time.monotonic()+seconds
    try:
        while time.monotonic()<deadline:
            _check_cancelled(g)
            if not g.qmp.command('query-status')['running']:
                _check_cancelled(g)
                early=getattr(g,'early_shutdown_observer',None)
                if 'Z9_UNKNOWN' in g.log() or not early or not early.handle():
                    break
                _check_cancelled(g)
                g.qmp.command('cont')
            time.sleep(.05)
        _check_cancelled(g)
    except InterruptedError:
        g.qmp.command('stop')
        raise
    g.qmp.command('stop')
