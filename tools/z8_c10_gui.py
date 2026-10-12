# SPDX-License-Identifier: GPL-2.0-or-later
"""C3.11 native GUI diagnostics and observed simulation MCU menu keys.

The native-manager routines can construct an offscreen graph: a matching
graph alone never certifies a displayed page or visible glyphs. Captures are
unchanged QEMU screen pixels. The key builder returns a body for the owner's
MCU peer; this module does not send it. The owner serializes all guest access.
"""
import hashlib
import re
import struct
from pathlib import Path

from z8_native_call import MAIN_SHA
from z8_picture_control_menu import LABELS, SELECTORS, CELLS, CELLS_ALT, STATE, C10_LABEL_IDS

# Pinned static evidence: 43a84518 has 224 records of 24 bytes; its key 0x5f
# record at 43a84e00 names 40453258. FAMILY_INIT allocates native controls and
# dispatches that record; key 0xbd instead calls 40459fc0, which chooses mode
# from native setting 0300122c. Calling the row constructor cannot render.
CURRENT_INDEX, INDEX_KEY = 0x404D9BF8, 0x404DAA5C
FAMILY_INIT, PAGE_INIT, REFRESH = 0x404508B8, 0x40453258, 0x404A5A84
# This is a different domain from the 224 page types above: the three native
# index-map constructor records at 42b02880/28a8/28d0 all have count 12 at +10;
# 404d89dc populates that many u32s and 404d8d90 consumes exactly 0x30 bytes.
INDEX_SLOTS = 12
POOL, ACTIVE_LIST = 0xA032E6BC0, 0xA032F6940
LIST_OBJECT, CONFIG = 0xA032E5E88, 0xA032E6478
FONT_CALLBACKS = (0xA02F2A598, 0xA02F2A5A0)
TILES, SELECT_TILE = 0xA032F84E0, 0x404D8DC0
PHOTO = dict(enter=0x4048A6C8, switch=0x4048A618, family_init=0x4048C44C,
             parent=0xA02840630, family=0xA02840634, pool=0xA032F5778,
             object=0xA032F4F60, config=0xA032F5B10, cells=CELLS_ALT)
PRIMARY = dict(enter=0x4044DC88, switch=0x4044EBF8, family_init=FAMILY_INIT,
               parent=0xA0283B55C, family=0xA0283B558, pool=POOL,
               object=LIST_OBJECT, config=CONFIG, cells=CELLS)
MCU_MENU_KEYS = {'right': 0x22, 'left': 0x23, 'up': 0x24, 'down': 0x25,
                 'ok': 0x2a, 'menu': 0x43}


def menu_key_body(key=None):
    """C3.11 menu meanings observed through original MCU frames and pixels.

    None releases all menu keys and half-shutter using the existing frame
    baseline. Diagonal fields are deliberately not named without evidence.
    This is a simulation binding, not physical wire-protocol verification.
    """
    if key is not None and (not isinstance(key, str) or key not in MCU_MENU_KEYS):
        raise ValueError('Use one of the six observed C3.11 menu keys, or release')
    body = bytearray(124)
    body[0] = body[4] = 1
    if key is not None:
        body[MCU_MENU_KEYS[key]] = 1
    return bytes(body)


def _paused(guest):
    if guest.data.get('firmware_sha256') != MAIN_SHA:
        raise ValueError('GUI diagnostics require the pinned C3.11 source identity')
    if guest.qmp.command('query-status')['running']:
        raise RuntimeError('GUI diagnostics require an already paused owned guest')
    if 'Z9_UNKNOWN' in guest.log():
        raise RuntimeError('Unknown MMIO prevents GUI acceptance')


def _read(guest, address, fmt):
    raw = guest.read_bytes(address, struct.calcsize(fmt))
    if len(raw) != struct.calcsize(fmt):
        raise RuntimeError('Short native GUI memory read')
    return struct.unpack(fmt, raw)


