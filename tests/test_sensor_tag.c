/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "../qemu/nikon-sensor-tag.h"

static unsigned read_sample(const uint8_t *row, unsigned x, unsigned bits)
{
    unsigned result = 0;
    for (unsigned n = 0; n < bits; n++) {
        unsigned at = x * bits + n;
        result |= ((row[at / 8] >> (at % 8)) & 1u) << n;
    }
    return result;
}

int main(void)
{
    uint8_t bytes[16 + 128 * 16 + 16], before[sizeof(bytes)];
    for (unsigned bits = 12; bits <= 14; bits += 2) {
        memset(bytes, 0x5a, sizeof(bytes));
        for (unsigned n = 0; n < 3; n++) {
            uint32_t id = (uint32_t[]){1, 0x80000005, 0xffffffff}[n];
            bool ok = nikon_sensor_tag_apply(bytes + 16, 128 * 16,
                                             64, 16, 128, bits, id);
            assert(ok);
            for (unsigned y = 0; y < 16; y++) {
                for (unsigned x = 0; x < 64; x++) {
                    unsigned bit = ((id >> (x / 2)) & 1) ^ (y >= 8);
                    unsigned white = (1u << bits) - 1;
                    assert(read_sample(bytes + 16 + 128 * y, x, bits) ==
                           (bit ? white * 7 / 8 : white / 8));
                }
                for (unsigned x = 64 * bits / 8; x < 128; x++) {
                    assert(bytes[16 + 128 * y + x] == 0x5a);
                }
            }
            for (unsigned x = 0; x < 16; x++) {
                assert(bytes[x] == 0x5a && bytes[16 + 128 * 16 + x] == 0x5a);
            }
        }
    }
    memcpy(before, bytes, sizeof(bytes));
    const unsigned bad[][5] = {{63,16,128,14,1},{64,15,128,14,1},
        {64,16,111,14,1},{64,16,128,16,1},{64,16,128,14,0},
        {64,17,128,14,1},{16386,16,32768,14,1}};
    for (unsigned i = 0; i < sizeof(bad)/sizeof(bad[0]); i++) {
        bool ok = nikon_sensor_tag_apply(bytes + 16, 128 * 16,
            bad[i][0],bad[i][1],bad[i][2],bad[i][3],bad[i][4]);
        assert(!ok && memcmp(bytes,before,sizeof(bytes)) == 0);
    }
    bool ok = nikon_sensor_tag_apply(NULL, 0, 64,16,128,14,1);
    assert(!ok);
    return 0;
}
