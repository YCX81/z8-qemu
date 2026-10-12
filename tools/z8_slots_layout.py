# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed C64 storage ABI, selectable C32/C64 exposure; simulation only."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Layout:
    slots: int = 32
    capacity = 64
    banks = 2
    header_bytes = 128
    stride = 0x400
    data_base = 0xA1162B000
    reserved_bytes = 0x100000
    general_base = 0xA10C2B000
    general_bytes = 0xA00000
    record_bytes = 0x20800
    media_bases = (2044 * 64, 2046 * 64)
    payload_chunks = ((1, 63, 0), (64, 2, 63 * 2048))
    core_code = 0x45010000
    main_code = 0x45018000
    media_code = 0x45020000
    label_code = 0x45022000
    imenu_code = 0x45024000
    code_base = 0x45010000
    code_bytes = 0x20000

    def __post_init__(self):
        if type(self.slots) is not int or self.slots not in (32, 64):
            raise ValueError('Only the explicitly designed C32/C64 configurations are accepted')

    @property
    def regions(self):
        relative = {
            'groups': (0, 0x2000), 'items': (0x2000, 0x20000),
            'main_menu': (0x22000, 0x18000),
            'records': (0x40000, self.record_bytes), 'load': (0x61000, self.record_bytes),
            'cache': (0x82000, 128 * 0x248),
            'params': (0x95000, 0x1000), 'input': (0x96000, 0x1000),
            'parsed': (0x97000, 0x1000), 'file': (0x98000, 0x1000),
            'scratch': (0x99000, 0x1000),
            'list0': (0x9A000, 0x400), 'list1': (0x9A400, 0x400),
            'codes': (0x9A800, 0x100), 'keys': (0x9AA00, 0x600),
            'header': (0x9B000, 0x100), 'defaults': (0x9B100, 0x300),
            'imenu': (0x9C000, 0x8000), 'labels': (0xA4000, 0x4000),
            'media_header': (0xA8000, 0x800),
            'calls': (0xAF000, 0x1000), 'stack': (0xB0000, 0x10000),
        }
        return {key: (self.data_base + start, size) for key, (start, size) in relative.items()}

    def address(self, region):
        return self.regions[region][0]

    def validate(self, slot, bank, *, reserved=False):
        limit = self.capacity if reserved else self.slots
        if type(slot) is not int or not 1 <= slot <= limit or type(bank) is not int or bank not in (0, 1):
            raise ValueError('Slot/bank outside the configured namespace')

    def selector(self, slot, bank):
        self.validate(slot, bank)
        return 30 + 50 * bank + slot if slot <= 9 else 0x1000 * (bank + 1) + slot - 1

    def decode(self, selector):
        if type(selector) is not int:
            return None
        for bank in (0, 1):
            old = selector - (31 + 50 * bank)
            extended = selector - 0x1000 * (bank + 1)
            if 0 <= old < 9: return old + 1, bank
            if 9 <= extended < self.slots: return extended + 1, bank
        return None

    def code(self, slot):
        self.validate(slot, 0)
        return slot + (27 if slot <= 9 else 30)

    def record(self, slot, bank, *, reserved=False):
        self.validate(slot, bank, reserved=reserved)
        return self.address('records') + self.header_bytes + (bank * self.capacity + slot - 1) * self.stride

    def keys(self, slot, bank, *, reserved=False):
        self.validate(slot, bank, reserved=reserved)
        if slot <= 9:
            return ((0x01000D08 if bank == 0 else 0x01001408) + slot - 1,
                    0xF00 + bank * 9 + slot - 1,
                    (0x01000D14 if bank == 0 else 0x01001414) + slot - 1)
        ordinal = (slot - 10) * 6 + bank * 3
        return tuple((0x01000000 if part != 1 else 0) |
                     ((0x7C + (ordinal + part) // 256) << 8) | ((ordinal + part) % 256)
                     for part in range(3))

    def row_key(self, slot, bank):
        self.validate(slot, bank)
        if slot < 10: raise ValueError('Original rows retain their original keys')
        return 0x7800 + bank * 0x100 + slot

    def label_id(self, slot, variant=0):
        self.validate(slot, 0)
        if slot < 10 or type(variant) is not int or not 0 <= variant < 5:
            raise ValueError('Only extension labels have an owned ID')
        return 0x7E00 + (slot - 10) * 5 + variant