def inspect_picture_control(session):
    """Inspect the native graph; only the two original getters are invoked."""
    guest = session.g
    _paused(guest)
    index = session.call(CURRENT_INDEX)
    # INDEX_KEY itself is unchecked; use the verified constructed slot domain.
    if not isinstance(index, int) or not 0 <= index < INDEX_SLOTS:
        raise RuntimeError('Current menu index outside the inspected GUI range')
    key = session.call(INDEX_KEY, index)
    graph = PHOTO if key == 0x10 else PRIMARY
    callbacks = [_read(guest, address, '<Q')[0] for address in FONT_CALLBACKS]
    mode, custom_count = _read(guest, STATE, '<II')
    total, step, focused = _read(guest, graph['config'], '<III')
    labels, cells = _read(guest, graph['config'] + 0x18, '<QQ')
    active = _read(guest, ACTIVE_LIST, '<Q')[0]
    config = _read(guest, graph['object'] + 0x20, '<Q')[0]
    pool = _read(guest, graph['pool'], '<Q')[0]
    selectors = list(_read(guest, SELECTORS, f'<{custom_count}I')) if custom_count <= 10 else []
    first_selector, c10_selector = (31, 40) if mode == 0 else (81, 90)
    rows = [31 + i for i, selector in enumerate(selectors) if selector == c10_selector]
    c10_row = rows[0] if len(rows) == 1 else None
    label_id = _read(guest, LABELS + c10_row * 2, '<H')[0] if c10_row is not None else None
    return dict(page_index=index, page_key=key, font_callbacks=callbacks,
                mode=mode, custom_count=custom_count, total_rows=total,
                focused_row=focused, c10_row=c10_row, c10_label_id=label_id,
                native_family_init=graph['family_init'],
                selectors=selectors, control_pool=pool, active_list=active,
                labels_pointer=labels, cells_pointer=cells,
                native_graph_matches=(active == graph['object'] and config == graph['config']
                    and labels == LABELS and cells == graph['cells'] and mode in (0, 1)
                    and 1 <= custom_count <= 10 and total == 31 + custom_count
                    and step == 1 and len(set(selectors)) == len(selectors)
                    and all(first_selector <= value <= c10_selector for value in selectors)
                    and c10_row is not None and label_id in C10_LABEL_IDS))


def ppm_dimensions(raw):
    """Check a complete QEMU P6 frame, without consuming binary whitespace."""
    match = re.match(rb'P6\s+(?:#[^\r\n]*\r?\n\s*)*(\d+)\s+(\d+)\s+255\r?\n', raw)
    if match is None:
        raise ValueError('Expected QEMU P6 screen capture')
    width, height = (int(value) for value in match.groups())
    if not 0 < width <= 16384 or not 0 < height <= 16384 or len(raw) - match.end() != width * height * 3:
        raise ValueError('Truncated or invalid QEMU screen capture')
    return width, height


def capture_picture_control(session, *, mode=0, rebuild=False, settle_seconds=2.0,
                            filename='c10-native-gui.ppm'):
    """Capture a focused C10 native page; visible text remains a review task.

    Current key must be 0x5f (photo) or 0xbd (native photo/movie choice).
    rebuild calls the original family allocator, dispatching that page record.
    No menu-manager globals, current selectors or framebuffer pixels are set.
    """
    if (not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.ppm', filename)
            or type(mode) is not int or mode not in (0, 1)
            or not isinstance(settle_seconds, (int, float)) or not 0 < settle_seconds <= 30):
        raise ValueError('Use a bounded settle time and a plain owned PPM filename')
    guest = session.g
    target = Path(guest.out) / filename
    if target.exists():
        raise FileExistsError(target)
    state = inspect_picture_control(session)
    record = dict(classification='active_native_gui_diagnostic_not_keyboard_traversal',
                  before=state, expected_mode=mode, rebuild_requested=bool(rebuild),
                  whole_camera_menu_clicks=False, physical_camera_verified=False,
                  c10_glyph_visually_verified=False, native_gui_graph_verified=False)
    guest.data.setdefault('c10_native_gui', []).append(record)
    keys = (0x10, 0x5f, 0xbd) if mode == 0 else (0xbd,)
    if state['page_key'] not in keys:
        raise RuntimeError('Native menu manager is not on a Picture Control page for the requested mode')
    if any(not 0x40160000 < address < 0x48000000 or address % 4 for address in state['font_callbacks']):
        raise RuntimeError('Native font callbacks have not initialized')
    if rebuild:
        session.call(state['native_family_init'], timeout=30)
        state = inspect_picture_control(session)
        record['after_rebuild'] = state
    if (not state['native_graph_matches'] or state['mode'] != mode or not state['control_pool']
            or state['focused_row'] != state['c10_row']):
        raise RuntimeError('Native focused C10 list graph is not ready; inspect recorded state')
    session.call(REFRESH, timeout=30)
    session.run(settle_seconds)
    _paused(guest)
    after = inspect_picture_control(session)
    record['after_redraw'] = after
    if (after['page_key'] not in keys or after['mode'] != mode or not after['native_graph_matches']
            or after['focused_row'] != after['c10_row']):
        raise RuntimeError('Native C10 page changed during redraw')
    guest.qmp.command('screendump', filename=str(target))
    frame = target.read_bytes()
    dimensions = ppm_dimensions(frame)
    record.update(native_gui_graph_verified=True, capture=str(target),
                  sha256=hashlib.sha256(frame).hexdigest(), image_dimensions=list(dimensions))
    return record


