# SPDX-License-Identifier: GPL-2.0-or-later
"""Read original Z8 input gates and primary subscriptions without clearing them.

Only numeric state is returned. A set/clear gate is a local branch condition,
not proof that the whole camera is ready. No writes, key inputs or IRQ ACKs.
"""
from z8_capture_bindings import load_bindings

# Every scalar is anchored by a C3.11 load/store in the associated observer
# profile. Names describe consumers rather than unproved physical meanings.
SCALARS = {
    'key_dispatch_ready': (0xa0321e4b9, 1),
    'key_dispatch_inhibit': (0xa0321e80c, 1),
    'key_dispatch_deferred': (0xa0321e820, 1),
    'full_worker_permission': (0xa03bd042c, 1),
    'gpio_half_level': (0xa03bd055c, 4),
    'gpio_full_level': (0xa03bd0560, 4),
    'full_worker_hold': (0xa03bd060c, 4),
    'gpio_full_worker_hold': (0xa03bd0614, 4),
    'full_worker_pending': (0xa03bd0618, 4),
    'primary_software_mask0': (0xa02f23804, 4),
    'primary_software_mask1': (0xa02f23808, 4),
    'primary_driver_callback': (0xa0280d6b8, 8),
    'processing_resource_base': (0xa0496f6a0, 8),
}
PRIMARY_OFFSETS = (0x18, 0x200, 0x204, 0x208, 0x20c, 0x210, 0x214, 0x218, 0x21c, 0x220)


def capture(guest, *, primary_mmio=False):
    profile = load_bindings(guest)
    def read(address, size):
        data = guest.read_bytes(address, size)
        if len(data) != size:
            raise ValueError('Short original Z8 readiness read')
        return data
    scalars = {name: dict(address=address, bytes=size,
                         value=int.from_bytes(read(address, size), 'little'))
               for name, (address, size) in SCALARS.items()}
    key_data = read(0xa0320169c, 44)
    key_words = [int.from_bytes(key_data[offset:offset + 4], 'little') for offset in range(0, 44, 4)]
    mmio = {hex(offset): guest.rd(0x150c0000 + offset) for offset in PRIMARY_OFFSETS} if primary_mmio else {}
    values = {name: row['value'] for name, row in scalars.items()}
    derived = dict(
        key_dispatch_ready_bit0=bool(values['key_dispatch_ready'] & 1),
        key_dispatch_inhibited=values['key_dispatch_inhibit'] != 0,
        full_worker_hold_union=values['full_worker_hold'] | values['full_worker_pending'],
        gpio_full_worker_held=values['gpio_full_worker_hold'] != 0,
        d980_software_subscribed=bool(values['primary_software_mask1'] & 0x04000000),
        d980_pending=bool(mmio['0x214'] & 0x04000000) if primary_mmio else None,
        d980_enabled_pending=bool(mmio['0x210'] & 0x04000000) if primary_mmio else None,
        d980_irq_enabled=bool(mmio['0x20c'] & 0x04000000) if primary_mmio else None)
    return dict(main_sha256=profile['main_sha256'], scalars=scalars,
                key_latch=dict(address=0xa0320169c, words=key_words),
                primary_mmio=mmio, derived=derived,
                guest_state_modified=False, camera_ready_verified=False,
                native_capture_verified=False,
                scope='Passive original branch inputs and subscriptions; no readiness, full boot or saved-image acceptance follows from a scalar alone')
