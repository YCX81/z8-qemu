# SPDX-License-Identifier: GPL-2.0-or-later
"""Real MCU main-menu traversal with native getter checks and raw captures.

This is separate from the diagnostic iMenu graph. It assumes the verified
C3.11 startup/menu layout and a restored photo C10 current selection. Timing
is explicitly host wall-clock pacing through Session.run, not a measurement
of the guest's virtual or physical key duration. Pixels require human review.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import time

from z8_c10_gui import MAIN_SHA, menu_key_body, ppm_dimensions
from z8_c10_memory import DATA_BASE

CURRENT_GETTER, SETTING_GETTER = 0x410A7B58, 0x410DF224
SELECTOR_OUTPUT, PARAMETER_OUTPUT = DATA_BASE + 0x3C000, DATA_BASE + 0x3C010
PHOTO_SETTING_KEY = 0x07000633


def run_menu_acceptance(session, *, expected_menu_rgb_sha256, output=None,
                        hold_wall_seconds=0.5, release_wall_seconds=3.0,
                        overall_wall_seconds=300.0):
    """Traverse photo C10 -> C9 -> C10 using original MCU press/release frames.

The owner must serialize guest access and terminate its owned guest after a
failure. No manager setters, host-rendered labels, or direct state writes are
used. Original getters only write their output into the reserved lab scratch.
Returned selection verification never certifies a glyph or highlight.
"""
    for value, lower, upper in ((hold_wall_seconds, 0.1, 2.0),
                                (release_wall_seconds, 0.5, 10.0),
                                (overall_wall_seconds, 1.0, 900.0)):
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not lower <= value <= upper):
            raise ValueError('Use finite bounded wall-clock pacing and deadline values')
    if (not isinstance(expected_menu_rgb_sha256, str)
            or re.fullmatch('[0-9a-f]{64}', expected_menu_rgb_sha256) is None):
        raise ValueError('The independently verified MENU RGB SHA256 is required')
    guest = session.g
    if guest.data.get('firmware_sha256') != MAIN_SHA:
        raise ValueError('Main-menu acceptance requires the pinned C3.11 source')
    if guest.qmp.command('query-status')['running'] or 'Z9_UNKNOWN' in guest.log():
        raise RuntimeError('Main-menu acceptance requires a paused healthy owned guest')
    directory = Path(output) if output is not None else Path(guest.out) / 'main-menu-gui'
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + overall_wall_seconds
    result = dict(status='running', simulation_only=True, physical_camera_verified=False,
                  classification='original_MCU_main_menu_traversal',
                  clock_basis='Session.run wall-clock pacing; guest virtual duration is not asserted',
                  mcu_selection_verified=False, c10_glyph_visually_verified=False,
                  c10_highlight_visually_verified=False, frames=[], captures=[], selections=[])
    guest.data['c10_main_menu_acceptance'] = result

    def bounded():
        if time.monotonic() >= deadline:
            raise TimeoutError('Main-menu acceptance wall-clock deadline exceeded')
        if guest.qmp.command('query-status')['running'] or 'Z9_UNKNOWN' in guest.log():
            raise RuntimeError('Menu step requires a paused guest without unknown hardware')

    def send(body, wall_seconds, *, cleanup=False):
        if not cleanup:
            bounded()
        first = len(session.peer.record['rx'])
        session.peer.enqueue_external(body, 2)
        session.run(wall_seconds)
        rows = session.peer.record['rx'][first:]
        matches = [row for row in rows if row.get('family') == 2
                   and row.get('body_hex') == body.hex() and row.get('complete') is True]
        if len(matches) != 1:
            raise RuntimeError('Expected exactly one completed MCU press/release frame')
        result['frames'].append(dict(body_hex=body.hex(), family=2,
                                    rx_index=first + rows.index(matches[0]),
                                    wall_pacing_seconds=wall_seconds))

    def press(key, *, release_seconds=None):
        body = menu_key_body(key)
        failure = None
        try:
            send(body, hold_wall_seconds)
        except BaseException as error:
            failure = error
            raise
        finally:
            # Release is attempted on the healthy owned peer even after a
            # transport/deadline failure. Never resume an unknown device path.
            if 'Z9_UNKNOWN' not in guest.log():
                try:
                    send(menu_key_body(), release_wall_seconds if release_seconds is None
                         else release_seconds, cleanup=True)
                except BaseException as release_error:
                    result['release_error'] = repr(release_error)
                    if failure is None:
                        raise

    def capture(name):
        bounded()
        target = directory / f'{name}.ppm'
        guest.qmp.command('screendump', filename=str(target))
        raw = target.read_bytes()
        width, height = ppm_dimensions(raw)
        rgb = raw[-width * height * 3:]
        row = dict(path=str(target), width=width, height=height,
                   sha256=hashlib.sha256(raw).hexdigest(), rgb_sha256=hashlib.sha256(rgb).hexdigest(),
                   host_pixel_edits=False)
        result['captures'].append(row)
        return row

    def verify_current(expected, phase):
        bounded()
        remaining = max(0.1, min(10.0, deadline - time.monotonic()))
        if session.call(CURRENT_GETTER, SELECTOR_OUTPUT, PARAMETER_OUTPUT, timeout=remaining) != 1:
            raise RuntimeError('Original Picture Control current getter failed')
        raw = guest.read_bytes(SELECTOR_OUTPUT, 4)
        if len(raw) != 4:
            raise RuntimeError('Short original selector getter output')
        selector = struct.unpack('<I', raw)[0]
        bounded()
        code = session.call(SETTING_GETTER, PHOTO_SETTING_KEY,
                            timeout=max(0.1, min(10.0, deadline - time.monotonic())))
        result['selections'].append(dict(phase=phase, selector=selector, property_code=code))
        if selector != expected or code != (36 if expected == 39 else 40):
            raise RuntimeError(f'Original selector/property mismatch after {phase}: {selector}/{code}')

    try:
        verify_current(40, 'restored_c10')
        send(menu_key_body(), 2.0)
        press('menu', release_seconds=10.0)
        if capture('00-menu')['rgb_sha256'] != expected_menu_rgb_sha256:
            raise RuntimeError('Actual MENU baseline differs; fixed down13 route is unsafe')
        for _ in range(13):
            press('down')
        press('right')
        capture('01-initial-c10')
        press('up')
        capture('02-c9-focused')
        press('ok')
        verify_current(39, 'MCU_C9_OK')
        capture('03-c9-selected')
        press('right')
        press('down')
        capture('04-c10-focused')
        press('ok')
        verify_current(40, 'MCU_C10_OK')
        capture('05-c10-selected')
        press('right')
        verify_current(40, 'C10_page_reopened')
        capture('06-c10-reopened')
        result.update(status='selection_verified_visual_review_required', mcu_selection_verified=True)
        return result
    except BaseException as error:
        result.update(status='failed', error=repr(error))
        raise
    finally:
        result['elapsed_wall_seconds'] = time.monotonic() - started
        (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