def _navigation_profile(parent_page, mode):
    if (type(parent_page) is not int or parent_page not in (0, 0x4f, 0xa5)
            or type(mode) is not int or mode not in (0, 1)
            or parent_page == 0 and mode != 0):
        raise ValueError('Use a verified native parent page and Picture Control bank')
    return PHOTO if parent_page == 0 else PRIMARY


def navigate_picture_control(session, *, parent_page=0, mode=0):
    """Enter a native menu container, select its PC tile, then open its list.

    These are active original manager calls, not MCU/keyboard traversal. The
    actual configured twelve-tile map is read after the original parent entry;
    an absent PC tile is an error rather than a host-written menu assignment.
    Parent 0 uses the independent photo implementation; 4f/a5 use PRIMARY.
    """
    profile = _navigation_profile(parent_page, mode)
    g = session.g
    _paused(g)
    record = dict(classification='active_native_menu_manager_navigation',
                  parent_page=parent_page, expected_mode=mode,
                  whole_camera_menu_clicks=False, physical_camera_verified=False)
    g.data.setdefault('c10_gui_navigation', []).append(record)
    session.call(profile['enter'], parent_page, timeout=30)
    record['parent_observed'] = _read(g, profile['parent'], '<I')[0]
    if record['parent_observed'] != parent_page:
        raise RuntimeError('Original manager did not enter the requested parent page')
    tiles = list(_read(g, TILES, '<12I'))
    record['native_tiles'] = tiles
    allowed = (0x10,) if parent_page == 0 else ((0x5f, 0xbd) if mode == 0 else (0xbd,))
    candidates = [index for index, key in enumerate(tiles) if key in allowed]
    if not candidates:
        raise RuntimeError('Requested Picture Control tile is absent from the original native menu')
    tile = candidates[0]
    record.update(selected_tile=tile, selected_key=tiles[tile])
    session.call(SELECT_TILE, tile, timeout=30)
    actual = session.call(CURRENT_INDEX)
    if actual != tile or session.call(INDEX_KEY, actual) != tiles[tile]:
        raise RuntimeError('Original menu tile selection did not take effect')
    session.call(profile['switch'], 2, timeout=30)
    record['opened'] = inspect_picture_control(session)
    if (_read(g, profile['family'], '<I')[0] != 2
            or not record['opened']['native_graph_matches']
            or record['opened']['mode'] != mode):
        raise RuntimeError('Native Picture Control family did not build the expected list')
    return record


def return_to_parent(session, navigation):
    """Use the native family exit and verify the same parent tile is retained."""
    profile = _navigation_profile(navigation['parent_page'], navigation['expected_mode'])
    g = session.g
    _paused(g)
    if (_read(g, profile['parent'], '<I')[0] != navigation['parent_page']
            or _read(g, profile['family'], '<I')[0] != 2):
        raise RuntimeError('The owned navigation is no longer on its native child page')
    session.call(profile['switch'], 0, timeout=30)
    index = session.call(CURRENT_INDEX)
    retained = (_read(g, profile['family'], '<I')[0] == 0
                and index == navigation['selected_tile'])
    if not retained or session.call(INDEX_KEY, index) != navigation['selected_key']:
        raise RuntimeError('Native parent did not retain the selected Picture Control tile')
    record = dict(parent_tile_retained=True, selected_tile=index, whole_camera_menu_clicks=False)
    navigation['returned'] = record
    return record
